"""Milestone 2 verification tests: ACID locking, 10-way race, idempotency, verification, and audit logs."""

import asyncio
from datetime import date, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aurora.db.models import (
    AuditLog,
    Conversation,
    Property,
    Reservation,
    RoomInventoryDaily,
    RoomType,
)
from aurora.db.seed import seed_database
from aurora.domain.enums import (
    ActorType,
    EscalationPriority,
    ReservationStatus,
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
    StaffStatusTransitionRequest,
)
from aurora.services.booking_service import (
    cancel_reservation,
    check_availability,
    create_reservation,
    modify_reservation,
    transition_reservation_status_by_staff,
)
from aurora.services.escalation_service import create_escalation
from aurora.services.ticket_service import create_support_ticket
from aurora.services.verification import verify_guest_reservation


async def test_10_way_concurrent_last_room_race_allows_only_one_booking(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """10 concurrent booking attempts on 1 remaining room must yield 1 success & 9 conflicts."""
    check_in = date.today() + timedelta(days=40)
    check_out = check_in + timedelta(days=2)
    nights = [check_in, check_in + timedelta(days=1)]

    async with session_factory() as setup_session:
        await seed_database(setup_session)
        prop = await setup_session.scalar(select(Property).where(Property.code == "PQC"))
        room_type = await setup_session.scalar(
            select(RoomType).where(
                RoomType.property_id == prop.id,
                RoomType.code == "POOL_VILLA",
            )
        )
        assert room_type is not None

        # Set available inventory to exactly 1 remaining room for both nights
        inv_rows = (
            await setup_session.scalars(
                select(RoomInventoryDaily).where(
                    RoomInventoryDaily.room_type_id == room_type.id,
                    RoomInventoryDaily.stay_date.in_(nights),
                )
            )
        ).all()
        assert len(inv_rows) == 2
        for row in inv_rows:
            row.reserved_rooms = row.total_rooms - 1
            row.blocked_rooms = 0
        await setup_session.commit()

    async def attempt_booking(idx: int) -> str:
        async with session_factory() as worker_session:
            req = CreateReservationRequest(
                property_code="PQC",
                room_type_code="POOL_VILLA",
                rate_plan_code="FLEX_24H",
                check_in_date=check_in,
                check_out_date=check_out,
                adults=2,
                children=0,
                guest_full_name=f"Concurrent Guest {idx}",
                guest_email=f"race-{idx}-{uuid4().hex[:6]}@example.com",
                guest_phone=f"+8490111{idx:04d}",
                preferred_language="vi",
            )
            try:
                res = await create_reservation(
                    worker_session,
                    req=req,
                    idempotency_key=f"race-{uuid4().hex}",
                    trace_id=f"trace-race-{idx}",
                )
                return f"OK:{res.confirmation_code}"
            except DomainError as exc:
                return f"ERR:{exc.error_code}"

    results = await asyncio.gather(*(attempt_booking(i) for i in range(10)))
    successes = [r for r in results if r.startswith("OK:")]
    conflicts = [r for r in results if r == "ERR:INSUFFICIENT_INVENTORY"]

    assert len(successes) == 1
    assert len(conflicts) == 9

    async with session_factory() as verify_session:
        inv_after = (
            await verify_session.scalars(
                select(RoomInventoryDaily).where(
                    RoomInventoryDaily.room_type_id == room_type.id,
                    RoomInventoryDaily.stay_date.in_(nights),
                )
            )
        ).all()
        for row in inv_after:
            assert row.reserved_rooms == row.total_rooms

        # Restore inventory to baseline for subsequent tests
        for row in inv_after:
            row.reserved_rooms = 0
        await verify_session.commit()


async def test_idempotency_replay_and_conflict(db_session: AsyncSession) -> None:
    """Duplicate Idempotency-Key returns same confirmation code without extra room deduction."""
    await seed_database(db_session)
    check_in = date.today() + timedelta(days=25)
    check_out = check_in + timedelta(days=2)

    avail_before = await check_availability(
        db_session,
        AvailabilityQuery(
            property_code="DAD",
            check_in_date=check_in,
            check_out_date=check_out,
            adults=2,
            room_type_code="DLX_OCEAN",
        ),
    )
    assert len(avail_before.options) == 1
    initial_min_avail = avail_before.options[0].min_available_rooms

    req = CreateReservationRequest(
        property_code="DAD",
        room_type_code="DLX_OCEAN",
        rate_plan_code="FLEX_24H",
        check_in_date=check_in,
        check_out_date=check_out,
        adults=2,
        guest_full_name="Tran Minh Anh",
        guest_email="minhanh.idem@example.com",
        guest_phone="0909123456",
        preferred_language="vi",
    )
    idem_key = f"idem-{uuid4().hex}"

    first = await create_reservation(
        db_session, req=req, idempotency_key=idem_key, trace_id="trace-idem-1"
    )
    second = await create_reservation(
        db_session, req=req, idempotency_key=idem_key, trace_id="trace-idem-2"
    )
    assert first.confirmation_code == second.confirmation_code
    assert first.total_amount_vnd == second.total_amount_vnd

    avail_after = await check_availability(
        db_session,
        AvailabilityQuery(
            property_code="DAD",
            check_in_date=check_in,
            check_out_date=check_out,
            adults=2,
            room_type_code="DLX_OCEAN",
        ),
    )
    assert avail_after.options[0].min_available_rooms == initial_min_avail - 1

    # Reusing same Idempotency-Key with modified payload must fail with IDEMPOTENCY_CONFLICT
    modified_req = req.model_copy(update={"adults": 1})
    with pytest.raises(DomainError) as exc_info:
        await create_reservation(
            db_session, req=modified_req, idempotency_key=idem_key
        )
    assert exc_info.value.error_code == "IDEMPOTENCY_CONFLICT"


async def test_guest_verification_modify_cancel_and_state_machine(
    db_session: AsyncSession,
) -> None:
    """Verify verification gate, rate-plan cancellation rules, modification, and audit logs."""
    await seed_database(db_session)
    check_in = date.today() + timedelta(days=15)
    check_out = check_in + timedelta(days=2)
    new_check_in = date.today() + timedelta(days=18)
    new_check_out = new_check_in + timedelta(days=3)

    # 1. Create FLEX_24H booking
    flex_res = await create_reservation(
        db_session,
        req=CreateReservationRequest(
            property_code="SGN",
            room_type_code="SUP_CITY",
            rate_plan_code="FLEX_24H",
            check_in_date=check_in,
            check_out_date=check_out,
            adults=2,
            guest_full_name="Le Quoc Bao",
            guest_email="bao.verify@example.com",
            guest_phone="0912345678",
        ),
        idempotency_key=f"flex-{uuid4().hex}",
        trace_id="trace-verify-1",
    )

    # 2. Unverified cancel attempt fails
    with pytest.raises(DomainError) as exc_unverified:
        await cancel_reservation(
            db_session,
            req=CancelReservationRequest(
                confirmation_code=flex_res.confirmation_code,
                verification_token=None,
            ),
            idempotency_key=f"cancel-unv-{uuid4().hex}",
        )
    assert exc_unverified.value.error_code == "IDENTITY_VERIFICATION_FAILED"

    # 3. Wrong phone fails verification
    with pytest.raises(DomainError) as exc_wrong_phone:
        await verify_guest_reservation(
            db_session,
            confirmation_code=flex_res.confirmation_code,
            phone_or_email="0999999999",
        )
    assert exc_wrong_phone.value.error_code == "IDENTITY_VERIFICATION_FAILED"

    # 4. Correct phone (normalized from local 0912345678 -> +84912345678) succeeds
    verified = await verify_guest_reservation(
        db_session,
        confirmation_code=flex_res.confirmation_code,
        phone_or_email="+84 912-345-678",
    )
    assert verified.verified is True

    # 5. Modify stay dates using valid verification token
    modified = await modify_reservation(
        db_session,
        req=ModifyReservationRequest(
            confirmation_code=flex_res.confirmation_code,
            verification_token=verified.verification_token,
            new_check_in_date=new_check_in,
            new_check_out_date=new_check_out,
            new_special_requests="High floor quiet room",
        ),
        idempotency_key=f"mod-{uuid4().hex}",
        trace_id="trace-modify-1",
    )
    assert len(modified.nights) == 3
    assert modified.special_requests == "High floor quiet room"

    # 6. Cancel FLEX_24H booking -> succeeds and sets status CANCELLED
    cancelled = await cancel_reservation(
        db_session,
        req=CancelReservationRequest(
            confirmation_code=flex_res.confirmation_code,
            verification_token=verified.verification_token,
            reason="Trip postponed",
        ),
        idempotency_key=f"can-{uuid4().hex}",
        trace_id="trace-cancel-1",
    )
    assert cancelled.status == ReservationStatus.CANCELLED

    # 7. Terminal state rejects further staff transition
    with pytest.raises(DomainError) as exc_terminal:
        await transition_reservation_status_by_staff(
            db_session,
            confirmation_code=flex_res.confirmation_code,
            req=StaffStatusTransitionRequest(target_status=ReservationStatus.CHECKED_IN),
            staff_id="staff-test",
        )
    assert exc_terminal.value.error_code == "INVALID_STATE_TRANSITION"

    # 8. NON_REF booking cannot be cancelled by AI_AGENT
    nonref_res = await create_reservation(
        db_session,
        req=CreateReservationRequest(
            property_code="SGN",
            room_type_code="SUP_CITY",
            rate_plan_code="NON_REF",
            check_in_date=check_in,
            check_out_date=check_out,
            adults=2,
            guest_full_name="Pham Non Ref",
            guest_email="nonref@example.com",
            guest_phone="0988777666",
        ),
        idempotency_key=f"nonref-{uuid4().hex}",
    )
    verified_nonref = await verify_guest_reservation(
        db_session,
        confirmation_code=nonref_res.confirmation_code,
        phone_or_email="nonref@example.com",
    )
    with pytest.raises(DomainError) as exc_nonref:
        await cancel_reservation(
            db_session,
            req=CancelReservationRequest(
                confirmation_code=nonref_res.confirmation_code,
                verification_token=verified_nonref.verification_token,
            ),
            idempotency_key=f"can-nonref-{uuid4().hex}",
        )
    assert exc_nonref.value.error_code == "CANCELLATION_WINDOW_EXPIRED"

    # Verify AuditLog recorded creation, modification, and cancellation
    audit_count = await db_session.scalar(
        select(func.count())
        .select_from(AuditLog)
        .where(AuditLog.entity_id == str(flex_res.id))
    )
    assert audit_count == 3


async def test_deadlock_free_concurrent_swapping_modifications(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Two bookings concurrently swapping opposite date windows must complete without deadlock."""
    win_a_in = date.today() + timedelta(days=50)
    win_a_out = win_a_in + timedelta(days=2)
    win_b_in = date.today() + timedelta(days=52)
    win_b_out = win_b_in + timedelta(days=2)

    async with session_factory() as s:
        await seed_database(s)
        res_a = await create_reservation(
            s,
            req=CreateReservationRequest(
                property_code="DAD",
                room_type_code="DLX_GARDEN",
                rate_plan_code="FLEX_24H",
                check_in_date=win_a_in,
                check_out_date=win_a_out,
                adults=2,
                guest_full_name="Swap Guest A",
                guest_email="swapa@example.com",
                guest_phone="0902000001",
            ),
            idempotency_key=f"swapa-{uuid4().hex}",
        )
        res_b = await create_reservation(
            s,
            req=CreateReservationRequest(
                property_code="DAD",
                room_type_code="DLX_GARDEN",
                rate_plan_code="FLEX_24H",
                check_in_date=win_b_in,
                check_out_date=win_b_out,
                adults=2,
                guest_full_name="Swap Guest B",
                guest_email="swapb@example.com",
                guest_phone="0902000002",
            ),
            idempotency_key=f"swapb-{uuid4().hex}",
        )
        tok_a = (
            await verify_guest_reservation(s, res_a.confirmation_code, "0902000001")
        ).verification_token
        tok_b = (
            await verify_guest_reservation(s, res_b.confirmation_code, "0902000002")
        ).verification_token

    async def mod_a() -> ReservationStatus:
        async with session_factory() as sa:
            out = await modify_reservation(
                sa,
                req=ModifyReservationRequest(
                    confirmation_code=res_a.confirmation_code,
                    verification_token=tok_a,
                    new_check_in_date=win_b_in,
                    new_check_out_date=win_b_out,
                ),
                idempotency_key=f"mod-a-{uuid4().hex}",
            )
            return out.status

    async def mod_b() -> ReservationStatus:
        async with session_factory() as sb:
            out = await modify_reservation(
                sb,
                req=ModifyReservationRequest(
                    confirmation_code=res_b.confirmation_code,
                    verification_token=tok_b,
                    new_check_in_date=win_a_in,
                    new_check_out_date=win_a_out,
                ),
                idempotency_key=f"mod-b-{uuid4().hex}",
            )
            return out.status

    statuses = await asyncio.gather(mod_a(), mod_b())
    assert statuses == [ReservationStatus.CONFIRMED, ReservationStatus.CONFIRMED]


async def test_support_tickets_and_escalation_context(db_session: AsyncSession) -> None:
    """Tickets and escalations persist structured records and conversation snapshot."""
    await seed_database(db_session)
    prop = await db_session.scalar(select(Property).where(Property.code == "SGN"))
    assert prop is not None

    conv = Conversation(
        property_id=prop.id,
        channel="web_chat",
        language="vi",
        status="ACTIVE",
    )
    db_session.add(conv)
    await db_session.commit()

    ticket = await create_support_ticket(
        db_session,
        req=CreateTicketRequest(
            property_code="SGN",
            category=TicketCategory.AIRPORT_TRANSFER,
            priority=TicketPriority.HIGH,
            description="Flight VN123 arriving at 21:30, request sedan pickup.",
            conversation_id=conv.id,
        ),
        idempotency_key=f"tck-{uuid4().hex}",
        trace_id="trace-tck-1",
    )
    assert ticket.ticket_code.startswith("TCK-SGN-")

    escalation = await create_escalation(
        db_session,
        req=CreateEscalationRequest(
            conversation_id=conv.id,
            property_code="SGN",
            reason_code="GROUP_BOOKING_OVER_1_ROOM",
            priority=EscalationPriority.HIGH,
            summary="Guest wants 4 Executive Suites for corporate retreat.",
            context_snapshot={"requested_rooms": 4},
        ),
        idempotency_key=f"esc-{uuid4().hex}",
        trace_id="trace-esc-1",
    )
    assert escalation.escalation_code.startswith("ESC-SGN-")
    assert escalation.context_snapshot["requested_rooms"] == 4
