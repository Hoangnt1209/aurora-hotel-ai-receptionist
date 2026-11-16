"""Guest identity verification and signed conversation-scoped tokens."""

import re
from datetime import UTC, datetime, timedelta
from uuid import UUID

import jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from aurora.config import get_settings
from aurora.db.models import Guest, Property, RatePlan, Reservation, RoomType
from aurora.domain.enums import ReservationStatus
from aurora.domain.errors import DomainError
from aurora.domain.schemas import (
    ReservationNightOut,
    ReservationOut,
    VerifyGuestResponse,
)


def normalize_phone_e164(raw_phone: str) -> str:
    """Normalize Vietnamese and international phone strings to E.164-like form."""
    cleaned = re.sub(r"[\s\-\.\(\)]", "", raw_phone.strip())
    if cleaned.startswith("+"):
        digits = re.sub(r"\D", "", cleaned)
        return f"+{digits}"
    digits = re.sub(r"\D", "", cleaned)
    if digits.startswith("0") and len(digits) >= 9:
        return f"+84{digits[1:]}"
    if digits.startswith("84") and len(digits) >= 10:
        return f"+{digits}"
    return f"+{digits}" if digits else ""


def issue_verification_token(
    confirmation_code: str,
    conversation_id: UUID | None = None,
) -> tuple[str, datetime]:
    """Sign a short-lived JWT binding guest verification to code and optional conversation."""
    settings = get_settings()
    now = datetime.now(UTC)
    expires_at = now + timedelta(seconds=settings.verification_token_ttl_seconds)
    payload = {
        "sub": confirmation_code.strip().upper(),
        "cid": str(conversation_id) if conversation_id else "*",
        "typ": "guest_verification",
        "iat": int(now.timestamp()),
        "exp": int(expires_at.timestamp()),
    }
    token = jwt.encode(
        payload,
        settings.jwt_secret_key,
        algorithm=settings.jwt_algorithm,
    )
    return token, expires_at


def validate_verification_token(
    token: str | None,
    confirmation_code: str,
    conversation_id: UUID | None = None,
) -> bool:
    """Validate guest verification token signature, expiry, code, and conversation scope."""
    if not token:
        return False
    settings = get_settings()
    try:
        decoded = jwt.decode(
            token,
            settings.jwt_secret_key,
            algorithms=[settings.jwt_algorithm],
        )
    except jwt.PyJWTError:
        return False

    if decoded.get("typ") != "guest_verification":
        return False
    if decoded.get("sub") != confirmation_code.strip().upper():
        return False

    token_cid = decoded.get("cid")
    if token_cid != "*" and conversation_id is not None and token_cid != str(conversation_id):
        return False
    return True


def build_reservation_out(
    reservation: Reservation,
    prop: Property,
    room_type: RoomType,
    rate_plan: RatePlan,
    guest: Guest,
) -> ReservationOut:
    """Convert ORM entities into ReservationOut DTO."""
    ordered_nights = sorted(reservation.nights, key=lambda n: n.stay_date)
    return ReservationOut(
        id=reservation.id,
        confirmation_code=reservation.confirmation_code,
        property_code=prop.code,
        property_name=prop.name,
        room_type_code=room_type.code,
        room_type_name=room_type.name,
        rate_plan_code=rate_plan.code,
        rate_plan_name=rate_plan.name,
        is_refundable=rate_plan.is_refundable,
        check_in_date=reservation.check_in_date,
        check_out_date=reservation.check_out_date,
        adults=reservation.adults,
        children=reservation.children,
        status=ReservationStatus(reservation.status),
        total_amount_vnd=reservation.total_amount_vnd,
        currency=reservation.currency,
        special_requests=reservation.special_requests,
        source_channel=reservation.source_channel,
        guest_full_name=guest.full_name,
        guest_email=guest.email,
        guest_phone_e164=guest.phone_e164,
        nights=[
            ReservationNightOut(
                stay_date=n.stay_date,
                nightly_rate_vnd=n.nightly_rate_vnd,
            )
            for n in ordered_nights
        ],
        created_at=reservation.created_at,
        updated_at=reservation.updated_at,
    )


async def verify_guest_reservation(
    session: AsyncSession,
    confirmation_code: str,
    phone_or_email: str,
    conversation_id: UUID | None = None,
) -> VerifyGuestResponse:
    """Verify reservation ownership via normalized phone or email before returning PII."""
    normalized_code = confirmation_code.strip().upper()
    row = (
        await session.execute(
            select(Reservation, Property, RoomType, RatePlan, Guest)
            .join(Property, Property.id == Reservation.property_id)
            .join(RoomType, RoomType.id == Reservation.room_type_id)
            .join(RatePlan, RatePlan.id == Reservation.rate_plan_id)
            .join(Guest, Guest.id == Reservation.guest_id)
            .options(selectinload(Reservation.nights))
            .where(Reservation.confirmation_code == normalized_code)
        )
    ).first()

    if row is None:
        raise DomainError(
            error_code="RESERVATION_NOT_FOUND",
            message=f"Reservation {normalized_code} was not found.",
            status_code=404,
        )

    reservation, prop, room_type, rate_plan, guest = row
    contact_clean = phone_or_email.strip()

    is_match = False
    if "@" in contact_clean:
        is_match = guest.email.strip().lower() == contact_clean.lower()
    else:
        normalized_input_phone = normalize_phone_e164(contact_clean)
        normalized_guest_phone = normalize_phone_e164(guest.phone_e164)
        is_match = bool(normalized_input_phone) and (
            normalized_input_phone == normalized_guest_phone
        )

    if not is_match:
        raise DomainError(
            error_code="IDENTITY_VERIFICATION_FAILED",
            message="Provided phone number or email does not match this reservation.",
            status_code=403,
        )

    token, expires_at = issue_verification_token(
        confirmation_code=normalized_code,
        conversation_id=conversation_id,
    )
    return VerifyGuestResponse(
        verified=True,
        confirmation_code=normalized_code,
        verification_token=token,
        expires_at=expires_at,
        reservation=build_reservation_out(reservation, prop, room_type, rate_plan, guest),
    )
