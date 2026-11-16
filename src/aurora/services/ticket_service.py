"""Support ticket management service with idempotency and audit logs."""

from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aurora.db.models import Property, Reservation, SupportTicket
from aurora.domain.enums import TicketCategory, TicketPriority, TicketStatus
from aurora.domain.errors import DomainError
from aurora.domain.schemas import (
    CreateTicketRequest,
    TicketOut,
    UpdateTicketStatusRequest,
)
from aurora.services.audit import record_audit_log
from aurora.services.idempotency import (
    check_idempotency,
    compute_request_hash,
    save_idempotency_record,
)


def _to_ticket_out(ticket: SupportTicket, property_code: str) -> TicketOut:
    return TicketOut(
        id=ticket.id,
        ticket_code=ticket.ticket_code,
        property_code=property_code,
        reservation_id=ticket.reservation_id,
        conversation_id=ticket.conversation_id,
        category=TicketCategory(ticket.category),
        priority=TicketPriority(ticket.priority),
        status=TicketStatus(ticket.status),
        description=ticket.description,
        created_at=ticket.created_at,
        updated_at=ticket.updated_at,
    )


async def create_support_ticket(
    session: AsyncSession,
    req: CreateTicketRequest,
    idempotency_key: str,
    actor_type: str = "AI_AGENT",
    actor_id: str = "aurora-agent",
    trace_id: str | None = None,
) -> TicketOut:
    """Create a support ticket in PostgreSQL protected by idempotency and audit logging."""
    trace = trace_id or uuid4().hex
    actor_scope = f"{actor_type}:{actor_id}"
    endpoint = "POST:/api/v1/tickets"
    req_hash = compute_request_hash(req)

    try:
        cached = await check_idempotency(
            session=session,
            actor_scope=actor_scope,
            endpoint=endpoint,
            idempotency_key=idempotency_key,
            request_hash=req_hash,
        )
        if cached is not None:
            return TicketOut.model_validate(cached)

        prop_code = req.property_code.strip().upper()
        prop = await session.scalar(select(Property).where(Property.code == prop_code))
        if prop is None:
            raise DomainError(
                error_code="PROPERTY_NOT_FOUND",
                message=f"Unknown property code '{prop_code}'.",
                status_code=404,
            )

        reservation_id: UUID | None = None
        if req.confirmation_code:
            res = await session.scalar(
                select(Reservation).where(
                    Reservation.confirmation_code == req.confirmation_code.strip().upper()
                )
            )
            if res is None:
                raise DomainError(
                    error_code="RESERVATION_NOT_FOUND",
                    message=f"Reservation '{req.confirmation_code}' was not found.",
                    status_code=404,
                )
            reservation_id = res.id

        ticket_code = f"TCK-{prop.code}-{uuid4().hex[:6].upper()}"
        ticket = SupportTicket(
            ticket_code=ticket_code,
            property_id=prop.id,
            reservation_id=reservation_id,
            conversation_id=req.conversation_id,
            category=req.category.value,
            priority=req.priority.value,
            status=TicketStatus.OPEN.value,
            description=req.description.strip(),
        )
        session.add(ticket)
        await session.flush()

        out = _to_ticket_out(ticket, prop.code)
        await record_audit_log(
            session=session,
            trace_id=trace,
            actor_type=actor_type,
            actor_id=actor_id,
            action="TICKET_CREATED",
            entity_type="support_ticket",
            entity_id=str(ticket.id),
            before_state=None,
            after_state=out.model_dump(mode="json"),
        )
        await save_idempotency_record(
            session=session,
            actor_scope=actor_scope,
            endpoint=endpoint,
            idempotency_key=idempotency_key,
            request_hash=req_hash,
            response_status=201,
            response_body=out.model_dump(mode="json"),
        )
        await session.commit()
        return out
    except Exception:
        await session.rollback()
        raise


async def list_support_tickets(
    session: AsyncSession,
    property_code: str | None = None,
    status: TicketStatus | None = None,
) -> list[TicketOut]:
    """List support tickets optionally filtered by property or status."""
    stmt = (
        select(SupportTicket, Property.code)
        .join(Property, Property.id == SupportTicket.property_id)
        .order_by(SupportTicket.created_at.desc())
    )
    if property_code:
        stmt = stmt.where(Property.code == property_code.strip().upper())
    if status:
        stmt = stmt.where(SupportTicket.status == status.value)

    rows = (await session.execute(stmt)).all()
    return [_to_ticket_out(ticket, prop_code) for ticket, prop_code in rows]


async def update_ticket_status(
    session: AsyncSession,
    ticket_code: str,
    req: UpdateTicketStatusRequest,
    staff_id: str,
    trace_id: str | None = None,
) -> TicketOut:
    """Update a support ticket status (staff operation)."""
    trace = trace_id or uuid4().hex
    try:
        row = (
            await session.execute(
                select(SupportTicket, Property.code)
                .join(Property, Property.id == SupportTicket.property_id)
                .where(SupportTicket.ticket_code == ticket_code.strip().upper())
                .with_for_update()
            )
        ).first()
        if row is None:
            raise DomainError(
                error_code="TICKET_NOT_FOUND",
                message=f"Support ticket '{ticket_code}' was not found.",
                status_code=404,
            )

        ticket, prop_code = row
        before = _to_ticket_out(ticket, prop_code).model_dump(mode="json")
        ticket.status = req.status.value
        await session.flush()
        await session.refresh(ticket, attribute_names=["updated_at"])
        after_out = _to_ticket_out(ticket, prop_code)

        await record_audit_log(
            session=session,
            trace_id=trace,
            actor_type="STAFF",
            actor_id=staff_id,
            action="TICKET_UPDATED",
            entity_type="support_ticket",
            entity_id=str(ticket.id),
            before_state=before,
            after_state={**after_out.model_dump(mode="json"), "note": req.note},
        )
        await session.commit()
        return after_out
    except Exception:
        await session.rollback()
        raise
