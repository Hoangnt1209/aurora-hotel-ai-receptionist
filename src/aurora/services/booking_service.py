"""Deterministic PostgreSQL ACID booking, inventory locking, and state machine service."""

import re
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from uuid import uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import delete, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from aurora.db.models import (
    Guest,
    Property,
    RatePlan,
    Reservation,
    ReservationNight,
    RoomInventoryDaily,
    RoomType,
)
from aurora.domain.enums import (
    TERMINAL_RESERVATION_STATUSES,
    ActorType,
    ReservationStatus,
)
from aurora.domain.errors import DomainError
from aurora.domain.schemas import (
    AvailabilityQuery,
    AvailabilityResponse,
    CancelReservationRequest,
    CreateReservationRequest,
    ModifyReservationRequest,
    NightlyBreakdown,
    RatePlanQuote,
    ReservationOut,
    RoomQuoteOption,
    StaffStatusTransitionRequest,
)
from aurora.services.audit import record_audit_log
from aurora.services.idempotency import (
    check_idempotency,
    compute_request_hash,
    save_idempotency_record,
)
from aurora.services.verification import (
    build_reservation_out,
    normalize_phone_e164,
    validate_verification_token,
)

TAX_AND_SERVICE_RATE = Decimal("0.15")
PCI_PAN_PATTERN = re.compile(r"\b(?:\d[ -]*?){13,19}\b")


def _validate_no_raw_pci(text: str | None) -> None:
    if text and PCI_PAN_PATTERN.search(text):
        raise DomainError(
            error_code="PCI_DATA_NOT_ALLOWED",
            message="Payment card numbers must never be submitted or stored in special requests.",
            status_code=400,
        )


def _enumerate_nights(check_in: date, check_out: date) -> list[date]:
    if check_out <= check_in:
        raise DomainError(
            error_code="INVALID_DATE_RANGE",
            message="Check-out date must be strictly after check-in date.",
            status_code=400,
        )
    num_nights = (check_out - check_in).days
    if num_nights > 30:
        raise DomainError(
            error_code="STAY_TOO_LONG",
            message="Online bookings support up to 30 nights; longer stays require staff assistance.",
            status_code=400,
        )
    return [check_in + timedelta(days=offset) for offset in range(num_nights)]


def _property_today(prop: Property) -> date:
    return datetime.now(ZoneInfo(prop.timezone)).date()


def _calculate_nightly_and_total(
    room_type: RoomType,
    rate_plan: RatePlan,
    inventories_by_date: dict[date, RoomInventoryDaily],
    nights: list[date],
) -> tuple[list[tuple[date, Decimal, int]], Decimal, Decimal, Decimal]:
    breakdown: list[tuple[date, Decimal, int]] = []
    subtotal = Decimal("0")
    for stay_day in nights:
        inv = inventories_by_date.get(stay_day)
        if inv is None:
            raise DomainError(
                error_code="INVENTORY_NOT_INITIALIZED",
                message=f"Inventory is not open yet for {stay_day.isoformat()}.",
                status_code=409,
            )
        available = inv.total_rooms - inv.reserved_rooms - inv.blocked_rooms
        base = inv.price_override_vnd if inv.price_override_vnd is not None else room_type.base_rate_vnd
        nightly = (base * rate_plan.price_multiplier).quantize(
            Decimal("1"), rounding=ROUND_HALF_UP
        )
        subtotal += nightly
        breakdown.append((stay_day, nightly, available))

    tax_and_service = (subtotal * TAX_AND_SERVICE_RATE).quantize(
        Decimal("1"), rounding=ROUND_HALF_UP
    )
    total = subtotal + tax_and_service
    return breakdown, subtotal, tax_and_service, total


async def check_availability(
    session: AsyncSession,
    query: AvailabilityQuery,
) -> AvailabilityResponse:
    """Query live PostgreSQL daily inventory and deterministic rate-plan quotes."""
    if query.rooms_count > 1:
        raise DomainError(
            error_code="GROUP_BOOKING_REQUIRES_ESCALATION",
            message="Bookings for 2 or more rooms must be coordinated by our front-desk team.",
            status_code=400,
        )

    nights = _enumerate_nights(query.check_in_date, query.check_out_date)
    prop_code = query.property_code.strip().upper()
    prop = await session.scalar(select(Property).where(Property.code == prop_code))
    if prop is None:
        raise DomainError(
            error_code="PROPERTY_NOT_FOUND",
            message=f"Property '{prop_code}' was not found.",
            status_code=404,
        )

    if query.check_in_date < _property_today(prop):
        raise DomainError(
            error_code="PAST_CHECK_IN_DATE",
            message="Check-in date cannot be in the past.",
            status_code=400,
        )

    rt_stmt = (
        select(RoomType)
        .options(selectinload(RoomType.rate_plans))
        .where(
            RoomType.property_id == prop.id,
            RoomType.is_active.is_(True),
            RoomType.max_adults >= query.adults,
            RoomType.max_children >= query.children,
        )
        .order_by(RoomType.base_rate_vnd.asc())
    )
    if query.room_type_code:
        rt_stmt = rt_stmt.where(RoomType.code == query.room_type_code.strip().upper())

    room_types = (await session.scalars(rt_stmt)).all()
    if not room_types:
        return AvailabilityResponse(
            property_code=prop.code,
            property_name=prop.name,
            check_in_date=query.check_in_date,
            check_out_date=query.check_out_date,
            nights=len(nights),
            options=[],
        )

    rt_ids = [rt.id for rt in room_types]
    inv_rows = (
        await session.scalars(
            select(RoomInventoryDaily).where(
                RoomInventoryDaily.room_type_id.in_(rt_ids),
                RoomInventoryDaily.stay_date.in_(nights),
            )
        )
    ).all()
    inv_map: dict[tuple[object, date], RoomInventoryDaily] = {
        (row.room_type_id, row.stay_date): row for row in inv_rows
    }

    options: list[RoomQuoteOption] = []
    for rt in room_types:
        rt_inventories = {
            d: inv_map[(rt.id, d)] for d in nights if (rt.id, d) in inv_map
        }
        if len(rt_inventories) != len(nights):
            continue

        min_avail = min(
            inv.total_rooms - inv.reserved_rooms - inv.blocked_rooms
            for inv in rt_inventories.values()
        )
        if min_avail < 1:
            continue

        plan_quotes: list[RatePlanQuote] = []
        for rp in sorted(rt.rate_plans, key=lambda r: r.price_multiplier, reverse=True):
            breakdown, subtotal, tax_svc, total = _calculate_nightly_and_total(
                rt, rp, rt_inventories, nights
            )
            plan_quotes.append(
                RatePlanQuote(
                    rate_plan_code=rp.code,
                    rate_plan_name=rp.name,
                    is_refundable=rp.is_refundable,
                    free_cancel_hours_before_checkin=rp.free_cancel_hours_before_checkin,
                    breakfast_included=rp.breakfast_included,
                    subtotal_vnd=subtotal,
                    tax_and_service_vnd=tax_svc,
                    total_amount_vnd=total,
                    nightly_breakdown=[
                        NightlyBreakdown(
                            stay_date=d,
                            nightly_rate_vnd=rate,
                            available_rooms=avail,
                        )
                        for d, rate, avail in breakdown
                    ],
                )
            )

        options.append(
            RoomQuoteOption(
                property_code=prop.code,
                property_name=prop.name,
                room_type_code=rt.code,
                room_type_name=rt.name,
                description=rt.description,
                max_adults=rt.max_adults,
                max_children=rt.max_children,
                min_available_rooms=min_avail,
                rate_plans=plan_quotes,
            )
        )

    return AvailabilityResponse(
        property_code=prop.code,
        property_name=prop.name,
        check_in_date=query.check_in_date,
        check_out_date=query.check_out_date,
        nights=len(nights),
        options=options,
    )


async def create_reservation(
    session: AsyncSession,
    req: CreateReservationRequest,
    idempotency_key: str,
    actor_type: str = ActorType.AI_AGENT.value,
    actor_id: str = "aurora-agent",
    trace_id: str | None = None,
) -> ReservationOut:
    """Create a reservation atomically under sorted row locks and idempotency protection."""
    trace = trace_id or uuid4().hex
    actor_scope = f"{actor_type}:{actor_id}"
    endpoint = "POST:/api/v1/reservations"
    req_hash = compute_request_hash(req)

    _validate_no_raw_pci(req.special_requests)
    if req.rooms_count > 1:
        raise DomainError(
            error_code="GROUP_BOOKING_REQUIRES_ESCALATION",
            message="Group reservations (2+ rooms) require human front-desk coordination.",
            status_code=400,
        )

    nights = _enumerate_nights(req.check_in_date, req.check_out_date)

    try:
        cached = await check_idempotency(
            session=session,
            actor_scope=actor_scope,
            endpoint=endpoint,
            idempotency_key=idempotency_key,
            request_hash=req_hash,
        )
        if cached is not None:
            return ReservationOut.model_validate(cached)

        prop_code = req.property_code.strip().upper()
        prop = await session.scalar(select(Property).where(Property.code == prop_code))
        if prop is None:
            raise DomainError(
                error_code="PROPERTY_NOT_FOUND",
                message=f"Property '{prop_code}' was not found.",
                status_code=404,
            )

        if req.check_in_date < _property_today(prop):
            raise DomainError(
                error_code="PAST_CHECK_IN_DATE",
                message="Check-in date cannot be in the past.",
                status_code=400,
            )

        room_type = await session.scalar(
            select(RoomType).where(
                RoomType.property_id == prop.id,
                RoomType.code == req.room_type_code.strip().upper(),
                RoomType.is_active.is_(True),
            )
        )
        if room_type is None:
            raise DomainError(
                error_code="ROOM_TYPE_NOT_FOUND",
                message=f"Room type '{req.room_type_code}' was not found at {prop.code}.",
                status_code=404,
            )

        if req.adults > room_type.max_adults or req.children > room_type.max_children:
            raise DomainError(
                error_code="OCCUPANCY_EXCEEDED",
                message=(
                    f"{room_type.name} supports up to {room_type.max_adults} adults and "
                    f"{room_type.max_children} children."
                ),
                status_code=400,
            )

        rate_plan = await session.scalar(
            select(RatePlan).where(
                RatePlan.room_type_id == room_type.id,
                RatePlan.code == req.rate_plan_code.strip().upper(),
            )
        )
        if rate_plan is None:
            raise DomainError(
                error_code="RATE_PLAN_NOT_FOUND",
                message=f"Rate plan '{req.rate_plan_code}' was not found.",
                status_code=404,
            )

        # Canonical sorted row-level lock across all stay nights
        locked_rows = (
            await session.scalars(
                select(RoomInventoryDaily)
                .where(
                    RoomInventoryDaily.room_type_id == room_type.id,
                    RoomInventoryDaily.stay_date.in_(nights),
                )
                .order_by(
                    RoomInventoryDaily.room_type_id.asc(),
                    RoomInventoryDaily.stay_date.asc(),
                )
                .with_for_update()
            )
        ).all()

        inv_by_date = {row.stay_date: row for row in locked_rows}
        breakdown, _, _, total_amount = _calculate_nightly_and_total(
            room_type, rate_plan, inv_by_date, nights
        )

        # Re-check inventory after acquiring FOR UPDATE locks
        for stay_day, _, available in breakdown:
            if available < 1:
                raise DomainError(
                    error_code="INSUFFICIENT_INVENTORY",
                    message=f"No rooms left for {room_type.code} on {stay_day.isoformat()}.",
                    status_code=409,
                )

        for stay_day in nights:
            inv = inv_by_date[stay_day]
            inv.reserved_rooms += 1
            inv.version += 1

        normalized_phone = normalize_phone_e164(req.guest_phone)
        normalized_email = str(req.guest_email).strip().lower()
        guest = await session.scalar(
            select(Guest).where(
                Guest.email == normalized_email,
                Guest.phone_e164 == normalized_phone,
            )
        )
        if guest is None:
            guest = Guest(
                full_name=req.guest_full_name.strip(),
                email=normalized_email,
                phone_e164=normalized_phone,
                preferred_language=req.preferred_language,
            )
            session.add(guest)
            await session.flush()

        confirmation_code = f"AUR-{prop.code}-{uuid4().hex[:6].upper()}"
        reservation = Reservation(
            confirmation_code=confirmation_code,
            property_id=prop.id,
            room_type_id=room_type.id,
            rate_plan_id=rate_plan.id,
            guest_id=guest.id,
            check_in_date=req.check_in_date,
            check_out_date=req.check_out_date,
            adults=req.adults,
            children=req.children,
            status=ReservationStatus.CONFIRMED.value,
            total_amount_vnd=total_amount,
            currency=prop.currency,
            special_requests=req.special_requests,
            source_channel=req.source_channel,
        )
        session.add(reservation)
        await session.flush()

        night_models = [
            ReservationNight(
                reservation_id=reservation.id,
                room_type_id=room_type.id,
                stay_date=stay_day,
                nightly_rate_vnd=nightly_rate,
            )
            for stay_day, nightly_rate, _ in breakdown
        ]
        session.add_all(night_models)
        await session.flush()
        await session.refresh(reservation, attribute_names=["nights"])

        out = build_reservation_out(reservation, prop, room_type, rate_plan, guest)
        await record_audit_log(
            session=session,
            trace_id=trace,
            actor_type=actor_type,
            actor_id=actor_id,
            action="RESERVATION_CREATED",
            entity_type="reservation",
            entity_id=str(reservation.id),
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


async def modify_reservation(
    session: AsyncSession,
    req: ModifyReservationRequest,
    idempotency_key: str,
    actor_type: str = ActorType.AI_AGENT.value,
    actor_id: str = "aurora-agent",
    trace_id: str | None = None,
) -> ReservationOut:
    """Modify dates, room type, occupancy, or notes under deadlock-free lock ordering."""
    trace = trace_id or uuid4().hex
    actor_scope = f"{actor_type}:{actor_id}"
    normalized_code = req.confirmation_code.strip().upper()
    endpoint = f"PATCH:/api/v1/reservations/{normalized_code}"
    req_hash = compute_request_hash(req)

    _validate_no_raw_pci(req.new_special_requests)

    if actor_type == ActorType.AI_AGENT.value:
        if not validate_verification_token(
            req.verification_token, normalized_code, req.conversation_id
        ):
            raise DomainError(
                error_code="IDENTITY_VERIFICATION_FAILED",
                message="Guest verification is required before modifying a reservation.",
                status_code=403,
            )

    try:
        cached = await check_idempotency(
            session=session,
            actor_scope=actor_scope,
            endpoint=endpoint,
            idempotency_key=idempotency_key,
            request_hash=req_hash,
        )
        if cached is not None:
            return ReservationOut.model_validate(cached)

        res = await session.scalar(
            select(Reservation)
            .options(selectinload(Reservation.nights))
            .where(Reservation.confirmation_code == normalized_code)
            .with_for_update()
        )
        if res is None:
            raise DomainError(
                error_code="RESERVATION_NOT_FOUND",
                message=f"Reservation {normalized_code} was not found.",
                status_code=404,
            )

        if res.status != ReservationStatus.CONFIRMED.value:
            raise DomainError(
                error_code="INVALID_STATE_TRANSITION",
                message=f"Cannot modify reservation in status '{res.status}'.",
                status_code=409,
            )

        prop = await session.scalar(select(Property).where(Property.id == res.property_id))
        old_room_type = await session.scalar(
            select(RoomType).where(RoomType.id == res.room_type_id)
        )
        rate_plan = await session.scalar(
            select(RatePlan).where(RatePlan.id == res.rate_plan_id)
        )
        guest = await session.scalar(select(Guest).where(Guest.id == res.guest_id))
        assert prop and old_room_type and rate_plan and guest

        before_state = build_reservation_out(
            res, prop, old_room_type, rate_plan, guest
        ).model_dump(mode="json")

        target_check_in = req.new_check_in_date or res.check_in_date
        target_check_out = req.new_check_out_date or res.check_out_date
        target_adults = req.new_adults if req.new_adults is not None else res.adults
        target_children = (
            req.new_children if req.new_children is not None else res.children
        )

        target_room_type = old_room_type
        if req.new_room_type_code:
            found_rt = await session.scalar(
                select(RoomType).where(
                    RoomType.property_id == prop.id,
                    RoomType.code == req.new_room_type_code.strip().upper(),
                    RoomType.is_active.is_(True),
                )
            )
            if found_rt is None:
                raise DomainError(
                    error_code="ROOM_TYPE_NOT_FOUND",
                    message=f"Room type '{req.new_room_type_code}' was not found.",
                    status_code=404,
                )
            target_room_type = found_rt

        dates_or_room_changed = (
            target_check_in != res.check_in_date
            or target_check_out != res.check_out_date
            or target_room_type.id != res.room_type_id
        )

        if (
            dates_or_room_changed
            and actor_type == ActorType.AI_AGENT.value
            and not rate_plan.is_refundable
        ):
            raise DomainError(
                error_code="MODIFICATION_NOT_ALLOWED_BY_RATE_PLAN",
                message="Non-refundable bookings cannot change dates or room type automatically; escalate to staff.",
                status_code=409,
            )

        if target_check_in < _property_today(prop) and dates_or_room_changed:
            raise DomainError(
                error_code="PAST_CHECK_IN_DATE",
                message="Modified check-in date cannot be in the past.",
                status_code=400,
            )

        if (
            target_adults > target_room_type.max_adults
            or target_children > target_room_type.max_children
        ):
            raise DomainError(
                error_code="OCCUPANCY_EXCEEDED",
                message="Requested guest count exceeds room type capacity.",
                status_code=400,
            )

        new_nights = _enumerate_nights(target_check_in, target_check_out)

        if dates_or_room_changed:
            old_keys = {(n.room_type_id, n.stay_date) for n in res.nights}
            new_keys = {(target_room_type.id, d) for d in new_nights}
            union_keys = sorted(old_keys | new_keys)

            locked_rows = (
                await session.scalars(
                    select(RoomInventoryDaily)
                    .where(
                        tuple_(
                            RoomInventoryDaily.room_type_id,
                            RoomInventoryDaily.stay_date,
                        ).in_(union_keys)
                    )
                    .order_by(
                        RoomInventoryDaily.room_type_id.asc(),
                        RoomInventoryDaily.stay_date.asc(),
                    )
                    .with_for_update()
                )
            ).all()
            inv_by_key = {(r.room_type_id, r.stay_date): r for r in locked_rows}

            # Release old nights first inside transaction
            for key in old_keys:
                old_inv = inv_by_key.get(key)
                if old_inv is not None:
                    old_inv.reserved_rooms -= 1
                    old_inv.version += 1

            # Check availability on all new nights
            target_inv_by_date = {
                d: inv_by_key[(target_room_type.id, d)]
                for d in new_nights
                if (target_room_type.id, d) in inv_by_key
            }
            target_rp = await session.scalar(
                select(RatePlan).where(
                    RatePlan.room_type_id == target_room_type.id,
                    RatePlan.code == rate_plan.code,
                )
            )
            assert target_rp is not None

            breakdown, _, _, total_amount = _calculate_nightly_and_total(
                target_room_type, target_rp, target_inv_by_date, new_nights
            )
            for stay_day, _, available in breakdown:
                if available < 1:
                    raise DomainError(
                        error_code="INSUFFICIENT_INVENTORY",
                        message=f"No rooms left for {target_room_type.code} on {stay_day.isoformat()}.",
                        status_code=409,
                    )

            for stay_day in new_nights:
                inv = target_inv_by_date[stay_day]
                inv.reserved_rooms += 1
                inv.version += 1

            # Replace reservation_nights cleanly
            await session.execute(
                delete(ReservationNight).where(
                    ReservationNight.reservation_id == res.id
                )
            )
            await session.flush()

            session.add_all(
                [
                    ReservationNight(
                        reservation_id=res.id,
                        room_type_id=target_room_type.id,
                        stay_date=stay_day,
                        nightly_rate_vnd=nightly_rate,
                    )
                    for stay_day, nightly_rate, _ in breakdown
                ]
            )
            res.room_type_id = target_room_type.id
            res.rate_plan_id = target_rp.id
            res.check_in_date = target_check_in
            res.check_out_date = target_check_out
            res.total_amount_vnd = total_amount
            rate_plan = target_rp

        res.adults = target_adults
        res.children = target_children
        if req.new_special_requests is not None:
            res.special_requests = req.new_special_requests

        await session.flush()
        await session.refresh(res, attribute_names=["nights", "updated_at"])

        after_out = build_reservation_out(
            res, prop, target_room_type, rate_plan, guest
        )
        await record_audit_log(
            session=session,
            trace_id=trace,
            actor_type=actor_type,
            actor_id=actor_id,
            action="RESERVATION_MODIFIED",
            entity_type="reservation",
            entity_id=str(res.id),
            before_state=before_state,
            after_state=after_out.model_dump(mode="json"),
        )
        await save_idempotency_record(
            session=session,
            actor_scope=actor_scope,
            endpoint=endpoint,
            idempotency_key=idempotency_key,
            request_hash=req_hash,
            response_status=200,
            response_body=after_out.model_dump(mode="json"),
        )
        await session.commit()
        return after_out
    except Exception:
        await session.rollback()
        raise


async def cancel_reservation(
    session: AsyncSession,
    req: CancelReservationRequest,
    idempotency_key: str,
    actor_type: str = ActorType.AI_AGENT.value,
    actor_id: str = "aurora-agent",
    trace_id: str | None = None,
) -> ReservationOut:
    """Cancel an eligible reservation, release locked nights, and write audit log."""
    trace = trace_id or uuid4().hex
    actor_scope = f"{actor_type}:{actor_id}"
    normalized_code = req.confirmation_code.strip().upper()
    endpoint = f"POST:/api/v1/reservations/{normalized_code}/cancel"
    req_hash = compute_request_hash(req)

    if actor_type == ActorType.AI_AGENT.value:
        if not validate_verification_token(
            req.verification_token, normalized_code, req.conversation_id
        ):
            raise DomainError(
                error_code="IDENTITY_VERIFICATION_FAILED",
                message="Guest verification is required before cancelling a reservation.",
                status_code=403,
            )

    try:
        cached = await check_idempotency(
            session=session,
            actor_scope=actor_scope,
            endpoint=endpoint,
            idempotency_key=idempotency_key,
            request_hash=req_hash,
        )
        if cached is not None:
            return ReservationOut.model_validate(cached)

        res = await session.scalar(
            select(Reservation)
            .options(selectinload(Reservation.nights))
            .where(Reservation.confirmation_code == normalized_code)
            .with_for_update()
        )
        if res is None:
            raise DomainError(
                error_code="RESERVATION_NOT_FOUND",
                message=f"Reservation {normalized_code} was not found.",
                status_code=404,
            )

        current_status = ReservationStatus(res.status)
        if current_status in TERMINAL_RESERVATION_STATUSES or current_status != ReservationStatus.CONFIRMED:
            raise DomainError(
                error_code="INVALID_STATE_TRANSITION",
                message=f"Reservation in status '{res.status}' cannot be cancelled.",
                status_code=409,
            )

        prop = await session.scalar(select(Property).where(Property.id == res.property_id))
        room_type = await session.scalar(
            select(RoomType).where(RoomType.id == res.room_type_id)
        )
        rate_plan = await session.scalar(
            select(RatePlan).where(RatePlan.id == res.rate_plan_id)
        )
        guest = await session.scalar(select(Guest).where(Guest.id == res.guest_id))
        assert prop and room_type and rate_plan and guest

        # Check cancellation policy unless authorized staff explicitly overrides
        is_staff_override = actor_type == ActorType.STAFF.value and req.force_override
        if not is_staff_override:
            if not rate_plan.is_refundable:
                raise DomainError(
                    error_code="CANCELLATION_WINDOW_EXPIRED",
                    message=(
                        "This reservation uses a Non-Refundable rate plan and cannot be "
                        "cancelled automatically; please escalate to front-desk staff."
                    ),
                    status_code=409,
                )
            check_in_dt = datetime.combine(
                res.check_in_date, prop.check_in_time, tzinfo=ZoneInfo(prop.timezone)
            )
            deadline = check_in_dt - timedelta(
                hours=rate_plan.free_cancel_hours_before_checkin
            )
            if datetime.now(UTC) > deadline.astimezone(UTC):
                raise DomainError(
                    error_code="CANCELLATION_WINDOW_EXPIRED",
                    message=(
                        "Free cancellation deadline has passed; please escalate to "
                        "front-desk management."
                    ),
                    status_code=409,
                )

        before_state = build_reservation_out(
            res, prop, room_type, rate_plan, guest
        ).model_dump(mode="json")

        night_keys = sorted((n.room_type_id, n.stay_date) for n in res.nights)
        if night_keys:
            locked_inv = (
                await session.scalars(
                    select(RoomInventoryDaily)
                    .where(
                        tuple_(
                            RoomInventoryDaily.room_type_id,
                            RoomInventoryDaily.stay_date,
                        ).in_(night_keys)
                    )
                    .order_by(
                        RoomInventoryDaily.room_type_id.asc(),
                        RoomInventoryDaily.stay_date.asc(),
                    )
                    .with_for_update()
                )
            ).all()
            for inv in locked_inv:
                inv.reserved_rooms -= 1
                inv.version += 1

        res.status = ReservationStatus.CANCELLED.value
        await session.flush()
        await session.refresh(res, attribute_names=["nights", "updated_at"])

        after_out = build_reservation_out(res, prop, room_type, rate_plan, guest)
        await record_audit_log(
            session=session,
            trace_id=trace,
            actor_type=actor_type,
            actor_id=actor_id,
            action="RESERVATION_CANCELLED",
            entity_type="reservation",
            entity_id=str(res.id),
            before_state=before_state,
            after_state={
                **after_out.model_dump(mode="json"),
                "cancel_reason": req.reason,
            },
        )
        await save_idempotency_record(
            session=session,
            actor_scope=actor_scope,
            endpoint=endpoint,
            idempotency_key=idempotency_key,
            request_hash=req_hash,
            response_status=200,
            response_body=after_out.model_dump(mode="json"),
        )
        await session.commit()
        return after_out
    except Exception:
        await session.rollback()
        raise


async def transition_reservation_status_by_staff(
    session: AsyncSession,
    confirmation_code: str,
    req: StaffStatusTransitionRequest,
    staff_id: str,
    trace_id: str | None = None,
) -> ReservationOut:
    """Perform staff-only transitions: CHECKED_IN, CHECKED_OUT, or NO_SHOW."""
    trace = trace_id or uuid4().hex
    normalized_code = confirmation_code.strip().upper()
    allowed_transitions: dict[ReservationStatus, set[ReservationStatus]] = {
        ReservationStatus.CONFIRMED: {
            ReservationStatus.CHECKED_IN,
            ReservationStatus.NO_SHOW,
        },
        ReservationStatus.CHECKED_IN: {ReservationStatus.CHECKED_OUT},
    }

    try:
        res = await session.scalar(
            select(Reservation)
            .options(selectinload(Reservation.nights))
            .where(Reservation.confirmation_code == normalized_code)
            .with_for_update()
        )
        if res is None:
            raise DomainError(
                error_code="RESERVATION_NOT_FOUND",
                message=f"Reservation {normalized_code} was not found.",
                status_code=404,
            )

        current = ReservationStatus(res.status)
        if req.target_status not in allowed_transitions.get(current, set()):
            raise DomainError(
                error_code="INVALID_STATE_TRANSITION",
                message=f"Staff cannot transition from '{current.value}' to '{req.target_status.value}'.",
                status_code=409,
            )

        prop = await session.scalar(select(Property).where(Property.id == res.property_id))
        room_type = await session.scalar(
            select(RoomType).where(RoomType.id == res.room_type_id)
        )
        rate_plan = await session.scalar(
            select(RatePlan).where(RatePlan.id == res.rate_plan_id)
        )
        guest = await session.scalar(select(Guest).where(Guest.id == res.guest_id))
        assert prop and room_type and rate_plan and guest

        before_state = build_reservation_out(
            res, prop, room_type, rate_plan, guest
        ).model_dump(mode="json")

        if req.target_status == ReservationStatus.NO_SHOW:
            today_prop = _property_today(prop)
            remaining_keys = sorted(
                (n.room_type_id, n.stay_date)
                for n in res.nights
                if n.stay_date >= today_prop
            )
            if remaining_keys:
                locked_inv = (
                    await session.scalars(
                        select(RoomInventoryDaily)
                        .where(
                            tuple_(
                                RoomInventoryDaily.room_type_id,
                                RoomInventoryDaily.stay_date,
                            ).in_(remaining_keys)
                        )
                        .order_by(
                            RoomInventoryDaily.room_type_id.asc(),
                            RoomInventoryDaily.stay_date.asc(),
                        )
                        .with_for_update()
                    )
                ).all()
                for inv in locked_inv:
                    inv.reserved_rooms -= 1
                    inv.version += 1

        res.status = req.target_status.value
        await session.flush()
        await session.refresh(res, attribute_names=["nights", "updated_at"])

        after_out = build_reservation_out(res, prop, room_type, rate_plan, guest)
        await record_audit_log(
            session=session,
            trace_id=trace,
            actor_type=ActorType.STAFF.value,
            actor_id=staff_id,
            action=f"RESERVATION_{req.target_status.value}",
            entity_type="reservation",
            entity_id=str(res.id),
            before_state=before_state,
            after_state={**after_out.model_dump(mode="json"), "reason": req.reason},
        )
        await session.commit()
        return after_out
    except Exception:
        await session.rollback()
        raise


async def list_reservations(
    session: AsyncSession,
    property_code: str | None = None,
    status: ReservationStatus | None = None,
) -> list[ReservationOut]:
    """List reservations for Staff Operations Dashboard."""
    stmt = (
        select(Reservation, Property, RoomType, RatePlan, Guest)
        .join(Property, Property.id == Reservation.property_id)
        .join(RoomType, RoomType.id == Reservation.room_type_id)
        .join(RatePlan, RatePlan.id == Reservation.rate_plan_id)
        .join(Guest, Guest.id == Reservation.guest_id)
        .options(selectinload(Reservation.nights))
        .order_by(Reservation.created_at.desc())
    )
    if property_code:
        stmt = stmt.where(Property.code == property_code.strip().upper())
    if status:
        stmt = stmt.where(Reservation.status == status.value)

    rows = (await session.execute(stmt)).all()
    return [
        build_reservation_out(res, prop, rt, rp, guest)
        for res, prop, rt, rp, guest in rows
    ]
