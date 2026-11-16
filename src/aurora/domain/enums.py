"""Canonical domain enumerations for Aurora Hospitality Group."""

from enum import StrEnum


class ReservationStatus(StrEnum):
    PENDING_CONFIRMATION = "PENDING_CONFIRMATION"
    CONFIRMED = "CONFIRMED"
    CHECKED_IN = "CHECKED_IN"
    CHECKED_OUT = "CHECKED_OUT"
    CANCELLED = "CANCELLED"
    NO_SHOW = "NO_SHOW"
    EXPIRED = "EXPIRED"


TERMINAL_RESERVATION_STATUSES: frozenset[ReservationStatus] = frozenset(
    {
        ReservationStatus.CHECKED_OUT,
        ReservationStatus.CANCELLED,
        ReservationStatus.NO_SHOW,
        ReservationStatus.EXPIRED,
    }
)


class TicketCategory(StrEnum):
    HOUSEKEEPING = "HOUSEKEEPING"
    AIRPORT_TRANSFER = "AIRPORT_TRANSFER"
    COMPLAINT = "COMPLAINT"
    SPECIAL_REQUEST = "SPECIAL_REQUEST"


class TicketPriority(StrEnum):
    LOW = "LOW"
    NORMAL = "NORMAL"
    HIGH = "HIGH"
    URGENT = "URGENT"


class TicketStatus(StrEnum):
    OPEN = "OPEN"
    IN_PROGRESS = "IN_PROGRESS"
    RESOLVED = "RESOLVED"
    CANCELLED = "CANCELLED"


class EscalationPriority(StrEnum):
    NORMAL = "NORMAL"
    HIGH = "HIGH"
    URGENT = "URGENT"


class EscalationStatus(StrEnum):
    OPEN = "OPEN"
    ASSIGNED = "ASSIGNED"
    RESOLVED = "RESOLVED"


class StaffRole(StrEnum):
    RECEPTIONIST = "RECEPTIONIST"
    MANAGER = "MANAGER"
    ADMIN = "ADMIN"


class ActorType(StrEnum):
    AI_AGENT = "AI_AGENT"
    GUEST = "GUEST"
    STAFF = "STAFF"
    SYSTEM = "SYSTEM"
