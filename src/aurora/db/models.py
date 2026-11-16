"""SQLAlchemy 2.0 ORM models for the 15 Aurora domain tables."""

from datetime import date, datetime, time
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    Time,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from aurora.db.base import Base


class Property(Base):
    """Aurora hotel property (`SGN`, `DAD`, `PQC`)."""

    __tablename__ = "properties"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    code: Mapped[str] = mapped_column(String(10), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    city: Mapped[str] = mapped_column(String(80), nullable=False)
    timezone: Mapped[str] = mapped_column(
        String(64), nullable=False, default="Asia/Ho_Chi_Minh"
    )
    check_in_time: Mapped[time] = mapped_column(Time, nullable=False, default=time(14, 0))
    check_out_time: Mapped[time] = mapped_column(Time, nullable=False, default=time(12, 0))
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="VND")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    room_types: Mapped[list["RoomType"]] = relationship(back_populates="property")


class RoomType(Base):
    """Room category owned by a property."""

    __tablename__ = "room_types"
    __table_args__ = (
        UniqueConstraint("property_id", "code", name="uq_room_types_property_code"),
        CheckConstraint("max_adults >= 1", name="max_adults_positive"),
        CheckConstraint("max_children >= 0", name="max_children_non_negative"),
        CheckConstraint("total_rooms >= 1", name="total_rooms_positive"),
        CheckConstraint("base_rate_vnd > 0", name="base_rate_positive"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    property_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("properties.id", ondelete="CASCADE"), nullable=False
    )
    code: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    max_adults: Mapped[int] = mapped_column(Integer, nullable=False)
    max_children: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    total_rooms: Mapped[int] = mapped_column(Integer, nullable=False)
    base_rate_vnd: Mapped[Decimal] = mapped_column(Numeric(12, 0), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    property: Mapped["Property"] = relationship(back_populates="room_types")
    rate_plans: Mapped[list["RatePlan"]] = relationship(back_populates="room_type")


class RatePlan(Base):
    """Pricing and cancellation rules bound to a room type."""

    __tablename__ = "rate_plans"
    __table_args__ = (
        UniqueConstraint("room_type_id", "code", name="uq_rate_plans_room_type_code"),
        CheckConstraint("price_multiplier > 0", name="price_multiplier_positive"),
        CheckConstraint(
            "free_cancel_hours_before_checkin >= 0",
            name="free_cancel_hours_non_negative",
        ),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    room_type_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("room_types.id", ondelete="CASCADE"), nullable=False
    )
    code: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    price_multiplier: Mapped[Decimal] = mapped_column(Numeric(6, 4), nullable=False)
    is_refundable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    free_cancel_hours_before_checkin: Mapped[int] = mapped_column(
        Integer, nullable=False, default=24
    )
    breakfast_included: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    room_type: Mapped["RoomType"] = relationship(back_populates="rate_plans")


class RoomInventoryDaily(Base):
    """Daily lockable room inventory ledger (one row per room_type + stay_date)."""

    __tablename__ = "room_inventory_daily"
    __table_args__ = (
        UniqueConstraint("room_type_id", "stay_date", name="uq_inventory_room_type_date"),
        CheckConstraint("total_rooms >= 0", name="inventory_total_non_negative"),
        CheckConstraint("reserved_rooms >= 0", name="inventory_reserved_non_negative"),
        CheckConstraint("blocked_rooms >= 0", name="inventory_blocked_non_negative"),
        CheckConstraint(
            "(reserved_rooms + blocked_rooms) <= total_rooms",
            name="inventory_no_overbooking",
        ),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    room_type_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("room_types.id", ondelete="CASCADE"), nullable=False
    )
    stay_date: Mapped[date] = mapped_column(Date, nullable=False)
    total_rooms: Mapped[int] = mapped_column(Integer, nullable=False)
    reserved_rooms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    blocked_rooms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    price_override_vnd: Mapped[Decimal | None] = mapped_column(Numeric(12, 0), nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class Guest(Base):
    """Guest contact profile linked to reservations and conversations."""

    __tablename__ = "guests"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    full_name: Mapped[str] = mapped_column(String(150), nullable=False)
    email: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    phone_e164: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    preferred_language: Mapped[str] = mapped_column(String(5), nullable=False, default="vi")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Reservation(Base):
    """Single-room reservation record governed by the Aurora state machine."""

    __tablename__ = "reservations"
    __table_args__ = (
        CheckConstraint("check_out_date > check_in_date", name="checkout_after_checkin"),
        CheckConstraint("adults >= 1", name="adults_positive"),
        CheckConstraint("children >= 0", name="children_non_negative"),
        CheckConstraint("total_amount_vnd >= 0", name="total_amount_non_negative"),
        CheckConstraint(
            "status IN ('PENDING_CONFIRMATION', 'CONFIRMED', 'CHECKED_IN', "
            "'CHECKED_OUT', 'CANCELLED', 'NO_SHOW', 'EXPIRED')",
            name="valid_reservation_status",
        ),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    confirmation_code: Mapped[str] = mapped_column(String(24), nullable=False, unique=True)
    property_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("properties.id", ondelete="RESTRICT"), nullable=False
    )
    room_type_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("room_types.id", ondelete="RESTRICT"), nullable=False
    )
    rate_plan_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("rate_plans.id", ondelete="RESTRICT"), nullable=False
    )
    guest_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("guests.id", ondelete="RESTRICT"), nullable=False
    )
    check_in_date: Mapped[date] = mapped_column(Date, nullable=False)
    check_out_date: Mapped[date] = mapped_column(Date, nullable=False)
    adults: Mapped[int] = mapped_column(Integer, nullable=False)
    children: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="CONFIRMED")
    total_amount_vnd: Mapped[Decimal] = mapped_column(Numeric(14, 0), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="VND")
    special_requests: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_channel: Mapped[str] = mapped_column(String(32), nullable=False, default="web_chat")
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    nights: Mapped[list["ReservationNight"]] = relationship(
        back_populates="reservation", cascade="all, delete-orphan"
    )


class ReservationNight(Base):
    """Per-night breakdown and locked rate snapshot for a reservation."""

    __tablename__ = "reservation_nights"
    __table_args__ = (
        UniqueConstraint("reservation_id", "stay_date", name="uq_res_nights_res_date"),
        CheckConstraint("nightly_rate_vnd >= 0", name="nightly_rate_non_negative"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    reservation_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("reservations.id", ondelete="CASCADE"),
        nullable=False,
    )
    room_type_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("room_types.id", ondelete="RESTRICT"), nullable=False
    )
    stay_date: Mapped[date] = mapped_column(Date, nullable=False)
    nightly_rate_vnd: Mapped[Decimal] = mapped_column(Numeric(12, 0), nullable=False)

    reservation: Mapped["Reservation"] = relationship(back_populates="nights")


class StaffUser(Base):
    """Authenticated hotel staff account with property/role scope."""

    __tablename__ = "staff_users"
    __table_args__ = (
        CheckConstraint(
            "role IN ('RECEPTIONIST', 'MANAGER', 'ADMIN')",
            name="valid_staff_role",
        ),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str] = mapped_column(String(150), nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    property_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("properties.id", ondelete="SET NULL"), nullable=True
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Conversation(Base):
    """Guest chat or voice conversation session."""

    __tablename__ = "conversations"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    property_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("properties.id", ondelete="SET NULL"), nullable=True
    )
    guest_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("guests.id", ondelete="SET NULL"), nullable=True
    )
    channel: Mapped[str] = mapped_column(String(32), nullable=False, default="web_chat")
    language: Mapped[str] = mapped_column(String(5), nullable=False, default="vi")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="ACTIVE")
    external_session_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    messages: Mapped[list["ConversationMessage"]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan"
    )


class ConversationMessage(Base):
    """Individual utterance, agent reply, or tool trace inside a conversation."""

    __tablename__ = "conversation_messages"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    conversation_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    tool_calls: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    trace_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    conversation: Mapped["Conversation"] = relationship(back_populates="messages")


class SupportTicket(Base):
    """Housekeeping, airport transfer, complaint, or special-request ticket."""

    __tablename__ = "support_tickets"
    __table_args__ = (
        CheckConstraint(
            "category IN ('HOUSEKEEPING', 'AIRPORT_TRANSFER', 'COMPLAINT', 'SPECIAL_REQUEST')",
            name="valid_ticket_category",
        ),
        CheckConstraint(
            "priority IN ('LOW', 'NORMAL', 'HIGH', 'URGENT')",
            name="valid_ticket_priority",
        ),
        CheckConstraint(
            "status IN ('OPEN', 'IN_PROGRESS', 'RESOLVED', 'CANCELLED')",
            name="valid_ticket_status",
        ),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    ticket_code: Mapped[str] = mapped_column(String(24), nullable=False, unique=True)
    property_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("properties.id", ondelete="RESTRICT"), nullable=False
    )
    reservation_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("reservations.id", ondelete="SET NULL"), nullable=True
    )
    conversation_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("conversations.id", ondelete="SET NULL"), nullable=True
    )
    category: Mapped[str] = mapped_column(String(32), nullable=False)
    priority: Mapped[str] = mapped_column(String(16), nullable=False, default="NORMAL")
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="OPEN")
    description: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class Escalation(Base):
    """Human handoff escalation with full context snapshot for staff."""

    __tablename__ = "escalations"
    __table_args__ = (
        CheckConstraint(
            "priority IN ('NORMAL', 'HIGH', 'URGENT')",
            name="valid_escalation_priority",
        ),
        CheckConstraint(
            "status IN ('OPEN', 'ASSIGNED', 'RESOLVED')",
            name="valid_escalation_status",
        ),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    escalation_code: Mapped[str] = mapped_column(String(24), nullable=False, unique=True)
    conversation_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    )
    property_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("properties.id", ondelete="SET NULL"), nullable=True
    )
    reservation_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("reservations.id", ondelete="SET NULL"), nullable=True
    )
    assigned_staff_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("staff_users.id", ondelete="SET NULL"), nullable=True
    )
    reason_code: Mapped[str] = mapped_column(String(64), nullable=False)
    priority: Mapped[str] = mapped_column(String(16), nullable=False, default="HIGH")
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="OPEN")
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    context_snapshot: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class IdempotencyRecord(Base):
    """Deduplication log protecting mutating booking/ticket/escalation calls."""

    __tablename__ = "idempotency_records"
    __table_args__ = (
        UniqueConstraint(
            "actor_scope",
            "endpoint",
            "idempotency_key",
            name="uq_idempotency_actor_endpoint_key",
        ),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    actor_scope: Mapped[str] = mapped_column(String(64), nullable=False)
    endpoint: Mapped[str] = mapped_column(String(128), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    response_status: Mapped[int] = mapped_column(Integer, nullable=False)
    response_body: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class AuditLog(Base):
    """Immutable audit trail for every state transition and staff override."""

    __tablename__ = "audit_logs"
    __table_args__ = (
        Index("ix_audit_logs_entity", "entity_type", "entity_id"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    trace_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    actor_type: Mapped[str] = mapped_column(String(32), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(128), nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(64), nullable=False)
    before_state: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    after_state: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class PolicyChunk(Base):
    """Vector-indexed hotel policy chunk filtered by property_code and language."""

    __tablename__ = "policy_chunks"
    __table_args__ = (
        UniqueConstraint(
            "property_code",
            "doc_id",
            "language",
            name="uq_policy_chunks_prop_doc_lang",
        ),
        Index("ix_policy_chunks_prop_lang", "property_code", "language"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    property_code: Mapped[str] = mapped_column(String(16), nullable=False)
    doc_id: Mapped[str] = mapped_column(String(120), nullable=False)
    section_title: Mapped[str] = mapped_column(String(200), nullable=False)
    language: Mapped[str] = mapped_column(String(5), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[list[float]] = mapped_column(Vector(384), nullable=False)
    chunk_metadata: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, nullable=False, default=dict
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
