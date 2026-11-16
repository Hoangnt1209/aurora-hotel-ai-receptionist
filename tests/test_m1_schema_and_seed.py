"""Milestone 1 verification tests: PostgreSQL schema, pgvector, seed idempotency, and DB constraints."""

from datetime import date, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from aurora.db.models import (
    Guest,
    Property,
    RatePlan,
    Reservation,
    RoomInventoryDaily,
    RoomType,
)
from aurora.db.seed import seed_database

EXPECTED_TABLES = {
    "properties",
    "room_types",
    "rate_plans",
    "room_inventory_daily",
    "guests",
    "reservations",
    "reservation_nights",
    "support_tickets",
    "conversations",
    "conversation_messages",
    "escalations",
    "idempotency_records",
    "audit_logs",
    "staff_users",
    "policy_chunks",
}


async def test_all_15_tables_and_pgvector_exist(db_session: AsyncSession) -> None:
    """Verify PostgreSQL has pgvector enabled and all 15 domain tables created."""
    ext_result = await db_session.scalar(
        text("SELECT extname FROM pg_extension WHERE extname = 'vector'")
    )
    assert ext_result == "vector"

    rows = await db_session.execute(
        text(
            "SELECT tablename FROM pg_tables "
            "WHERE schemaname = 'public' AND tablename != 'alembic_version'"
        )
    )
    actual_tables = {row[0] for row in rows.fetchall()}
    assert EXPECTED_TABLES.issubset(actual_tables)


async def test_seed_is_idempotent_and_matches_property_capacities(
    db_session: AsyncSession,
) -> None:
    """Running seed_database twice on a clean database yields exact expected counts."""
    await db_session.execute(
        text(
            "TRUNCATE TABLE properties, guests, idempotency_records, audit_logs, "
            "policy_chunks RESTART IDENTITY CASCADE"
        )
    )
    await db_session.commit()

    anchor = date(2026, 11, 16)
    first_counts = await seed_database(db_session, start_date=anchor, days=90)
    second_counts = await seed_database(db_session, start_date=anchor, days=90)

    expected = {
        "properties": 3,
        "room_types": 9,
        "rate_plans": 18,
        "room_inventory_daily": 810,
        "staff_users": 4,
        "policy_chunks": 8,
    }
    assert first_counts == expected
    assert second_counts == expected

    # Verify total room capacities per property (SGN=45, DAD=60, PQC=50)
    cap_rows = await db_session.execute(
        select(Property.code, func.sum(RoomType.total_rooms))
        .join(RoomType, RoomType.property_id == Property.id)
        .group_by(Property.code)
    )
    capacities = {code: int(total) for code, total in cap_rows.fetchall()}
    assert capacities == {"SGN": 45, "DAD": 60, "PQC": 50}


async def test_inventory_rejects_overbooking_check_constraint(
    db_session: AsyncSession,
) -> None:
    """PostgreSQL CHECK constraint rejects reserved + blocked > total_rooms."""
    await seed_database(db_session)
    inv = await db_session.scalar(select(RoomInventoryDaily).limit(1))
    assert inv is not None

    inv.reserved_rooms = inv.total_rooms
    inv.blocked_rooms = 1
    with pytest.raises(IntegrityError):
        await db_session.flush()
    await db_session.rollback()


async def test_inventory_rejects_negative_reserved_rooms(
    db_session: AsyncSession,
) -> None:
    """PostgreSQL CHECK constraint rejects negative reserved_rooms."""
    await seed_database(db_session)
    inv = await db_session.scalar(select(RoomInventoryDaily).limit(1))
    assert inv is not None

    inv.reserved_rooms = -1
    with pytest.raises(IntegrityError):
        await db_session.flush()
    await db_session.rollback()


async def test_inventory_rejects_duplicate_room_type_and_date(
    db_session: AsyncSession,
) -> None:
    """PostgreSQL UNIQUE(room_type_id, stay_date) prevents duplicate daily rows."""
    await seed_database(db_session)
    existing = await db_session.scalar(select(RoomInventoryDaily).limit(1))
    assert existing is not None

    duplicate = RoomInventoryDaily(
        room_type_id=existing.room_type_id,
        stay_date=existing.stay_date,
        total_rooms=existing.total_rooms,
        reserved_rooms=0,
        blocked_rooms=0,
    )
    db_session.add(duplicate)
    with pytest.raises(IntegrityError):
        await db_session.flush()
    await db_session.rollback()


async def test_reservation_rejects_invalid_date_range(
    db_session: AsyncSession,
) -> None:
    """PostgreSQL CHECK constraint rejects check_out_date <= check_in_date."""
    await seed_database(db_session)
    prop = await db_session.scalar(select(Property).where(Property.code == "SGN"))
    room_type = await db_session.scalar(
        select(RoomType).where(RoomType.property_id == prop.id).limit(1)
    )
    rate_plan = await db_session.scalar(
        select(RatePlan).where(RatePlan.room_type_id == room_type.id).limit(1)
    )
    assert prop is not None and room_type is not None and rate_plan is not None

    guest = Guest(
        full_name="Test Constraint Guest",
        email=f"constraint-{uuid4().hex[:8]}@example.com",
        phone_e164="+84901234567",
        preferred_language="vi",
    )
    db_session.add(guest)
    await db_session.flush()

    same_day = date.today() + timedelta(days=5)
    bad_res = Reservation(
        confirmation_code=f"AUR-TST-{uuid4().hex[:6].upper()}",
        property_id=prop.id,
        room_type_id=room_type.id,
        rate_plan_id=rate_plan.id,
        guest_id=guest.id,
        check_in_date=same_day,
        check_out_date=same_day,
        adults=2,
        children=0,
        status="CONFIRMED",
        total_amount_vnd=Decimal("2200000"),
    )
    db_session.add(bad_res)
    with pytest.raises(IntegrityError):
        await db_session.flush()
    await db_session.rollback()
