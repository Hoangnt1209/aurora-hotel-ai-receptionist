"""Idempotency key enforcement backed by PostgreSQL."""

import hashlib
import json
from typing import Any

from pydantic import BaseModel
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from aurora.db.models import IdempotencyRecord
from aurora.domain.errors import DomainError


def compute_request_hash(payload: BaseModel | dict[str, Any]) -> str:
    """Compute canonical SHA-256 hash of a request payload."""
    if isinstance(payload, BaseModel):
        raw = payload.model_dump(mode="json")
    else:
        raw = payload
    encoded = json.dumps(raw, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


async def check_idempotency(
    session: AsyncSession,
    actor_scope: str,
    endpoint: str,
    idempotency_key: str,
    request_hash: str,
) -> dict[str, Any] | None:
    """Check and lock an idempotency slot; return cached body on replay or raise on conflict."""
    cleaned_key = idempotency_key.strip()
    if not cleaned_key:
        raise DomainError(
            error_code="MISSING_IDEMPOTENCY_KEY",
            message="Idempotency-Key must be a non-empty string.",
            status_code=400,
        )

    lock_target = f"{actor_scope}:{endpoint}:{cleaned_key}"
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:lock_target, 0))"),
        {"lock_target": lock_target},
    )

    existing = await session.scalar(
        select(IdempotencyRecord).where(
            IdempotencyRecord.actor_scope == actor_scope,
            IdempotencyRecord.endpoint == endpoint,
            IdempotencyRecord.idempotency_key == cleaned_key,
        )
    )
    if existing is None:
        return None

    if existing.request_hash != request_hash:
        raise DomainError(
            error_code="IDEMPOTENCY_CONFLICT",
            message="Idempotency-Key was already used with a different request payload.",
            status_code=409,
        )

    return existing.response_body


async def save_idempotency_record(
    session: AsyncSession,
    actor_scope: str,
    endpoint: str,
    idempotency_key: str,
    request_hash: str,
    response_status: int,
    response_body: dict[str, Any],
) -> None:
    """Persist an idempotency record within the active database transaction."""
    record = IdempotencyRecord(
        actor_scope=actor_scope,
        endpoint=endpoint,
        idempotency_key=idempotency_key.strip(),
        request_hash=request_hash,
        response_status=response_status,
        response_body=response_body,
    )
    session.add(record)
