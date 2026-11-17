"""All FastAPI routes for Guest Booking, RAG, Chat, Voice, and Staff RBAC Dashboard."""

from typing import Any
from uuid import UUID
from xml.sax.saxutils import escape as xml_escape

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from aurora.agent.graph import AgentTurnResponse, ReceptionistAgentService
from aurora.auth.security import (
    LoginRequest,
    StaffIdentity,
    TokenResponse,
    assert_property_scope,
    authenticate_staff,
    create_staff_jwt,
    require_staff_roles,
)
from aurora.db.models import (
    AuditLog,
    Conversation,
    ConversationMessage,
    Escalation,
    Property,
    Reservation,
    SupportTicket,
)
from aurora.db.session import get_db_session
from aurora.domain.enums import (
    ActorType,
    EscalationStatus,
    ReservationStatus,
    StaffRole,
    TicketStatus,
)
from aurora.domain.errors import DomainError
from aurora.domain.schemas import (
    AvailabilityQuery,
    AvailabilityResponse,
    CancelReservationRequest,
    CreateEscalationRequest,
    CreateReservationRequest,
    CreateTicketRequest,
    EscalationOut,
    ModifyReservationRequest,
    ReservationOut,
    ResolveEscalationRequest,
    StaffStatusTransitionRequest,
    TicketOut,
    ToolResult,
    UpdateTicketStatusRequest,
    VerifyGuestRequest,
    VerifyGuestResponse,
)
from aurora.rag.retriever import PolicySearchResponse, search_hotel_policy
from aurora.services.booking_service import (
    cancel_reservation,
    check_availability,
    create_reservation,
    list_reservations,
    modify_reservation,
    transition_reservation_status_by_staff,
)
from aurora.services.escalation_service import (
    create_escalation,
    list_escalations,
    resolve_escalation,
)
from aurora.services.outbound_call import (
    OutboundCallRequest,
    OutboundCallResult,
    dispatch_outbound_call,
)
from aurora.services.ticket_service import (
    create_support_ticket,
    list_support_tickets,
    update_ticket_status,
)
from aurora.services.verification import verify_guest_reservation
from aurora.voice.vieneu_tts import VieNeuTTSProvider, get_audio_wav
from aurora.voice.worker import (
    BargeInResult,
    VoiceSessionController,
    VoiceTurnResult,
    create_livekit_room_token,
)

router = APIRouter(prefix="/api/v1")

# Per-conversation active VoiceSessionController & Twilio CallSid registries
_VOICE_SESSIONS: dict[UUID, VoiceSessionController] = {}
_TWILIO_CALL_CONVERSATIONS: dict[str, UUID] = {}


class PolicySearchRequest(BaseModel):
    query: str = Field(min_length=2, max_length=500)
    property_code: str = Field(default="GROUP", max_length=10)
    language: str | None = Field(default=None, pattern="^(vi|en)$")
    top_k: int = Field(default=3, ge=1, le=5)


class ChatTurnRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    conversation_id: UUID | None = None
    property_code: str | None = None


class VoiceTurnRequest(BaseModel):
    transcript: str = Field(min_length=1, max_length=2000)
    conversation_id: UUID | None = None
    property_code: str | None = None
    stt_ms: float = Field(default=0.0, ge=0.0)


class BargeInRequest(BaseModel):
    conversation_id: UUID


class LiveKitTokenRequest(BaseModel):
    room_name: str = Field(default="aurora-reception", min_length=2, max_length=120)
    participant_identity: str = Field(min_length=2, max_length=120)
    participant_name: str = Field(default="Guest", min_length=1, max_length=120)


class SynthesizeTTSRequest(BaseModel):
    text: str = Field(min_length=1, max_length=2000)
    property_code: str | None = None
    voice_preset: str | None = None


def _require_idempotency_key(idempotency_key: str | None) -> str:
    if not idempotency_key or not idempotency_key.strip():
        raise DomainError(
            error_code="MISSING_IDEMPOTENCY_KEY",
            message="Header 'Idempotency-Key' is required for mutating requests.",
            status_code=400,
        )
    return idempotency_key.strip()


def _trace_id(request: Request) -> str:
    return getattr(request.state, "trace_id", "unknown")


# --- Auth ---
@router.post("/auth/login", response_model=TokenResponse)
async def staff_login(
    req: LoginRequest,
    session: AsyncSession = Depends(get_db_session),
) -> TokenResponse:
    identity = await authenticate_staff(session, req.email, req.password)
    token, ttl = create_staff_jwt(identity)
    return TokenResponse(
        access_token=token,
        expires_in_seconds=ttl,
        staff=identity,
    )


# --- Public Catalog, Availability & Booking ---
@router.get("/properties")
async def get_properties(
    session: AsyncSession = Depends(get_db_session),
) -> list[dict[str, Any]]:
    props = (
        await session.scalars(
            select(Property)
            .options(selectinload(Property.room_types))
            .order_by(Property.code.asc())
        )
    ).all()
    return [
        {
            "code": p.code,
            "name": p.name,
            "city": p.city,
            "check_in_time": p.check_in_time.strftime("%H:%M"),
            "check_out_time": p.check_out_time.strftime("%H:%M"),
            "currency": p.currency,
            "room_types": [
                {
                    "code": rt.code,
                    "name": rt.name,
                    "max_adults": rt.max_adults,
                    "max_children": rt.max_children,
                    "total_rooms": rt.total_rooms,
                    "base_rate_vnd": int(rt.base_rate_vnd),
                }
                for rt in p.room_types
            ],
        }
        for p in props
    ]


@router.post("/availability", response_model=ToolResult[AvailabilityResponse])
async def api_check_availability(
    query: AvailabilityQuery,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> ToolResult[AvailabilityResponse]:
    data = await check_availability(session, query)
    return ToolResult(
        ok=True,
        data=data,
        message="Availability retrieved.",
        trace_id=_trace_id(request),
    )


@router.post(
    "/reservations",
    response_model=ToolResult[ReservationOut],
    status_code=status.HTTP_201_CREATED,
)
async def api_create_reservation(
    req: CreateReservationRequest,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_db_session),
) -> ToolResult[ReservationOut]:
    key = _require_idempotency_key(idempotency_key)
    out = await create_reservation(
        session,
        req=req,
        idempotency_key=key,
        actor_type=ActorType.GUEST.value,
        actor_id=str(req.guest_email),
        trace_id=_trace_id(request),
    )
    return ToolResult(
        ok=True,
        data=out,
        message="Reservation confirmed.",
        trace_id=_trace_id(request),
    )


@router.post("/reservations/verify", response_model=ToolResult[VerifyGuestResponse])
async def api_verify_guest(
    req: VerifyGuestRequest,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> ToolResult[VerifyGuestResponse]:
    out = await verify_guest_reservation(
        session,
        confirmation_code=req.confirmation_code,
        phone_or_email=req.phone_or_email,
        conversation_id=req.conversation_id,
    )
    return ToolResult(
        ok=True,
        data=out,
        message="Guest verified.",
        trace_id=_trace_id(request),
    )


@router.patch("/reservations/{confirmation_code}", response_model=ToolResult[ReservationOut])
async def api_modify_reservation(
    confirmation_code: str,
    req: ModifyReservationRequest,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_db_session),
) -> ToolResult[ReservationOut]:
    key = _require_idempotency_key(idempotency_key)
    req_bound = req.model_copy(update={"confirmation_code": confirmation_code})
    out = await modify_reservation(
        session,
        req=req_bound,
        idempotency_key=key,
        actor_type=ActorType.AI_AGENT.value,
        actor_id="guest-api",
        trace_id=_trace_id(request),
    )
    return ToolResult(
        ok=True,
        data=out,
        message="Reservation modified.",
        trace_id=_trace_id(request),
    )


@router.post(
    "/reservations/{confirmation_code}/cancel",
    response_model=ToolResult[ReservationOut],
)
async def api_cancel_reservation(
    confirmation_code: str,
    req: CancelReservationRequest,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_db_session),
) -> ToolResult[ReservationOut]:
    key = _require_idempotency_key(idempotency_key)
    req_bound = req.model_copy(update={"confirmation_code": confirmation_code})
    out = await cancel_reservation(
        session,
        req=req_bound,
        idempotency_key=key,
        actor_type=ActorType.AI_AGENT.value,
        actor_id="guest-api",
        trace_id=_trace_id(request),
    )
    return ToolResult(
        ok=True,
        data=out,
        message="Reservation cancelled.",
        trace_id=_trace_id(request),
    )


@router.post(
    "/tickets",
    response_model=ToolResult[TicketOut],
    status_code=status.HTTP_201_CREATED,
)
async def api_create_ticket(
    req: CreateTicketRequest,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_db_session),
) -> ToolResult[TicketOut]:
    key = _require_idempotency_key(idempotency_key)
    out = await create_support_ticket(
        session,
        req=req,
        idempotency_key=key,
        actor_type=ActorType.GUEST.value,
        actor_id="guest-api",
        trace_id=_trace_id(request),
    )
    return ToolResult(
        ok=True,
        data=out,
        message="Support ticket created.",
        trace_id=_trace_id(request),
    )


@router.post(
    "/escalations",
    response_model=ToolResult[EscalationOut],
    status_code=status.HTTP_201_CREATED,
)
async def api_create_escalation(
    req: CreateEscalationRequest,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_db_session),
) -> ToolResult[EscalationOut]:
    key = _require_idempotency_key(idempotency_key)
    out = await create_escalation(
        session,
        req=req,
        idempotency_key=key,
        actor_type=ActorType.AI_AGENT.value,
        actor_id="guest-api",
        trace_id=_trace_id(request),
    )
    return ToolResult(
        ok=True,
        data=out,
        message="Escalation created.",
        trace_id=_trace_id(request),
    )


# --- Grounded Policy RAG ---
@router.post("/policies/search", response_model=ToolResult[PolicySearchResponse])
async def api_search_policies(
    req: PolicySearchRequest,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> ToolResult[PolicySearchResponse]:
    res = await search_hotel_policy(
        session,
        query=req.query,
        property_code=req.property_code,
        language=req.language,
        top_k=req.top_k,
    )
    return ToolResult(
        ok=True,
        data=res,
        message="Policy search completed.",
        trace_id=_trace_id(request),
    )


# --- Chat & Voice Receptionist ---
@router.post("/chat/turn", response_model=AgentTurnResponse)
async def api_chat_turn(
    req: ChatTurnRequest,
    request: Request,
    x_ai_provider: str | None = Header(default=None, alias="X-AI-Provider"),
    x_ai_api_key: str | None = Header(default=None, alias="X-AI-Api-Key"),
    session: AsyncSession = Depends(get_db_session),
) -> AgentTurnResponse:
    agent = ReceptionistAgentService(
        session,
        ai_provider=x_ai_provider,
        ai_api_key=x_ai_api_key,
    )
    return await agent.handle_turn(
        user_message=req.message,
        conversation_id=req.conversation_id,
        channel="web_chat",
        property_code=req.property_code,
        trace_id=_trace_id(request),
    )


@router.post("/voice/turn", response_model=VoiceTurnResult)
async def api_voice_turn(
    req: VoiceTurnRequest,
    request: Request,
    x_ai_provider: str | None = Header(default=None, alias="X-AI-Provider"),
    x_ai_api_key: str | None = Header(default=None, alias="X-AI-Api-Key"),
    session: AsyncSession = Depends(get_db_session),
) -> VoiceTurnResult:
    controller = (
        _VOICE_SESSIONS.get(req.conversation_id)
        if req.conversation_id
        else None
    ) or VoiceSessionController()
    result = await controller.process_voice_turn(
        session,
        transcript=req.transcript,
        conversation_id=req.conversation_id,
        property_code=req.property_code,
        stt_ms=req.stt_ms,
        trace_id=_trace_id(request),
        ai_provider=x_ai_provider,
        ai_api_key=x_ai_api_key,
    )
    _VOICE_SESSIONS[result.conversation_id] = controller
    return result


@router.post("/voice/barge-in", response_model=BargeInResult)
async def api_voice_barge_in(
    req: BargeInRequest,
    session: AsyncSession = Depends(get_db_session),
) -> BargeInResult:
    controller = _VOICE_SESSIONS.setdefault(
        req.conversation_id, VoiceSessionController()
    )
    return await controller.handle_barge_in(session, req.conversation_id)


@router.post("/voice/livekit-token")
async def api_voice_livekit_token(req: LiveKitTokenRequest) -> dict[str, Any]:
    return create_livekit_room_token(
        room_name=req.room_name,
        participant_identity=req.participant_identity,
        participant_name=req.participant_name,
    )


@router.post("/voice/tts")
async def api_voice_tts(req: SynthesizeTTSRequest) -> dict[str, Any]:
    """Synthesize speech via VieNeu-TTS v3 Turbo and return playable 48 kHz WAV URL."""
    return await VieNeuTTSProvider().synthesize_wav(
        req.text,
        property_code=req.property_code,
        voice_preset=req.voice_preset,
    )


@router.get("/telephony/tts/{audio_id}.wav")
async def api_get_telephony_wav(audio_id: str) -> Response:
    """Serve cached 48 kHz VieNeu-TTS WAV audio for browser <audio> or Twilio <Play>."""
    wav_bytes = get_audio_wav(audio_id)
    if wav_bytes is None:
        raise HTTPException(
            status_code=404,
            detail={"error_code": "AUDIO_NOT_FOUND", "message": "Audio clip expired or missing."},
        )
    return Response(content=wav_bytes, media_type="audio/wav")


async def _parse_twilio_payload(request: Request) -> dict[str, str]:
    ctype = (request.headers.get("content-type") or "").lower()
    if "application/json" in ctype:
        body = await request.json()
        return {str(k): str(v) for k, v in body.items() if v is not None}
    form = await request.form()
    return {str(k): str(v) for k, v in form.items()}


def _build_twiml_gather(play_url: str, gather_action_url: str) -> Response:
    xml_body = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        "<Response>\n"
        f'  <Gather input="speech" language="vi-VN" speechTimeout="auto" bargeIn="true" action="{xml_escape(gather_action_url)}" method="POST">\n'
        f"    <Play>{xml_escape(play_url)}</Play>\n"
        "  </Gather>\n"
        "</Response>\n"
    )
    return Response(content=xml_body, media_type="application/xml")


@router.post("/telephony/twilio/incoming")
async def api_twilio_incoming_call(
    request: Request,
) -> Response:
    """Handle incoming phone call webhook from Twilio PSTN / SIP trunk."""
    payload = await _parse_twilio_payload(request)
    call_sid = payload.get("CallSid") or "unknown-call"
    greeting = (
        "Xin chào, cảm ơn quý khách đã gọi đến khách sạn Aurora. "
        "Tôi là trợ lý lễ tân AI. Quý khách muốn đặt phòng hay hỏi thông tin chi nhánh nào ạ?"
    )
    tts_meta = await VieNeuTTSProvider().synthesize_wav(greeting, property_code="SGN")
    base = str(request.base_url).rstrip("/")
    play_url = f"{base}{tts_meta['audio_url']}"
    gather_url = f"{base}/api/v1/telephony/twilio/gather?CallSid={call_sid}"
    return _build_twiml_gather(play_url, gather_url)


@router.api_route("/telephony/twilio/outbound-twiml", methods=["GET", "POST"], response_class=Response)
async def api_twilio_outbound_twiml(
    request: Request,
    conv_id: str = Query(default=""),
    audio_id: str = Query(default=""),
) -> Response:
    """Serve initial greeting TwiML for Twilio outbound call."""
    base = str(request.base_url).rstrip("/")
    play_url = f"{base}/api/v1/telephony/tts/{audio_id}.wav" if audio_id else ""
    gather_url = f"{base}/api/v1/telephony/twilio/gather?CallSid={conv_id}"
    greeting = "Xin chào, mình là nhân viên từ khách sạn Aurora gọi đến để xác nhận thông tin đặt phòng của quý khách."
    xml_body = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        "<Response>\n"
        f'  <Gather input="speech" language="vi-VN" speechTimeout="auto" bargeIn="true" action="{xml_escape(gather_url)}" method="POST">\n'
        f"    <Play>{xml_escape(play_url)}</Play>\n"
        f"    <Say language=\"vi-VN\">{xml_escape(greeting)}</Say>\n"
        "  </Gather>\n"
        "</Response>\n"
    )
    return Response(content=xml_body, media_type="application/xml")


@router.post("/telephony/twilio/gather")
async def api_twilio_gather_speech(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> Response:
    """Process phone caller STT speech from Twilio and reply with VieNeu-TTS v3 Turbo."""
    payload = await _parse_twilio_payload(request)
    call_sid = payload.get("CallSid") or request.query_params.get("CallSid") or "unknown-call"
    speech_text = (payload.get("SpeechResult") or "").strip()
    base = str(request.base_url).rstrip("/")
    gather_url = f"{base}/api/v1/telephony/twilio/gather?CallSid={call_sid}"

    if not speech_text:
        reprompt = "Xin lỗi, tôi chưa nghe rõ. Quý khách có thể nói lại được không ạ?"
        tts_meta = await VieNeuTTSProvider().synthesize_wav(reprompt, property_code="SGN")
        return _build_twiml_gather(f"{base}{tts_meta['audio_url']}", gather_url)

    conv_id = _TWILIO_CALL_CONVERSATIONS.get(call_sid)
    controller = (_VOICE_SESSIONS.get(conv_id) if conv_id else None) or VoiceSessionController()
    result = await controller.process_voice_turn(
        session,
        transcript=speech_text,
        conversation_id=conv_id,
        trace_id=_trace_id(request),
    )
    _TWILIO_CALL_CONVERSATIONS[call_sid] = result.conversation_id
    _VOICE_SESSIONS[result.conversation_id] = controller

    play_url = f"{base}{result.audio_url}"
    return _build_twiml_gather(play_url, gather_url)


@router.post("/telephony/outbound-call", response_model=ToolResult[OutboundCallResult])
async def api_trigger_outbound_call(
    req: OutboundCallRequest,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> ToolResult[OutboundCallResult]:
    """Initiate an outbound phone call with VieNeu-TTS v3 Turbo greeting."""
    res = await dispatch_outbound_call(
        session,
        req,
        trace_id=_trace_id(request),
    )
    return ToolResult(
        ok=res.ok,
        data=res,
        message="Outbound call dispatched.",
        trace_id=_trace_id(request),
    )


@router.get("/conversations/{conversation_id}")
async def api_get_conversation(
    conversation_id: UUID,
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    conv = await session.scalar(
        select(Conversation)
        .options(selectinload(Conversation.messages))
        .where(Conversation.id == conversation_id)
    )
    if conv is None:
        raise HTTPException(
            status_code=404,
            detail={"error_code": "CONVERSATION_NOT_FOUND", "message": "Conversation not found."},
        )
    ordered = sorted(conv.messages, key=lambda m: m.created_at)
    return {
        "id": str(conv.id),
        "channel": conv.channel,
        "language": conv.language,
        "status": conv.status,
        "started_at": conv.started_at.isoformat(),
        "messages": [
            {
                "id": str(m.id),
                "role": m.role,
                "content": m.content,
                "trace_id": m.trace_id,
                "tool_calls": m.tool_calls,
                "created_at": m.created_at.isoformat(),
            }
            for m in ordered
        ],
    }


# --- Staff Operations Dashboard (Strict RBAC) ---
@router.get("/staff/dashboard")
async def api_staff_dashboard(
    property_code: str | None = Query(default=None),
    staff: StaffIdentity = Depends(
        require_staff_roles(
            StaffRole.RECEPTIONIST, StaffRole.MANAGER, StaffRole.ADMIN
        )
    ),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    scoped_prop = assert_property_scope(staff, property_code)
    reservations = await list_reservations(session, property_code=scoped_prop)
    tickets = await list_support_tickets(session, property_code=scoped_prop)
    escalations = await list_escalations(session)
    if scoped_prop:
        escalations = [
            e for e in escalations if e.property_code in (None, scoped_prop)
        ]

    conv_count = await session.scalar(select(func.count()).select_from(Conversation)) or 0
    return {
        "staff": staff.model_dump(mode="json"),
        "property_scope": scoped_prop or "ALL",
        "metrics": {
            "total_reservations": len(reservations),
            "confirmed_reservations": sum(
                1 for r in reservations if r.status == ReservationStatus.CONFIRMED
            ),
            "checked_in_reservations": sum(
                1 for r in reservations if r.status == ReservationStatus.CHECKED_IN
            ),
            "open_tickets": sum(1 for t in tickets if t.status == TicketStatus.OPEN),
            "open_escalations": sum(
                1 for e in escalations if e.status == EscalationStatus.OPEN
            ),
            "total_conversations": conv_count,
        },
    }


@router.get("/staff/reservations", response_model=list[ReservationOut])
async def api_staff_list_reservations(
    property_code: str | None = Query(default=None),
    status_filter: ReservationStatus | None = Query(default=None, alias="status"),
    staff: StaffIdentity = Depends(
        require_staff_roles(
            StaffRole.RECEPTIONIST, StaffRole.MANAGER, StaffRole.ADMIN
        )
    ),
    session: AsyncSession = Depends(get_db_session),
) -> list[ReservationOut]:
    scoped_prop = assert_property_scope(staff, property_code)
    return await list_reservations(session, property_code=scoped_prop, status=status_filter)


@router.post(
    "/staff/reservations/{confirmation_code}/status",
    response_model=ToolResult[ReservationOut],
)
async def api_staff_transition_reservation(
    confirmation_code: str,
    req: StaffStatusTransitionRequest,
    request: Request,
    staff: StaffIdentity = Depends(
        require_staff_roles(
            StaffRole.RECEPTIONIST, StaffRole.MANAGER, StaffRole.ADMIN
        )
    ),
    session: AsyncSession = Depends(get_db_session),
) -> ToolResult[ReservationOut]:
    # Enforce property scope before transitioning
    matches = [
        r
        for r in await list_reservations(session)
        if r.confirmation_code == confirmation_code.strip().upper()
    ]
    if matches:
        assert_property_scope(staff, matches[0].property_code)

    out = await transition_reservation_status_by_staff(
        session,
        confirmation_code=confirmation_code,
        req=req,
        staff_id=str(staff.id),
        trace_id=_trace_id(request),
    )
    return ToolResult(
        ok=True,
        data=out,
        message=f"Reservation transitioned to {out.status.value}.",
        trace_id=_trace_id(request),
    )


@router.get("/staff/tickets", response_model=list[TicketOut])
async def api_staff_list_tickets(
    property_code: str | None = Query(default=None),
    status_filter: TicketStatus | None = Query(default=None, alias="status"),
    staff: StaffIdentity = Depends(
        require_staff_roles(
            StaffRole.RECEPTIONIST, StaffRole.MANAGER, StaffRole.ADMIN
        )
    ),
    session: AsyncSession = Depends(get_db_session),
) -> list[TicketOut]:
    scoped_prop = assert_property_scope(staff, property_code)
    return await list_support_tickets(session, property_code=scoped_prop, status=status_filter)


@router.patch("/staff/tickets/{ticket_code}", response_model=ToolResult[TicketOut])
async def api_staff_update_ticket(
    ticket_code: str,
    req: UpdateTicketStatusRequest,
    request: Request,
    staff: StaffIdentity = Depends(
        require_staff_roles(
            StaffRole.RECEPTIONIST, StaffRole.MANAGER, StaffRole.ADMIN
        )
    ),
    session: AsyncSession = Depends(get_db_session),
) -> ToolResult[TicketOut]:
    out = await update_ticket_status(
        session,
        ticket_code=ticket_code,
        req=req,
        staff_id=str(staff.id),
        trace_id=_trace_id(request),
    )
    return ToolResult(
        ok=True,
        data=out,
        message="Ticket updated.",
        trace_id=_trace_id(request),
    )


@router.get("/staff/escalations", response_model=list[EscalationOut])
async def api_staff_list_escalations(
    status_filter: EscalationStatus | None = Query(default=None, alias="status"),
    staff: StaffIdentity = Depends(
        require_staff_roles(
            StaffRole.RECEPTIONIST, StaffRole.MANAGER, StaffRole.ADMIN
        )
    ),
    session: AsyncSession = Depends(get_db_session),
) -> list[EscalationOut]:
    escalations = await list_escalations(session, status=status_filter)
    if staff.role != StaffRole.ADMIN and staff.property_code:
        escalations = [
            e for e in escalations if e.property_code in (None, staff.property_code)
        ]
    return escalations


@router.patch(
    "/staff/escalations/{escalation_code}",
    response_model=ToolResult[EscalationOut],
)
async def api_staff_resolve_escalation(
    escalation_code: str,
    req: ResolveEscalationRequest,
    request: Request,
    staff: StaffIdentity = Depends(
        require_staff_roles(
            StaffRole.RECEPTIONIST, StaffRole.MANAGER, StaffRole.ADMIN
        )
    ),
    session: AsyncSession = Depends(get_db_session),
) -> ToolResult[EscalationOut]:
    out = await resolve_escalation(
        session,
        escalation_code=escalation_code,
        req=req,
        staff_id=staff.id,
        trace_id=_trace_id(request),
    )
    return ToolResult(
        ok=True,
        data=out,
        message="Escalation updated.",
        trace_id=_trace_id(request),
    )


@router.get("/staff/conversations")
async def api_staff_list_conversations(
    staff: StaffIdentity = Depends(
        require_staff_roles(
            StaffRole.RECEPTIONIST, StaffRole.MANAGER, StaffRole.ADMIN
        )
    ),
    session: AsyncSession = Depends(get_db_session),
) -> list[dict[str, Any]]:
    rows = (
        await session.execute(
            select(Conversation, Property.code)
            .outerjoin(Property, Property.id == Conversation.property_id)
            .order_by(Conversation.started_at.desc())
            .limit(50)
        )
    ).all()
    results = []
    for conv, prop_code in rows:
        if staff.role != StaffRole.ADMIN and staff.property_code and prop_code not in (None, staff.property_code):
            continue
        results.append(
            {
                "id": str(conv.id),
                "property_code": prop_code,
                "channel": conv.channel,
                "language": conv.language,
                "status": conv.status,
                "started_at": conv.started_at.isoformat(),
            }
        )
    return results


@router.get("/staff/audit-logs")
async def api_staff_audit_logs(
    limit: int = Query(default=50, ge=1, le=200),
    staff: StaffIdentity = Depends(
        require_staff_roles(StaffRole.MANAGER, StaffRole.ADMIN)
    ),
    session: AsyncSession = Depends(get_db_session),
) -> list[dict[str, Any]]:
    logs = (
        await session.scalars(
            select(AuditLog).order_by(AuditLog.created_at.desc()).limit(limit)
        )
    ).all()
    return [
        {
            "id": str(log.id),
            "trace_id": log.trace_id,
            "actor_type": log.actor_type,
            "actor_id": log.actor_id,
            "action": log.action,
            "entity_type": log.entity_type,
            "entity_id": log.entity_id,
            "before_state": log.before_state,
            "after_state": log.after_state,
            "created_at": log.created_at.isoformat(),
        }
        for log in logs
    ]
