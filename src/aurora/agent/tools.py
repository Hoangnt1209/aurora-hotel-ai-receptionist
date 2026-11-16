"""Structured Pydantic tool wrappers calling Aurora deterministic M2/M3 services."""

from datetime import date
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from aurora.domain.enums import (
    ActorType,
    EscalationPriority,
    TicketCategory,
    TicketPriority,
)
from aurora.domain.errors import DomainError
from aurora.domain.schemas import (
    AvailabilityQuery,
    CancelReservationRequest,
    CreateEscalationRequest,
    CreateReservationRequest,
    CreateTicketRequest,
    ModifyReservationRequest,
    ToolResult,
)
from aurora.rag.retriever import search_hotel_policy
from aurora.services.booking_service import (
    cancel_reservation,
    check_availability,
    create_reservation,
    modify_reservation,
)
from aurora.services.escalation_service import create_escalation
from aurora.services.ticket_service import create_support_ticket
from aurora.services.verification import (
    validate_verification_token,
    verify_guest_reservation,
)


def _err(error_code: str, message: str, trace_id: str) -> ToolResult[dict[str, Any]]:
    return ToolResult(
        ok=False,
        data=None,
        error_code=error_code,
        message=message,
        trace_id=trace_id,
    )


async def check_availability_tool(
    session: AsyncSession,
    *,
    property_code: str,
    check_in_date: date,
    check_out_date: date,
    adults: int = 2,
    children: int = 0,
    rooms_count: int = 1,
    room_type_code: str | None = None,
    trace_id: str,
) -> ToolResult[dict[str, Any]]:
    try:
        res = await check_availability(
            session,
            AvailabilityQuery(
                property_code=property_code,
                check_in_date=check_in_date,
                check_out_date=check_out_date,
                adults=adults,
                children=children,
                rooms_count=rooms_count,
                room_type_code=room_type_code,
            ),
        )
        return ToolResult(
            ok=True,
            data=res.model_dump(mode="json"),
            message="Availability retrieved from PostgreSQL.",
            trace_id=trace_id,
        )
    except DomainError as exc:
        return _err(exc.error_code, exc.message, trace_id)


async def create_reservation_tool(
    session: AsyncSession,
    *,
    req: CreateReservationRequest,
    idempotency_key: str,
    trace_id: str,
) -> ToolResult[dict[str, Any]]:
    try:
        res = await create_reservation(
            session,
            req=req,
            idempotency_key=idempotency_key,
            actor_type=ActorType.AI_AGENT.value,
            actor_id="langgraph-receptionist",
            trace_id=trace_id,
        )
        return ToolResult(
            ok=True,
            data=res.model_dump(mode="json"),
            message="Reservation created and confirmed.",
            trace_id=trace_id,
        )
    except DomainError as exc:
        return _err(exc.error_code, exc.message, trace_id)


async def verify_guest_reservation_tool(
    session: AsyncSession,
    *,
    confirmation_code: str,
    phone_or_email: str,
    conversation_id: UUID | None,
    trace_id: str,
) -> ToolResult[dict[str, Any]]:
    try:
        verified = await verify_guest_reservation(
            session,
            confirmation_code=confirmation_code,
            phone_or_email=phone_or_email,
            conversation_id=conversation_id,
        )
        return ToolResult(
            ok=True,
            data=verified.model_dump(mode="json"),
            message="Guest identity verified.",
            trace_id=trace_id,
        )
    except DomainError as exc:
        return _err(exc.error_code, exc.message, trace_id)


async def get_reservation_details_tool(
    session: AsyncSession,
    *,
    confirmation_code: str,
    verification_token: str | None,
    conversation_id: UUID | None,
    trace_id: str,
) -> ToolResult[dict[str, Any]]:
    if not validate_verification_token(
        verification_token, confirmation_code, conversation_id
    ):
        return _err(
            "IDENTITY_VERIFICATION_FAILED",
            "Verification token is missing or invalid for this reservation.",
            trace_id,
        )
    # Reuse listing lookup after token verification
    from aurora.services.booking_service import list_reservations

    matches = [
        r
        for r in await list_reservations(session)
        if r.confirmation_code == confirmation_code.strip().upper()
    ]
    if not matches:
        return _err("RESERVATION_NOT_FOUND", "Reservation was not found.", trace_id)
    return ToolResult(
        ok=True,
        data=matches[0].model_dump(mode="json"),
        message="Verified reservation details retrieved.",
        trace_id=trace_id,
    )


async def modify_reservation_tool(
    session: AsyncSession,
    *,
    req: ModifyReservationRequest,
    idempotency_key: str,
    trace_id: str,
) -> ToolResult[dict[str, Any]]:
    try:
        res = await modify_reservation(
            session,
            req=req,
            idempotency_key=idempotency_key,
            actor_type=ActorType.AI_AGENT.value,
            actor_id="langgraph-receptionist",
            trace_id=trace_id,
        )
        return ToolResult(
            ok=True,
            data=res.model_dump(mode="json"),
            message="Reservation modified.",
            trace_id=trace_id,
        )
    except DomainError as exc:
        return _err(exc.error_code, exc.message, trace_id)


async def cancel_reservation_tool(
    session: AsyncSession,
    *,
    req: CancelReservationRequest,
    idempotency_key: str,
    trace_id: str,
) -> ToolResult[dict[str, Any]]:
    try:
        res = await cancel_reservation(
            session,
            req=req,
            idempotency_key=idempotency_key,
            actor_type=ActorType.AI_AGENT.value,
            actor_id="langgraph-receptionist",
            trace_id=trace_id,
        )
        return ToolResult(
            ok=True,
            data=res.model_dump(mode="json"),
            message="Reservation cancelled.",
            trace_id=trace_id,
        )
    except DomainError as exc:
        return _err(exc.error_code, exc.message, trace_id)


async def search_hotel_policy_tool(
    session: AsyncSession,
    *,
    query: str,
    property_code: str = "GROUP",
    language: str | None = None,
    trace_id: str,
) -> ToolResult[dict[str, Any]]:
    res = await search_hotel_policy(
        session,
        query=query,
        property_code=property_code,
        language=language,
        top_k=3,
    )
    return ToolResult(
        ok=True,
        data=res.model_dump(mode="json"),
        message="Policy search completed.",
        trace_id=trace_id,
    )


async def create_support_ticket_tool(
    session: AsyncSession,
    *,
    property_code: str,
    category: TicketCategory,
    description: str,
    priority: TicketPriority = TicketPriority.NORMAL,
    confirmation_code: str | None = None,
    conversation_id: UUID | None = None,
    idempotency_key: str,
    trace_id: str,
) -> ToolResult[dict[str, Any]]:
    try:
        ticket = await create_support_ticket(
            session,
            req=CreateTicketRequest(
                property_code=property_code,
                category=category,
                priority=priority,
                description=description,
                confirmation_code=confirmation_code,
                conversation_id=conversation_id,
            ),
            idempotency_key=idempotency_key,
            actor_type=ActorType.AI_AGENT.value,
            actor_id="langgraph-receptionist",
            trace_id=trace_id,
        )
        return ToolResult(
            ok=True,
            data=ticket.model_dump(mode="json"),
            message="Support ticket created.",
            trace_id=trace_id,
        )
    except DomainError as exc:
        return _err(exc.error_code, exc.message, trace_id)


async def escalate_to_human_tool(
    session: AsyncSession,
    *,
    conversation_id: UUID,
    reason_code: str,
    summary: str,
    property_code: str | None = None,
    confirmation_code: str | None = None,
    priority: EscalationPriority = EscalationPriority.HIGH,
    context_snapshot: dict[str, Any] | None = None,
    idempotency_key: str,
    trace_id: str,
) -> ToolResult[dict[str, Any]]:
    try:
        esc = await create_escalation(
            session,
            req=CreateEscalationRequest(
                conversation_id=conversation_id,
                property_code=property_code,
                confirmation_code=confirmation_code,
                reason_code=reason_code,
                priority=priority,
                summary=summary,
                context_snapshot=context_snapshot or {},
            ),
            idempotency_key=idempotency_key,
            actor_type=ActorType.AI_AGENT.value,
            actor_id="langgraph-receptionist",
            trace_id=trace_id,
        )
        return ToolResult(
            ok=True,
            data=esc.model_dump(mode="json"),
            message="Conversation escalated to human receptionist.",
            trace_id=trace_id,
        )
    except DomainError as exc:
        return _err(exc.error_code, exc.message, trace_id)
