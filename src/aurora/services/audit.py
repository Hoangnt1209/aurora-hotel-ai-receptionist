"""Immutable audit log writer for all domain state transitions."""

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from aurora.db.models import AuditLog


async def record_audit_log(
    session: AsyncSession,
    trace_id: str,
    actor_type: str,
    actor_id: str,
    action: str,
    entity_type: str,
    entity_id: str,
    before_state: dict[str, Any] | None = None,
    after_state: dict[str, Any] | None = None,
) -> AuditLog:
    """Insert an immutable audit log entry inside the current transaction."""
    entry = AuditLog(
        trace_id=trace_id,
        actor_type=actor_type,
        actor_id=actor_id,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        before_state=before_state,
        after_state=after_state,
    )
    session.add(entry)
    return entry
