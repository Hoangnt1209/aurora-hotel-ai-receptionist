"""Idempotent seed data loader for Aurora Hospitality Group (SGN, DAD, PQC)."""

import asyncio
import hashlib
import math
from datetime import date, time, timedelta
from decimal import Decimal
from typing import Any

import bcrypt
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from aurora.db.models import (
    PolicyChunk,
    Property,
    RatePlan,
    RoomInventoryDaily,
    RoomType,
    StaffUser,
)
from aurora.db.session import AsyncSessionLocal

PROPERTIES_DATA: list[dict[str, Any]] = [
    {
        "code": "SGN",
        "name": "Aurora Saigon Central",
        "city": "Ho Chi Minh City",
        "timezone": "Asia/Ho_Chi_Minh",
        "check_in_time": time(14, 0),
        "check_out_time": time(12, 0),
        "currency": "VND",
    },
    {
        "code": "DAD",
        "name": "Aurora My Khe Beach",
        "city": "Da Nang",
        "timezone": "Asia/Ho_Chi_Minh",
        "check_in_time": time(14, 0),
        "check_out_time": time(12, 0),
        "currency": "VND",
    },
    {
        "code": "PQC",
        "name": "Aurora Sunset Bay",
        "city": "Phu Quoc",
        "timezone": "Asia/Ho_Chi_Minh",
        "check_in_time": time(15, 0),
        "check_out_time": time(12, 0),
        "currency": "VND",
    },
]

ROOM_TYPES_DATA: dict[str, list[dict[str, Any]]] = {
    "SGN": [
        {
            "code": "SUP_CITY",
            "name": "Superior City View",
            "description": "Cozy boutique room in District 1 with soundproof city windows.",
            "max_adults": 2,
            "max_children": 1,
            "total_rooms": 18,
            "base_rate_vnd": Decimal("2200000"),
        },
        {
            "code": "DLX_BALCONY",
            "name": "Deluxe Balcony",
            "description": "Spacious room with private Saigon balcony and work desk.",
            "max_adults": 2,
            "max_children": 1,
            "total_rooms": 18,
            "base_rate_vnd": Decimal("2900000"),
        },
        {
            "code": "EXEC_SUITE",
            "name": "Executive Saigon Suite",
            "description": "Corner suite with lounge access and late check-out priority.",
            "max_adults": 3,
            "max_children": 2,
            "total_rooms": 9,
            "base_rate_vnd": Decimal("4600000"),
        },
    ],
    "DAD": [
        {
            "code": "DLX_GARDEN",
            "name": "Deluxe Garden View",
            "description": "Serene tropical garden room steps away from My Khe Beach.",
            "max_adults": 2,
            "max_children": 1,
            "total_rooms": 25,
            "base_rate_vnd": Decimal("2500000"),
        },
        {
            "code": "DLX_OCEAN",
            "name": "Deluxe Ocean View",
            "description": "Panoramic ocean-facing balcony overlooking My Khe sunrise.",
            "max_adults": 2,
            "max_children": 2,
            "total_rooms": 25,
            "base_rate_vnd": Decimal("3400000"),
        },
        {
            "code": "BEACH_SUITE",
            "name": "Beachfront Suite",
            "description": "Two-zone coastal suite with soaking tub and private terrace.",
            "max_adults": 4,
            "max_children": 2,
            "total_rooms": 10,
            "base_rate_vnd": Decimal("5800000"),
        },
    ],
    "PQC": [
        {
            "code": "TROPICAL_DLX",
            "name": "Tropical Bungalow",
            "description": "Private wooden bungalow surrounded by Phu Quoc palms.",
            "max_adults": 2,
            "max_children": 1,
            "total_rooms": 20,
            "base_rate_vnd": Decimal("3200000"),
        },
        {
            "code": "SUNSET_SUITE",
            "name": "Sunset Bay Suite",
            "description": "West-facing suite with unobstructed Gulf of Thailand sunset view.",
            "max_adults": 3,
            "max_children": 2,
            "total_rooms": 20,
            "base_rate_vnd": Decimal("4900000"),
        },
        {
            "code": "POOL_VILLA",
            "name": "Private Pool Villa",
            "description": "Secluded infinity plunge pool villa with dedicated butler support.",
            "max_adults": 4,
            "max_children": 2,
            "total_rooms": 10,
            "base_rate_vnd": Decimal("8500000"),
        },
    ],
}

RATE_PLANS_TEMPLATE: list[dict[str, Any]] = [
    {
        "code": "FLEX_24H",
        "name": "Flexible Rate (Breakfast Included, Free Cancel 24h)",
        "price_multiplier": Decimal("1.0000"),
        "is_refundable": True,
        "free_cancel_hours_before_checkin": 24,
        "breakfast_included": True,
    },
    {
        "code": "NON_REF",
        "name": "Saver Non-Refundable (15% Off, Breakfast Included)",
        "price_multiplier": Decimal("0.8500"),
        "is_refundable": False,
        "free_cancel_hours_before_checkin": 0,
        "breakfast_included": True,
    },
]

STAFF_USERS_DATA: list[dict[str, Any]] = [
    {
        "email": "admin@aurora-hotel.vn",
        "password": "AuroraAdmin!2026",
        "full_name": "Nguyen Minh Tuan",
        "role": "ADMIN",
        "property_code": None,
    },
    {
        "email": "manager.sgn@aurora-hotel.vn",
        "password": "AuroraSgn!2026",
        "full_name": "Tran Thao Vy",
        "role": "MANAGER",
        "property_code": "SGN",
    },
    {
        "email": "reception.dad@aurora-hotel.vn",
        "password": "AuroraDad!2026",
        "full_name": "Le Hoang Nam",
        "role": "RECEPTIONIST",
        "property_code": "DAD",
    },
    {
        "email": "reception.pqc@aurora-hotel.vn",
        "password": "AuroraPqc!2026",
        "full_name": "Pham Khanh Linh",
        "role": "RECEPTIONIST",
        "property_code": "PQC",
    },
]

INITIAL_POLICY_CHUNKS: list[dict[str, str]] = [
    {
        "property_code": "GROUP",
        "doc_id": "group_cancellation_policy_en",
        "section_title": "Cancellation & Modification Rules",
        "language": "en",
        "content": (
            "Flexible Rate (FLEX_24H) reservations may be modified or cancelled free of "
            "charge up to 24 hours prior to the property's standard check-in time. "
            "Non-Refundable (NON_REF) reservations cannot be cancelled or refunded by AI; "
            "special waiver requests must be escalated to front-desk management."
        ),
    },
    {
        "property_code": "GROUP",
        "doc_id": "group_cancellation_policy_vi",
        "section_title": "Chính sách hủy và đổi đặt phòng",
        "language": "vi",
        "content": (
            "Gói giá Linh hoạt (FLEX_24H) được hủy hoặc đổi ngày miễn phí trước giờ nhận "
            "phòng tiêu chuẩn 24 giờ. Gói Tiết kiệm Không hoàn hủy (NON_REF) không hỗ trợ "
            "tự động hủy; mọi yêu cầu ngoại lệ cần chuyển nhân viên lễ tân xem xét."
        ),
    },
    {
        "property_code": "SGN",
        "doc_id": "sgn_checkin_shuttle_en",
        "section_title": "Check-in, Check-out & Tan Son Nhat Airport Transfer",
        "language": "en",
        "content": (
            "Aurora Saigon Central standard check-in is 14:00 and check-out is 12:00. "
            "Private airport sedan transfer from Tan Son Nhat International Airport (SGN) "
            "costs 450,000 VND one-way and requires flight number 12 hours in advance."
        ),
    },
    {
        "property_code": "SGN",
        "doc_id": "sgn_checkin_shuttle_vi",
        "section_title": "Giờ nhận trả phòng & Đưa đón sân bay Tân Sơn Nhất",
        "language": "vi",
        "content": (
            "Aurora Saigon Central nhận phòng từ 14:00 và trả phòng trước 12:00. "
            "Dịch vụ xe riêng đưa đón sân bay Tân Sơn Nhất có giá 450.000 VND một chiều, "
            "vui lòng cung cấp mã chuyến bay trước tối thiểu 12 giờ."
        ),
    },
    {
        "property_code": "DAD",
        "doc_id": "dad_checkin_shuttle_en",
        "section_title": "Check-in, Breakfast & Da Nang Airport Transfer",
        "language": "en",
        "content": (
            "Aurora My Khe Beach check-in starts at 14:00 and check-out is 12:00. "
            "Daily buffet breakfast is served from 06:30 to 10:00. Private transfer from "
            "Da Nang Airport (DAD) is 350,000 VND per vehicle per trip."
        ),
    },
    {
        "property_code": "DAD",
        "doc_id": "dad_checkin_shuttle_vi",
        "section_title": "Nhận phòng, Ăn sáng & Đưa đón sân bay Đà Nẵng",
        "language": "vi",
        "content": (
            "Aurora My Khe Beach nhận phòng từ 14:00 và trả phòng lúc 12:00. Bữa sáng "
            "buffet phục vụ từ 06:30 đến 10:00. Xe đón sân bay Đà Nẵng có giá 350.000 VND/lượt."
        ),
    },
    {
        "property_code": "PQC",
        "doc_id": "pqc_checkin_shuttle_en",
        "section_title": "Resort Check-in & Complimentary Phu Quoc Shuttle",
        "language": "en",
        "content": (
            "Aurora Sunset Bay check-in begins at 15:00 and check-out is 12:00. "
            "Scheduled shared shuttle from Phu Quoc International Airport (PQC) is "
            "complimentary for confirmed guests with flight details submitted 24 hours ahead."
        ),
    },
    {
        "property_code": "PQC",
        "doc_id": "pqc_checkin_shuttle_vi",
        "section_title": "Nhận phòng Resort & Xe đón sân bay Phú Quốc miễn phí",
        "language": "vi",
        "content": (
            "Aurora Sunset Bay nhận phòng từ 15:00 và trả phòng lúc 12:00. Xe đưa đón "
            "sân bay Phú Quốc theo khung giờ cố định được miễn phí cho khách đã xác nhận "
            "và báo trước thông tin chuyến bay 24 giờ."
        ),
    },
]


def _deterministic_placeholder_embedding(text: str, dim: int = 384) -> list[float]:
    """Generate a normalized deterministic 384-dim vector for M1 seed rows."""
    values: list[float] = []
    seed_bytes = text.encode("utf-8")
    counter = 0
    while len(values) < dim:
        digest = hashlib.sha256(seed_bytes + counter.to_bytes(4, "big")).digest()
        for idx in range(0, len(digest), 4):
            if len(values) == dim:
                break
            raw_int = int.from_bytes(digest[idx : idx + 4], "big", signed=False)
            values.append((raw_int / 2**31) - 1.0)
        counter += 1
    norm = math.sqrt(sum(v * v for v in values)) or 1.0
    return [round(v / norm, 6) for v in values]


def _hash_password(plain_password: str) -> str:
    return bcrypt.hashpw(plain_password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


async def seed_database(
    session: AsyncSession,
    start_date: date | None = None,
    days: int = 90,
) -> dict[str, int]:
    """Populate initial properties, rooms, rate plans, inventory, staff, and policies idempotently."""
    anchor_date = start_date or date.today()

    # 1. Properties
    await session.execute(
        pg_insert(Property)
        .values(PROPERTIES_DATA)
        .on_conflict_do_nothing(index_elements=["code"])
    )
    prop_rows = (await session.execute(select(Property))).scalars().all()
    prop_by_code = {p.code: p for p in prop_rows}

    # 2. Room Types
    room_type_values: list[dict[str, Any]] = []
    for prop_code, rooms in ROOM_TYPES_DATA.items():
        prop_id = prop_by_code[prop_code].id
        for room in rooms:
            room_type_values.append({"property_id": prop_id, **room})

    await session.execute(
        pg_insert(RoomType)
        .values(room_type_values)
        .on_conflict_do_nothing(index_elements=["property_id", "code"])
    )
    room_types = (await session.execute(select(RoomType))).scalars().all()

    # 3. Rate Plans
    rate_plan_values: list[dict[str, Any]] = []
    for rt in room_types:
        for tpl in RATE_PLANS_TEMPLATE:
            rate_plan_values.append({"room_type_id": rt.id, **tpl})

    await session.execute(
        pg_insert(RatePlan)
        .values(rate_plan_values)
        .on_conflict_do_nothing(index_elements=["room_type_id", "code"])
    )

    # 4. Daily Inventory (anchor_date .. anchor_date + days - 1)
    inventory_values: list[dict[str, Any]] = []
    for rt in room_types:
        for offset in range(days):
            stay_day = anchor_date + timedelta(days=offset)
            # Friday (4) and Saturday (5) carry a +15% weekend rate override
            is_weekend = stay_day.weekday() in (4, 5)
            override = (
                (rt.base_rate_vnd * Decimal("1.15")).quantize(Decimal("1"))
                if is_weekend
                else None
            )
            inventory_values.append(
                {
                    "room_type_id": rt.id,
                    "stay_date": stay_day,
                    "total_rooms": rt.total_rooms,
                    "reserved_rooms": 0,
                    "blocked_rooms": 0,
                    "price_override_vnd": override,
                    "version": 1,
                }
            )

    await session.execute(
        pg_insert(RoomInventoryDaily)
        .values(inventory_values)
        .on_conflict_do_nothing(index_elements=["room_type_id", "stay_date"])
    )

    # 5. Staff Users
    staff_values: list[dict[str, Any]] = []
    for staff in STAFF_USERS_DATA:
        prop_code = staff["property_code"]
        staff_values.append(
            {
                "email": staff["email"],
                "password_hash": _hash_password(staff["password"]),
                "full_name": staff["full_name"],
                "role": staff["role"],
                "property_id": prop_by_code[prop_code].id if prop_code else None,
                "is_active": True,
            }
        )

    await session.execute(
        pg_insert(StaffUser)
        .values(staff_values)
        .on_conflict_do_nothing(index_elements=["email"])
    )

    # 6. Policy Chunks (initial baseline; M3 adds full corpus & semantic embeddings)
    policy_values: list[dict[str, Any]] = []
    for chunk in INITIAL_POLICY_CHUNKS:
        policy_values.append(
            {
                **chunk,
                "embedding": _deterministic_placeholder_embedding(
                    f"{chunk['property_code']}::{chunk['section_title']}::{chunk['content']}"
                ),
                "chunk_metadata": {"source": "m1_baseline", "version": "1.0"},
            }
        )

    await session.execute(
        pg_insert(PolicyChunk)
        .values(policy_values)
        .on_conflict_do_nothing(index_elements=["property_code", "doc_id", "language"])
    )

    await session.commit()

    return {
        "properties": await session.scalar(select(func.count()).select_from(Property)) or 0,
        "room_types": await session.scalar(select(func.count()).select_from(RoomType)) or 0,
        "rate_plans": await session.scalar(select(func.count()).select_from(RatePlan)) or 0,
        "room_inventory_daily": await session.scalar(
            select(func.count()).select_from(RoomInventoryDaily)
        )
        or 0,
        "staff_users": await session.scalar(select(func.count()).select_from(StaffUser)) or 0,
        "policy_chunks": await session.scalar(select(func.count()).select_from(PolicyChunk)) or 0,
    }


async def _main() -> None:
    async with AsyncSessionLocal() as session:
        counts = await seed_database(session)
        print(f"Aurora seed complete: {counts}")


if __name__ == "__main__":
    asyncio.run(_main())
