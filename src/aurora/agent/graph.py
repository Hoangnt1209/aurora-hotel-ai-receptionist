"""Compiled LangGraph stateful receptionist workflow with verification & confirmation gates."""

import re
from datetime import date, datetime
from decimal import Decimal
from typing import Any, TypedDict
from uuid import UUID, uuid4

from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aurora.agent.llm_provider import LLMClient
from aurora.agent.tools import (
    cancel_reservation_tool,
    check_availability_tool,
    create_reservation_tool,
    create_support_ticket_tool,
    escalate_to_human_tool,
    get_reservation_details_tool,
    modify_reservation_tool,
    search_hotel_policy_tool,
    verify_guest_reservation_tool,
)
from aurora.db.models import Conversation, ConversationMessage, Property
from aurora.domain.enums import (
    EscalationPriority,
    TicketCategory,
    TicketPriority,
)
from aurora.domain.schemas import (
    CancelReservationRequest,
    CreateReservationRequest,
    ModifyReservationRequest,
)


class AgentTurnResponse(BaseModel):
    conversation_id: UUID
    reply: str
    language: str
    intent: str
    property_code: str | None = None
    requires_confirmation: bool = False
    pending_action: dict[str, Any] | None = None
    verified_codes: list[str] = Field(default_factory=list)
    tool_traces: list[dict[str, Any]] = Field(default_factory=list)
    citations: list[dict[str, Any]] = Field(default_factory=list)
    escalation_code: str | None = None
    ai_provider_used: str = "deterministic_fallback"
    trace_id: str


class ReceptionistGraphState(TypedDict, total=False):
    conversation_id: str
    channel: str
    trace_id: str
    user_message: str
    language: str
    intent: str
    is_injection: bool
    is_confirmation: bool
    is_decline: bool
    slots: dict[str, Any]
    verified_tokens: dict[str, str]
    pending_action: dict[str, Any] | None
    gate_decision: str
    tool_traces: list[dict[str, Any]]
    citations: list[dict[str, Any]]
    escalation_code: str | None
    ai_provider_used: str
    reply: str
    requires_confirmation: bool


def _format_vnd(amount: Any) -> str:
    try:
        return f"{int(Decimal(str(amount))):,} VND"
    except Exception:
        return f"{amount} VND"


def _verify_no_hallucinated_codes(
    reply: str,
    tool_traces: list[dict[str, Any]],
    user_message: str,
    verified_codes: list[str],
) -> str:
    """Guardrail ensuring no confirmation/ticket/escalation code is fabricated."""
    mentioned = set(re.findall(r"\b(?:AUR|TCK|ESC)-[A-Z0-9]{2,4}-[A-Z0-9]{4,8}\b", reply))
    if not mentioned:
        return reply

    allowed = set(verified_codes)
    allowed.update(re.findall(r"\b(?:AUR|TCK|ESC)-[A-Z0-9]{2,4}-[A-Z0-9]{4,8}\b", user_message.upper()))
    for trace in tool_traces:
        data = trace.get("data") or {}
        if isinstance(data, dict):
            for key in ("confirmation_code", "ticket_code", "escalation_code"):
                if data.get(key):
                    allowed.add(str(data[key]))

    unverified = mentioned - allowed
    if unverified:
        return (
            "I could not verify a valid reference code from our reservation system. "
            "Let me connect you with our front-desk team so we can help safely."
        )
    return reply


class ReceptionistAgentService:
    """Orchestrates multi-turn guest conversations via compiled LangGraph + PostgreSQL."""

    def __init__(
        self,
        session: AsyncSession,
        ai_provider: str | None = None,
        ai_api_key: str | None = None,
        http_transport: Any = None,
    ) -> None:
        self.session = session
        self.llm_client = LLMClient(
            provider=ai_provider,
            api_key=ai_api_key,
            http_transport=http_transport,
        )
        self.graph = self._build_graph()

    def _build_graph(self):
        workflow = StateGraph(ReceptionistGraphState)
        workflow.add_node("nlu_router", self._nlu_router_node)
        workflow.add_node("safety_gate", self._safety_gate_node)
        workflow.add_node("tool_executor", self._tool_executor_node)
        workflow.add_node("grounded_responder", self._grounded_responder_node)

        workflow.add_edge(START, "nlu_router")
        workflow.add_edge("nlu_router", "safety_gate")
        workflow.add_edge("safety_gate", "tool_executor")
        workflow.add_edge("tool_executor", "grounded_responder")
        workflow.add_edge("grounded_responder", END)
        return workflow.compile()

    async def _nlu_router_node(
        self, state: ReceptionistGraphState
    ) -> dict[str, Any]:
        existing_slots = dict(state.get("slots") or {})
        pending = state.get("pending_action")
        parsed, provider_used = await self.llm_client.extract_turn_nlu(
            state["user_message"],
            existing_slots=existing_slots,
            has_pending_action=bool(pending),
        )
        merged_slots = {**existing_slots, **parsed.slots}
        return {
            "language": parsed.language,
            "intent": parsed.intent,
            "is_injection": parsed.is_injection,
            "is_confirmation": parsed.is_confirmation,
            "is_decline": parsed.is_decline,
            "slots": merged_slots,
            "ai_provider_used": provider_used,
        }

    async def _safety_gate_node(
        self, state: ReceptionistGraphState
    ) -> dict[str, Any]:
        intent = state["intent"]
        slots = dict(state.get("slots") or {})
        verified_tokens = dict(state.get("verified_tokens") or {})
        pending = state.get("pending_action")

        if state.get("is_injection") or intent == "PROMPT_INJECTION":
            return {"gate_decision": "BLOCK_INJECTION"}

        if int(slots.get("rooms_count", 1)) > 1:
            return {"gate_decision": "AUTO_ESCALATE_GROUP"}

        if intent == "ESCALATE":
            return {"gate_decision": "ESCALATE_REQUESTED"}

        if intent == "DECLINE_ACTION" and pending:
            return {"gate_decision": "CLEAR_PENDING", "pending_action": None}

        if intent == "CONFIRM_ACTION" and pending:
            action_type = pending.get("type")
            if action_type == "CREATE_RESERVATION":
                return {"gate_decision": "EXECUTE_CREATE_RESERVATION"}
            if action_type == "MODIFY_RESERVATION":
                return {"gate_decision": "EXECUTE_MODIFY_RESERVATION"}
            if action_type == "CANCEL_RESERVATION":
                return {"gate_decision": "EXECUTE_CANCEL_RESERVATION"}

        # Verification Gate for viewing/modifying/cancelling existing reservations
        if intent in {"VIEW_BOOKING", "MODIFY_BOOKING", "CANCEL_BOOKING"}:
            code = slots.get("confirmation_code")
            if not code:
                return {"gate_decision": "NEED_CONFIRMATION_CODE"}

            if code not in verified_tokens:
                if slots.get("guest_phone") or slots.get("guest_email"):
                    return {"gate_decision": "VERIFY_THEN_STAGE"}
                slots["pending_verification_for"] = intent
                return {
                    "gate_decision": "NEED_VERIFICATION_CONTACT",
                    "slots": slots,
                }

            # Already verified -> route cleanly
            if intent == "VIEW_BOOKING":
                return {"gate_decision": "EXECUTE_VIEW_BOOKING"}
            if intent == "CANCEL_BOOKING":
                if state.get("is_confirmation") and pending and pending.get("type") == "CANCEL_RESERVATION":
                    return {"gate_decision": "EXECUTE_CANCEL_RESERVATION"}
                return {"gate_decision": "STAGE_CANCEL_CONFIRMATION"}
            if intent == "MODIFY_BOOKING":
                if not (slots.get("check_in_date") and slots.get("check_out_date")) and not slots.get("new_special_requests"):
                    return {"gate_decision": "NEED_MODIFICATION_DETAILS"}
                return {"gate_decision": "STAGE_MODIFY_CONFIRMATION"}

        if intent == "VERIFY_BOOKING":
            code = slots.get("confirmation_code")
            if code and (slots.get("guest_phone") or slots.get("guest_email")):
                return {"gate_decision": "VERIFY_THEN_STAGE"}
            return {"gate_decision": "NEED_VERIFICATION_CONTACT"}

        if intent == "BOOK_ROOM":
            missing = []
            if not slots.get("property_code"):
                missing.append("property_code")
            if not (slots.get("check_in_date") and slots.get("check_out_date")):
                missing.append("dates")
            if missing:
                return {"gate_decision": "NEED_BOOKING_SLOTS"}
            return {"gate_decision": "STAGE_OR_COLLECT_GUEST_FOR_BOOKING"}

        if intent == "CHECK_AVAILABILITY":
            if not slots.get("property_code") or not (
                slots.get("check_in_date") and slots.get("check_out_date")
            ):
                return {"gate_decision": "NEED_AVAILABILITY_SLOTS"}
            return {"gate_decision": "EXECUTE_CHECK_AVAILABILITY"}

        if intent == "ASK_POLICY":
            return {"gate_decision": "EXECUTE_POLICY_RAG"}

        if intent == "CREATE_TICKET":
            if not slots.get("property_code"):
                return {"gate_decision": "NEED_PROPERTY_FOR_TICKET"}
            return {"gate_decision": "EXECUTE_CREATE_TICKET"}

        return {"gate_decision": "GREETING"}

    async def _tool_executor_node(
        self, state: ReceptionistGraphState
    ) -> dict[str, Any]:
        decision = state["gate_decision"]
        slots = dict(state.get("slots") or {})
        verified_tokens = dict(state.get("verified_tokens") or {})
        pending = state.get("pending_action")
        traces: list[dict[str, Any]] = []
        citations: list[dict[str, Any]] = []
        trace_id = state["trace_id"]
        conv_id = UUID(state["conversation_id"])
        esc_code: str | None = state.get("escalation_code")

        if decision in {"AUTO_ESCALATE_GROUP", "ESCALATE_REQUESTED"}:
            reason = (
                "GROUP_BOOKING_OVER_1_ROOM"
                if decision == "AUTO_ESCALATE_GROUP"
                else "GUEST_REQUESTED_HUMAN_AGENT"
            )
            esc_res = await escalate_to_human_tool(
                self.session,
                conversation_id=conv_id,
                reason_code=reason,
                summary=f"Escalated from {state.get('channel')}: {state['user_message'][:400]}",
                property_code=slots.get("property_code"),
                confirmation_code=slots.get("confirmation_code"),
                priority=EscalationPriority.HIGH,
                context_snapshot={"slots": slots, "intent": state["intent"]},
                idempotency_key=f"esc-{conv_id}-{trace_id}",
                trace_id=trace_id,
            )
            traces.append({"tool": "escalate_to_human", **esc_res.model_dump(mode="json")})
            if esc_res.ok and esc_res.data:
                esc_code = esc_res.data["escalation_code"]
            return {
                "tool_traces": traces,
                "escalation_code": esc_code,
            }

        if decision == "VERIFY_THEN_STAGE":
            code = str(slots["confirmation_code"])
            contact = str(slots.get("guest_phone") or slots.get("guest_email"))
            ver_res = await verify_guest_reservation_tool(
                self.session,
                confirmation_code=code,
                phone_or_email=contact,
                conversation_id=conv_id,
                trace_id=trace_id,
            )
            traces.append({"tool": "verify_guest_reservation", **ver_res.model_dump(mode="json")})
            if ver_res.ok and ver_res.data:
                verified_tokens[code] = ver_res.data["verification_token"]
                target_intent = slots.pop("pending_verification_for", state["intent"])
                if target_intent in {"CANCEL_BOOKING", "MODIFY_BOOKING"}:
                    action_type = (
                        "CANCEL_RESERVATION"
                        if target_intent == "CANCEL_BOOKING"
                        else "MODIFY_RESERVATION"
                    )
                    pending = {
                        "type": action_type,
                        "confirmation_code": code,
                        "idempotency_key": f"agent-{action_type.lower()}-{conv_id}-{uuid4().hex[:8]}",
                        "summary": ver_res.data["reservation"],
                        "slots": slots,
                    }
                    return {
                        "verified_tokens": verified_tokens,
                        "pending_action": pending,
                        "slots": slots,
                        "tool_traces": traces,
                        "requires_confirmation": True,
                    }
                return {
                    "verified_tokens": verified_tokens,
                    "slots": slots,
                    "tool_traces": traces,
                }
            return {"tool_traces": traces}

        if decision == "EXECUTE_VIEW_BOOKING":
            code = str(slots["confirmation_code"])
            det = await get_reservation_details_tool(
                self.session,
                confirmation_code=code,
                verification_token=verified_tokens.get(code),
                conversation_id=conv_id,
                trace_id=trace_id,
            )
            traces.append({"tool": "get_reservation_details", **det.model_dump(mode="json")})
            return {"tool_traces": traces}

        if decision == "STAGE_CANCEL_CONFIRMATION":
            code = str(slots["confirmation_code"])
            det = await get_reservation_details_tool(
                self.session,
                confirmation_code=code,
                verification_token=verified_tokens.get(code),
                conversation_id=conv_id,
                trace_id=trace_id,
            )
            traces.append({"tool": "get_reservation_details", **det.model_dump(mode="json")})
            if det.ok and det.data:
                pending = {
                    "type": "CANCEL_RESERVATION",
                    "confirmation_code": code,
                    "idempotency_key": f"agent-cancel-{conv_id}-{uuid4().hex[:8]}",
                    "summary": det.data,
                }
                return {
                    "tool_traces": traces,
                    "pending_action": pending,
                    "requires_confirmation": True,
                }
            return {"tool_traces": traces}

        if decision == "STAGE_MODIFY_CONFIRMATION":
            code = str(slots["confirmation_code"])
            pending = {
                "type": "MODIFY_RESERVATION",
                "confirmation_code": code,
                "idempotency_key": f"agent-modify-{conv_id}-{uuid4().hex[:8]}",
                "slots": slots,
            }
            return {
                "pending_action": pending,
                "requires_confirmation": True,
            }

        if decision == "EXECUTE_CANCEL_RESERVATION" and pending:
            code = str(pending["confirmation_code"])
            can_res = await cancel_reservation_tool(
                self.session,
                req=CancelReservationRequest(
                    confirmation_code=code,
                    verification_token=verified_tokens.get(code),
                    conversation_id=conv_id,
                    reason="Guest confirmed cancellation via AI receptionist",
                ),
                idempotency_key=str(pending["idempotency_key"]),
                trace_id=trace_id,
            )
            traces.append({"tool": "cancel_reservation", **can_res.model_dump(mode="json")})
            if not can_res.ok and can_res.error_code == "CANCELLATION_WINDOW_EXPIRED":
                esc_res = await escalate_to_human_tool(
                    self.session,
                    conversation_id=conv_id,
                    reason_code="NON_REFUNDABLE_OR_LATE_CANCELLATION",
                    summary=f"Guest asked to cancel {code} outside free cancellation policy.",
                    confirmation_code=code,
                    property_code=slots.get("property_code"),
                    priority=EscalationPriority.HIGH,
                    context_snapshot={"error": can_res.message},
                    idempotency_key=f"esc-can-{conv_id}-{trace_id}",
                    trace_id=trace_id,
                )
                traces.append({"tool": "escalate_to_human", **esc_res.model_dump(mode="json")})
                if esc_res.ok and esc_res.data:
                    esc_code = esc_res.data["escalation_code"]
            return {
                "tool_traces": traces,
                "pending_action": None,
                "escalation_code": esc_code,
                "requires_confirmation": False,
            }

        if decision == "EXECUTE_MODIFY_RESERVATION" and pending:
            code = str(pending["confirmation_code"])
            p_slots = pending.get("slots") or slots
            mod_req = ModifyReservationRequest(
                confirmation_code=code,
                verification_token=verified_tokens.get(code),
                conversation_id=conv_id,
                new_check_in_date=(
                    date.fromisoformat(p_slots["check_in_date"])
                    if p_slots.get("check_in_date")
                    else None
                ),
                new_check_out_date=(
                    date.fromisoformat(p_slots["check_out_date"])
                    if p_slots.get("check_out_date")
                    else None
                ),
                new_room_type_code=p_slots.get("room_type_code"),
                new_special_requests=p_slots.get("new_special_requests"),
            )
            mod_res = await modify_reservation_tool(
                self.session,
                req=mod_req,
                idempotency_key=str(pending["idempotency_key"]),
                trace_id=trace_id,
            )
            traces.append({"tool": "modify_reservation", **mod_res.model_dump(mode="json")})
            return {
                "tool_traces": traces,
                "pending_action": None,
                "requires_confirmation": False,
            }

        if decision in {"EXECUTE_CHECK_AVAILABILITY", "STAGE_OR_COLLECT_GUEST_FOR_BOOKING"}:
            avail_res = await check_availability_tool(
                self.session,
                property_code=str(slots["property_code"]),
                check_in_date=date.fromisoformat(slots["check_in_date"]),
                check_out_date=date.fromisoformat(slots["check_out_date"]),
                adults=int(slots.get("adults", 2)),
                children=int(slots.get("children", 0)),
                rooms_count=int(slots.get("rooms_count", 1)),
                room_type_code=slots.get("room_type_code"),
                trace_id=trace_id,
            )
            traces.append({"tool": "check_availability", **avail_res.model_dump(mode="json")})

            if decision == "STAGE_OR_COLLECT_GUEST_FOR_BOOKING" and avail_res.ok and avail_res.data:
                options = avail_res.data.get("options") or []
                has_guest_info = all(
                    slots.get(k) for k in ("guest_full_name", "guest_email", "guest_phone")
                )
                if options and has_guest_info:
                    chosen_opt = options[0]
                    target_rp_code = slots.get("rate_plan_code") or "FLEX_24H"
                    chosen_rp = next(
                        (
                            rp
                            for rp in chosen_opt["rate_plans"]
                            if rp["rate_plan_code"] == target_rp_code
                        ),
                        chosen_opt["rate_plans"][0],
                    )
                    pending = {
                        "type": "CREATE_RESERVATION",
                        "idempotency_key": f"agent-book-{conv_id}-{uuid4().hex[:8]}",
                        "property_code": chosen_opt["property_code"],
                        "property_name": chosen_opt["property_name"],
                        "room_type_code": chosen_opt["room_type_code"],
                        "room_type_name": chosen_opt["room_type_name"],
                        "rate_plan_code": chosen_rp["rate_plan_code"],
                        "check_in_date": slots["check_in_date"],
                        "check_out_date": slots["check_out_date"],
                        "adults": int(slots.get("adults", 2)),
                        "children": int(slots.get("children", 0)),
                        "total_amount_vnd": chosen_rp["total_amount_vnd"],
                        "guest_full_name": slots["guest_full_name"],
                        "guest_email": slots["guest_email"],
                        "guest_phone": slots["guest_phone"],
                        "preferred_language": state["language"],
                    }
                    return {
                        "tool_traces": traces,
                        "pending_action": pending,
                        "requires_confirmation": True,
                    }
            return {"tool_traces": traces}

        if decision == "EXECUTE_CREATE_RESERVATION" and pending:
            req = CreateReservationRequest(
                property_code=pending["property_code"],
                room_type_code=pending["room_type_code"],
                rate_plan_code=pending["rate_plan_code"],
                check_in_date=date.fromisoformat(pending["check_in_date"]),
                check_out_date=date.fromisoformat(pending["check_out_date"]),
                adults=int(pending["adults"]),
                children=int(pending["children"]),
                guest_full_name=pending["guest_full_name"],
                guest_email=pending["guest_email"],
                guest_phone=pending["guest_phone"],
                preferred_language=pending.get("preferred_language", state["language"]),
                source_channel=state.get("channel", "web_chat"),
            )
            book_res = await create_reservation_tool(
                self.session,
                req=req,
                idempotency_key=str(pending["idempotency_key"]),
                trace_id=trace_id,
            )
            traces.append({"tool": "create_reservation", **book_res.model_dump(mode="json")})
            return {
                "tool_traces": traces,
                "pending_action": None,
                "requires_confirmation": False,
            }

        if decision == "EXECUTE_POLICY_RAG":
            rag_res = await search_hotel_policy_tool(
                self.session,
                query=state["user_message"],
                property_code=slots.get("property_code") or "GROUP",
                language=state["language"],
                trace_id=trace_id,
            )
            traces.append({"tool": "search_hotel_policy", **rag_res.model_dump(mode="json")})
            if rag_res.ok and rag_res.data:
                citations = rag_res.data.get("citations") or []
            return {"tool_traces": traces, "citations": citations}

        if decision == "EXECUTE_CREATE_TICKET":
            cat = TicketCategory(
                slots.get("ticket_category") or TicketCategory.SPECIAL_REQUEST.value
            )
            prio = (
                TicketPriority.HIGH
                if cat == TicketCategory.COMPLAINT
                else TicketPriority.NORMAL
            )
            tck_res = await create_support_ticket_tool(
                self.session,
                property_code=str(slots["property_code"]),
                category=cat,
                priority=prio,
                description=state["user_message"],
                confirmation_code=slots.get("confirmation_code"),
                conversation_id=conv_id,
                idempotency_key=f"agent-tck-{conv_id}-{trace_id}",
                trace_id=trace_id,
            )
            traces.append({"tool": "create_support_ticket", **tck_res.model_dump(mode="json")})
            return {"tool_traces": traces}

        return {"tool_traces": traces}

    async def _grounded_responder_node(
        self, state: ReceptionistGraphState
    ) -> dict[str, Any]:
        lang = state["language"]
        vi = lang == "vi"
        decision = state["gate_decision"]
        traces = state.get("tool_traces") or []
        pending = state.get("pending_action")
        esc_code = state.get("escalation_code")
        verified_codes = list((state.get("verified_tokens") or {}).keys())

        reply = ""

        if decision == "BLOCK_INJECTION":
            reply = (
                "Xin lỗi, tôi không thể bỏ qua quy định giá hoặc xác minh bảo mật của khách sạn Aurora. "
                "Tôi có thể kiểm tra giá phòng chính thức hoặc chuyển bạn cho nhân viên lễ tân."
                if vi
                else "I cannot override hotel pricing rules or bypass verification controls. "
                "I can check official room rates or escalate your request to our front-desk team."
            )
        elif decision in {"AUTO_ESCALATE_GROUP", "ESCALATE_REQUESTED"}:
            reply = (
                f"Tôi đã chuyển toàn bộ nội dung trao đổi sang lễ tân/quản lý trực ca với mã hỗ trợ {esc_code}. "
                "Nhân viên của Aurora sẽ liên hệ xử lý ngay cho quý khách."
                if vi
                else f"I have escalated your request with full conversation context to our human front-desk team "
                f"(Reference: {esc_code}). A specialist will assist you shortly."
            )
        elif decision == "CLEAR_PENDING":
            reply = (
                "Đã hủy thao tác đang chờ. Quý khách cần kiểm tra phòng hay hỗ trợ thêm thông tin nào khác không?"
                if vi
                else "Understood — I have cancelled that pending action. How else may I assist you?"
            )
        elif decision == "NEED_CONFIRMATION_CODE":
            reply = (
                "Vui lòng cung cấp mã đặt phòng (ví dụ AUR-SGN-XXXXXX) để tôi hỗ trợ kiểm tra."
                if vi
                else "Please provide your reservation confirmation code (e.g., AUR-SGN-XXXXXX) so I can look it up."
            )
        elif decision == "NEED_VERIFICATION_CONTACT":
            reply = (
                "Để bảo vệ thông tin cá nhân, vui lòng cung cấp số điện thoại hoặc email bạn đã dùng khi đặt phòng."
                if vi
                else "For security, please confirm the phone number or email address associated with this booking."
            )
        elif decision == "NEED_MODIFICATION_DETAILS":
            reply = (
                "Bạn đã được xác minh. Vui lòng cho biết ngày nhận/trả phòng mới hoặc yêu cầu thay đổi cụ thể."
                if vi
                else "Your booking is verified. Please share your new check-in/check-out dates or desired room changes."
            )
        elif decision == "NEED_BOOKING_SLOTS" or decision == "NEED_AVAILABILITY_SLOTS":
            reply = (
                "Quý khách muốn kiểm tra phòng tại chi nhánh nào (Sài Gòn SGN, Đà Nẵng DAD, hay Phú Quốc PQC) và ngày nhận/trả phòng dự kiến?"
                if vi
                else "Which Aurora property (Saigon SGN, Da Nang DAD, or Phu Quoc PQC) and check-in/check-out dates would you like to check?"
            )
        elif decision == "NEED_PROPERTY_FOR_TICKET":
            reply = (
                "Quý khách đang lưu trú tại chi nhánh nào (SGN, DAD, hoặc PQC) để tôi tạo phiếu yêu cầu hỗ trợ ngay?"
                if vi
                else "Which Aurora property (SGN, DAD, or PQC) is your request for so I can open a support ticket?"
            )
        elif decision == "GREETING":
            reply = (
                "Xin chào! Tôi là trợ lý lễ tân AI của Aurora Hospitality Group (Sài Gòn, Đà Nẵng, Phú Quốc). "
                "Tôi có thể giúp bạn kiểm tra phòng trống, đặt phòng, đổi/hủy phòng hoặc giải đáp chính sách khách sạn."
                if vi
                else "Welcome to Aurora Hospitality Group (Saigon, Da Nang, Phu Quoc)! "
                "I can help check live room availability, create or manage bookings, and answer hotel policy questions."
            )
        elif traces:
            last = traces[-1]
            tool_name = last["tool"]
            ok = last["ok"]
            data = last.get("data") or {}

            if not ok:
                err_code = last.get("error_code")
                if esc_code:
                    reply = (
                        f"{last['message']} Tôi đã chuyển yêu cầu sang quản lý lễ tân với mã {esc_code}."
                        if vi
                        else f"{last['message']} I have escalated this to our front-desk team under reference {esc_code}."
                    )
                else:
                    reply = (
                        f"Không thể thực hiện yêu cầu ({err_code}): {last['message']}"
                        if vi
                        else f"Unable to complete request ({err_code}): {last['message']}"
                    )
            elif tool_name == "create_reservation":
                reply = (
                    f"Đặt phòng thành công! Mã xác nhận của quý khách là {data['confirmation_code']} tại "
                    f"{data['property_name']} ({data['room_type_name']}, {data['check_in_date']} đến {data['check_out_date']}), "
                    f"tổng thanh toán {_format_vnd(data['total_amount_vnd'])}."
                    if vi
                    else f"Your reservation is confirmed! Confirmation code: {data['confirmation_code']} at "
                    f"{data['property_name']} ({data['room_type_name']}, {data['check_in_date']} to {data['check_out_date']}), "
                    f"total {_format_vnd(data['total_amount_vnd'])}."
                )
            elif tool_name == "cancel_reservation":
                reply = (
                    f"Đơn đặt phòng {data['confirmation_code']} đã được hủy thành công (trạng thái: {data['status']})."
                    if vi
                    else f"Reservation {data['confirmation_code']} has been cancelled (status: {data['status']})."
                )
            elif tool_name == "modify_reservation":
                reply = (
                    f"Đã cập nhật đơn {data['confirmation_code']}: nhận phòng {data['check_in_date']}, "
                    f"trả phòng {data['check_out_date']}, tổng mới {_format_vnd(data['total_amount_vnd'])}."
                    if vi
                    else f"Updated reservation {data['confirmation_code']}: {data['check_in_date']} to "
                    f"{data['check_out_date']}, new total {_format_vnd(data['total_amount_vnd'])}."
                )
            elif tool_name == "check_availability":
                options = data.get("options") or []
                if not options:
                    reply = (
                        "Rất tiếc, hiện không còn phòng trống phù hợp cho ngày bạn chọn."
                        if vi
                        else "Sorry, no rooms are available matching those dates and guest count."
                    )
                elif pending and pending.get("type") == "CREATE_RESERVATION":
                    reply = (
                        f"Phòng {pending['room_type_name']} tại {pending['property_name']} từ "
                        f"{pending['check_in_date']} đến {pending['check_out_date']} (gói {pending['rate_plan_code']}) "
                        f"có tổng giá {_format_vnd(pending['total_amount_vnd'])}. Quý khách vui lòng nhắn 'Xác nhận' để tôi hoàn tất đặt phòng."
                        if vi
                        else f"{pending['room_type_name']} at {pending['property_name']} from "
                        f"{pending['check_in_date']} to {pending['check_out_date']} ({pending['rate_plan_code']}) "
                        f"is available for {_format_vnd(pending['total_amount_vnd'])} total. Please reply 'Confirm' to finalize your booking."
                    )
                else:
                    lines = []
                    for opt in options[:3]:
                        flex = next(
                            (r for r in opt["rate_plans"] if r["rate_plan_code"] == "FLEX_24H"),
                            opt["rate_plans"][0],
                        )
                        lines.append(
                            f"- {opt['room_type_name']} ({opt['min_available_rooms']} left): {_format_vnd(flex['total_amount_vnd'])}"
                        )
                    summary_str = "\n".join(lines)
                    reply = (
                        f"Các hạng phòng trống tại {data['property_name']} ({data['check_in_date']} - {data['check_out_date']}):\n{summary_str}\n"
                        "Để đặt phòng, vui lòng cho tôi biết hạng phòng, họ tên, SĐT và email."
                        if vi
                        else f"Available rooms at {data['property_name']} ({data['check_in_date']} to {data['check_out_date']}):\n{summary_str}\n"
                        "To book, please share your preferred room type, full name, phone, and email."
                    )
            elif tool_name in {"verify_guest_reservation", "get_reservation_details"}:
                res_obj = data.get("reservation", data)
                if pending and pending.get("type") == "CANCEL_RESERVATION":
                    reply = (
                        f"Đã xác minh đơn {res_obj['confirmation_code']} ({res_obj['room_type_name']}, "
                        f"{res_obj['check_in_date']} - {res_obj['check_out_date']}). Bạn có chắc chắn muốn hủy đặt phòng này không? Hãy nhắn 'Xác nhận'."
                        if vi
                        else f"Verified booking {res_obj['confirmation_code']} ({res_obj['room_type_name']}, "
                        f"{res_obj['check_in_date']} to {res_obj['check_out_date']}). Reply 'Confirm' to cancel it."
                    )
                elif pending and pending.get("type") == "MODIFY_RESERVATION":
                    reply = (
                        f"Đã xác minh đơn {res_obj['confirmation_code']}. Vui lòng nhắn 'Xác nhận' để áp dụng thay đổi."
                        if vi
                        else f"Verified booking {res_obj['confirmation_code']}. Please reply 'Confirm' to apply your changes."
                    )
                else:
                    reply = (
                        f"Thông tin đơn {res_obj['confirmation_code']}: {res_obj['property_name']}, "
                        f"{res_obj['room_type_name']}, {res_obj['check_in_date']} - {res_obj['check_out_date']}, "
                        f"trạng thái {res_obj['status']}, tổng {_format_vnd(res_obj['total_amount_vnd'])}."
                        if vi
                        else f"Reservation {res_obj['confirmation_code']}: {res_obj['property_name']}, "
                        f"{res_obj['room_type_name']}, {res_obj['check_in_date']} to {res_obj['check_out_date']}, "
                        f"status {res_obj['status']}, total {_format_vnd(res_obj['total_amount_vnd'])}."
                    )
            elif tool_name == "search_hotel_policy":
                if data.get("abstained"):
                    reply = (
                        "Xin lỗi, thông tin này không có trong tài liệu chính sách chính thức của Aurora. "
                        "Tôi có thể kết nối quý khách với nhân viên lễ tân để hỗ trợ trực tiếp."
                        if vi
                        else "I'm sorry, I do not have an official Aurora policy covering that question. "
                        "I would be happy to escalate you to a human receptionist."
                    )
                else:
                    top = (data.get("citations") or [])[0]
                    reply = (
                        f"{top['content']} [Nguồn: {top['property_code']} / {top['doc_id']}]"
                        if vi
                        else f"{top['content']} [Source: {top['property_code']} / {top['doc_id']}]"
                    )
            elif tool_name == "create_support_ticket":
                reply = (
                    f"Tôi đã tạo phiếu yêu cầu hỗ trợ {data['ticket_code']} (Loại: {data['category']}) cho quý khách."
                    if vi
                    else f"I have created support ticket {data['ticket_code']} ({data['category']}) for our team."
                )
        elif decision == "STAGE_MODIFY_CONFIRMATION" and pending:
            reply = (
                f"Bạn đã xác minh đơn {pending['confirmation_code']}. Vui lòng nhắn 'Xác nhận' để thực hiện thay đổi."
                if vi
                else f"Your booking {pending['confirmation_code']} is verified. Please reply 'Confirm' to apply the modification."
            )

        synthesized, resp_provider = await self.llm_client.synthesize_grounded_reply(
            user_message=state["user_message"],
            language=lang,
            gate_decision=decision,
            tool_traces=traces,
            citations=state.get("citations") or [],
            pending_action=pending,
            fallback_reply=reply,
        )
        safe_reply = _verify_no_hallucinated_codes(
            synthesized, traces, state["user_message"], verified_codes
        )
        return {
            "reply": safe_reply,
            "ai_provider_used": (
                resp_provider
                if resp_provider in {"gemini", "openai"}
                else state.get("ai_provider_used", "deterministic_fallback")
            ),
        }

    async def handle_turn(
        self,
        user_message: str,
        conversation_id: UUID | None = None,
        channel: str = "web_chat",
        property_code: str | None = None,
        trace_id: str | None = None,
    ) -> AgentTurnResponse:
        """Load conversation state from PostgreSQL, run LangGraph, and persist turn."""
        trace = trace_id or uuid4().hex

        conv: Conversation | None = None
        if conversation_id is not None:
            conv = await self.session.scalar(
                select(Conversation).where(Conversation.id == conversation_id)
            )

        if conv is None:
            prop_id = None
            if property_code:
                prop = await self.session.scalar(
                    select(Property).where(Property.code == property_code.strip().upper())
                )
                prop_id = prop.id if prop else None
            conv = Conversation(
                id=conversation_id or uuid4(),
                property_id=prop_id,
                channel=channel,
                language="vi",
                status="ACTIVE",
            )
            self.session.add(conv)
            await self.session.flush()

        # Rehydrate persisted LangGraph state from latest assistant message
        prior_state: dict[str, Any] = {}
        last_assistant = await self.session.scalar(
            select(ConversationMessage)
            .where(
                ConversationMessage.conversation_id == conv.id,
                ConversationMessage.role == "assistant",
            )
            .order_by(ConversationMessage.created_at.desc())
            .limit(1)
        )
        if last_assistant and last_assistant.tool_calls:
            prior_state = dict(last_assistant.tool_calls.get("state_snapshot") or {})

        slots = dict(prior_state.get("slots") or {})
        if property_code and not slots.get("property_code"):
            slots["property_code"] = property_code.strip().upper()

        user_msg_row = ConversationMessage(
            conversation_id=conv.id,
            role="user",
            content=user_message,
            tool_calls=None,
            trace_id=trace,
        )
        self.session.add(user_msg_row)
        await self.session.commit()

        initial_state: ReceptionistGraphState = {
            "conversation_id": str(conv.id),
            "channel": channel,
            "trace_id": trace,
            "user_message": user_message,
            "slots": slots,
            "verified_tokens": dict(prior_state.get("verified_tokens") or {}),
            "pending_action": prior_state.get("pending_action"),
            "escalation_code": prior_state.get("escalation_code"),
            "tool_traces": [],
            "citations": [],
            "requires_confirmation": False,
        }

        final_state = await self.graph.ainvoke(initial_state)

        persisted_snapshot = {
            "slots": final_state.get("slots") or {},
            "verified_tokens": final_state.get("verified_tokens") or {},
            "pending_action": final_state.get("pending_action"),
            "escalation_code": final_state.get("escalation_code"),
        }
        assistant_msg = ConversationMessage(
            conversation_id=conv.id,
            role="assistant",
            content=final_state["reply"],
            tool_calls={
                "traces": final_state.get("tool_traces") or [],
                "citations": final_state.get("citations") or [],
                "state_snapshot": persisted_snapshot,
            },
            trace_id=trace,
        )
        self.session.add(assistant_msg)
        conv.language = final_state["language"]
        if persisted_snapshot["slots"].get("property_code") and conv.property_id is None:
            prop = await self.session.scalar(
                select(Property).where(
                    Property.code == persisted_snapshot["slots"]["property_code"]
                )
            )
            if prop:
                conv.property_id = prop.id
        await self.session.commit()

        return AgentTurnResponse(
            conversation_id=conv.id,
            reply=final_state["reply"],
            language=final_state["language"],
            intent=final_state["intent"],
            property_code=persisted_snapshot["slots"].get("property_code"),
            requires_confirmation=bool(final_state.get("requires_confirmation")),
            pending_action=persisted_snapshot["pending_action"],
            verified_codes=list(persisted_snapshot["verified_tokens"].keys()),
            tool_traces=final_state.get("tool_traces") or [],
            citations=final_state.get("citations") or [],
            escalation_code=persisted_snapshot["escalation_code"],
            ai_provider_used=final_state.get(
                "ai_provider_used", "deterministic_fallback"
            ),
            trace_id=trace,
        )
