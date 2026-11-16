"""Human escalation handoff service preserving full conversation context."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aurora.db.models import (
    Conversation,
    ConversationMessage,
    Escalation,
    Property,
    Reservation,
)
from aurora.domain.enums import EscalationPriority, EscalationStatus
from aurora.domain.errors import DomainError
from aurora.domain.schemas import (
    CreateEscalationRequest,
    EscalationOut,
    ResolveEscalationRequest,
)
from aurora.services.audit import record_audit_log
from aurora.services.idempotency import (
    check_idempotency,
    compute_request_hash,
    save_idempotency_record,
)


def _to_escalation_out(
    esc: Escalation, property_code: str | None
) -> EscalationOut:
    return EscalationOut(
        id=esc.id,
        escalation_code=esc.escalation_code,
        conversation_id=esc.conversation_id,
        property_code=property_code,
        reservation_id=esc.reservation_id,
        assigned_staff_id=esc.assigned_staff_id,
        reason_code=esc.reason_code,
        priority=EscalationPriority(esc.priority),
        status=EscalationStatus(esc.status),
        summary=esc.summary,
        context_snapshot=esc.context_snapshot,
        created_at=esc.created_at,
        resolved_at=esc.resolved_at,
    )


async def create_escalation(
    session: AsyncSession,
    req: CreateEscalationRequest,
    idempotency_key: str,
    actor_type: str = "AI_AGENT",
    actor_id: str = "aurora-agent",
    trace_id: str | None = None,
) -> EscalationOut:
    """Create a human handoff escalation capturing recent conversation messages."""
    trace = trace_id or uuid4().hex
    actor_scope = f"{actor_type}:{actor_id}"
    endpoint = "POST:/api/v1/escalations"
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
            return EscalationOut.model_validate(cached)

        conv = await session.scalar(
            select(Conversation).where(Conversation.id == req.conversation_id)
        )
        if conv is None:
            raise DomainError(
                error_code="CONVERSATION_NOT_FOUND",
                message=f"Conversation '{req.conversation_id}' was not found.",
                status_code=404,
            )

        prop_id: UUID | None = conv.property_id
        prop_code_out: str | None = None
        if req.property_code:
            prop = await session.scalar(
                select(Property).where(Property.code == req.property_code.strip().upper())
            )
            if prop is None:
                raise DomainError(
                    error_code="PROPERTY_NOT_FOUND",
                    message=f"Unknown property code '{req.property_code}'.",
                    status_code=404,
                )
            prop_id = prop.id
            prop_code_out = prop.code
        elif prop_id is not None:
            prop = await session.scalar(select(Property).where(Property.id == prop_id))
            prop_code_out = prop.code if prop else None

        reservation_id: UUID | None = None
        if req.confirmation_code:
            res = await session.scalar(
                select(Reservation).where(
                    Reservation.confirmation_code == req.confirmation_code.strip().upper()
                )
            )
            if res is not None:
                reservation_id = res.id

        # Capture up to 20 recent conversation messages in snapshot
        msgs = (
            await session.scalars(
                select(ConversationMessage)
                .where(ConversationMessage.conversation_id == conv.id)
                .order_by(ConversationMessage.created_at.asc())
                .limit(20)
            )
        ).all()

        snapshot = {
            **req.context_snapshot,
            "channel": conv.channel,
            "language": conv.language,
            "recent_messages": [
                {
                    "role": m.role,
                    "content": m.content,
                    "created_at": m.created_at.isoformat(),
                }
                for m in msgs
            ],
        }

        code_prefix = prop_code_out or "GRP"
        escalation_code = f"ESC-{code_prefix}-{uuid4().hex[:6].upper()}"
        escalation = Escalation(
            escalation_code=escalation_code,
            conversation_id=conv.id,
            property_id=prop_id,
            reservation_id=reservation_id,
            reason_code=req.reason_code.strip().upper(),
            priority=req.priority.value,
            status=EscalationStatus.OPEN.value,
            summary=req.summary.strip(),
            context_snapshot=snapshot,
        )
        session.add(escalation)
        conv.status = "ESCALATED"
        await session.flush()

        out = _to_escalation_out(escalation, prop_code_out)
        await record_audit_log(
            session=session,
            trace_id=trace,
            actor_type=actor_type,
            actor_id=actor_id,
            action="ESCALATION_CREATED",
            entity_type="escalation",
            entity_id=str(escalation.id),
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


async def list_escalations(
    session: AsyncSession,
    status: EscalationStatus | None = None,
) -> list[EscalationOut]:
    """List escalations ordered newest first."""
    stmt = (
        select(Escalation, Property.code)
        .outerjoin(Property, Property.id == Escalation.property_id)
        .order_by(Escalation.created_at.desc())
    )
    if status:
        stmt = stmt.where(Escalation.status == status.value)
    rows = (await session.execute(stmt)).all()
    return [_to_escalation_out(esc, prop_code) for esc, prop_code in rows]


async def resolve_escalation(
    session: AsyncSession,
    escalation_code: str,
    req: ResolveEscalationRequest,
    staff_id: UUID,
    trace_id: str | None = None,
) -> EscalationOut:
    """Assign or resolve an escalation (staff operation)."""
    trace = trace_id or uuid4().hex
    try:
        row = (
            await session.execute(
                select(Escalation, Property.code)
                .outerjoin(Property, Property.id == Escalation.property_id)
                .where(Escalation.escalation_code == escalation_code.strip().upper())
                .with_for_update()
            )
        ).first()
        if row is None:
            raise DomainError(
                error_code="ESCALATION_NOT_FOUND",
                message=f"Escalation '{escalation_code}' was not found.",
                status_code=404,
            )

        esc, prop_code = row
        before = _to_escalation_out(esc, prop_code).model_dump(mode="json")
        esc.assigned_staff_id = staff_id
        esc.status = req.status.value
        if req.status == EscalationStatus.RESOLVED:
            esc.resolved_at = datetime.now(UTC)
        if req.resolution_note:
            esc.context_snapshot = {
                **esc.context_snapshot,
                "resolution_note": req.resolution_note,
            }
        await session.flush()

        after_out = _to_escalation_out(esc, prop_code)
        await record_audit_log(
            session=session,
            trace_id=trace,
            actor_type="STAFF",
            actor_id=str(staff_id),
            action="ESCALATION_UPDATED",
            entity_type="escalation",
            entity_id=str(esc.id),
            before_state=before,
            after_state=after_out.model_dump(mode="json"),
        )
        await session.commit()
        return after_out
    except Exception:
        await session.rollback()
        raise
