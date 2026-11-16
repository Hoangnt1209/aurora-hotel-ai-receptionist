"""Pydantic v2 contracts for Aurora booking, ticket, escalation, and tool envelopes."""

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Generic, TypeVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from aurora.domain.enums import (
    EscalationPriority,
    EscalationStatus,
    ReservationStatus,
    TicketCategory,
    TicketPriority,
    TicketStatus,
)

T = TypeVar("T")


class ToolResult(BaseModel, Generic[T]):
    """Standard structured envelope returned by every Aurora domain tool and API."""

    ok: bool
    data: T | None = None
    error_code: str | None = None
    message: str
    trace_id: str


class AvailabilityQuery(BaseModel):
    """Input criteria for checking live PostgreSQL room availability and rates."""

    model_config = ConfigDict(extra="forbid")

    property_code: str = Field(min_length=2, max_length=10)
    check_in_date: date
    check_out_date: date
    adults: int = Field(default=2, ge=1, le=10)
    children: int = Field(default=0, ge=0, le=10)
    rooms_count: int = Field(default=1, ge=1, le=50)
    room_type_code: str | None = None


class NightlyBreakdown(BaseModel):
    stay_date: date
    nightly_rate_vnd: Decimal
    available_rooms: int


class RatePlanQuote(BaseModel):
    rate_plan_code: str
    rate_plan_name: str
    is_refundable: bool
    free_cancel_hours_before_checkin: int
    breakfast_included: bool
    subtotal_vnd: Decimal
    tax_and_service_vnd: Decimal
    total_amount_vnd: Decimal
    nightly_breakdown: list[NightlyBreakdown]


class RoomQuoteOption(BaseModel):
    property_code: str
    property_name: str
    room_type_code: str
    room_type_name: str
    description: str | None
    max_adults: int
    max_children: int
    min_available_rooms: int
    rate_plans: list[RatePlanQuote]


class AvailabilityResponse(BaseModel):
    property_code: str
    property_name: str
    check_in_date: date
    check_out_date: date
    nights: int
    currency: str = "VND"
    options: list[RoomQuoteOption]


class CreateReservationRequest(BaseModel):
    """Validated input for creating a single-room reservation (server computes code & price)."""

    model_config = ConfigDict(extra="forbid")

    property_code: str = Field(min_length=2, max_length=10)
    room_type_code: str = Field(min_length=2, max_length=32)
    rate_plan_code: str = Field(default="FLEX_24H", min_length=2, max_length=32)
    check_in_date: date
    check_out_date: date
    adults: int = Field(default=2, ge=1, le=10)
    children: int = Field(default=0, ge=0, le=10)
    rooms_count: int = Field(default=1, ge=1, le=50)
    guest_full_name: str = Field(min_length=2, max_length=150)
    guest_email: EmailStr
    guest_phone: str = Field(min_length=8, max_length=32)
    preferred_language: str = Field(default="vi", pattern="^(vi|en)$")
    special_requests: str | None = Field(default=None, max_length=1000)
    source_channel: str = Field(default="web_chat", max_length=32)


class ReservationNightOut(BaseModel):
    stay_date: date
    nightly_rate_vnd: Decimal


class ReservationOut(BaseModel):
    id: UUID
    confirmation_code: str
    property_code: str
    property_name: str
    room_type_code: str
    room_type_name: str
    rate_plan_code: str
    rate_plan_name: str
    is_refundable: bool
    check_in_date: date
    check_out_date: date
    adults: int
    children: int
    status: ReservationStatus
    total_amount_vnd: Decimal
    currency: str
    special_requests: str | None
    source_channel: str
    guest_full_name: str
    guest_email: str
    guest_phone_e164: str
    nights: list[ReservationNightOut]
    created_at: datetime
    updated_at: datetime


class VerifyGuestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirmation_code: str = Field(min_length=5, max_length=24)
    phone_or_email: str = Field(min_length=4, max_length=255)
    conversation_id: UUID | None = None


class VerifyGuestResponse(BaseModel):
    verified: bool
    confirmation_code: str
    verification_token: str
    expires_at: datetime
    reservation: ReservationOut


class ModifyReservationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirmation_code: str = Field(min_length=5, max_length=24)
    verification_token: str | None = None
    conversation_id: UUID | None = None
    new_check_in_date: date | None = None
    new_check_out_date: date | None = None
    new_room_type_code: str | None = None
    new_adults: int | None = Field(default=None, ge=1, le=10)
    new_children: int | None = Field(default=None, ge=0, le=10)
    new_special_requests: str | None = Field(default=None, max_length=1000)


class CancelReservationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirmation_code: str = Field(min_length=5, max_length=24)
    verification_token: str | None = None
    conversation_id: UUID | None = None
    reason: str | None = Field(default=None, max_length=500)
    force_override: bool = False


class StaffStatusTransitionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_status: ReservationStatus
    reason: str | None = Field(default=None, max_length=500)


class CreateTicketRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    property_code: str = Field(min_length=2, max_length=10)
    category: TicketCategory
    priority: TicketPriority = TicketPriority.NORMAL
    description: str = Field(min_length=5, max_length=2000)
    confirmation_code: str | None = None
    conversation_id: UUID | None = None


class TicketOut(BaseModel):
    id: UUID
    ticket_code: str
    property_code: str
    reservation_id: UUID | None
    conversation_id: UUID | None
    category: TicketCategory
    priority: TicketPriority
    status: TicketStatus
    description: str
    created_at: datetime
    updated_at: datetime


class UpdateTicketStatusRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: TicketStatus
    note: str | None = Field(default=None, max_length=500)


class CreateEscalationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conversation_id: UUID
    property_code: str | None = None
    confirmation_code: str | None = None
    reason_code: str = Field(min_length=3, max_length=64)
    priority: EscalationPriority = EscalationPriority.HIGH
    summary: str = Field(min_length=5, max_length=2000)
    context_snapshot: dict[str, Any] = Field(default_factory=dict)


class EscalationOut(BaseModel):
    id: UUID
    escalation_code: str
    conversation_id: UUID
    property_code: str | None
    reservation_id: UUID | None
    assigned_staff_id: UUID | None
    reason_code: str
    priority: EscalationPriority
    status: EscalationStatus
    summary: str
    context_snapshot: dict[str, Any]
    created_at: datetime
    resolved_at: datetime | None


class ResolveEscalationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: EscalationStatus = EscalationStatus.RESOLVED
    resolution_note: str | None = Field(default=None, max_length=1000)
