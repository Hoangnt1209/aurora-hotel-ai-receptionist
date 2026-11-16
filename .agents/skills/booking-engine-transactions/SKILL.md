---
name: booking-engine-transactions
description: >-
  Guides implementation and testing of Aurora's PostgreSQL ACID booking engine,
  daily inventory row locking, rate calculation, idempotency keys, audit logging,
  and concurrency race-condition tests. Use when working on Milestone 1 or Milestone 2.
---

# Deterministic Booking Engine & Transaction Runbook

Apply this skill for PostgreSQL schema design, booking/cancellation/modification domain logic, idempotency, audit trails, and real-DB concurrency testing.

## Core Implementation Pattern

### 1. Night Enumeration & Pricing
- Treat stay dates as half-open `[check_in_date, check_out_date)`.
- Reject `check_in_date >= check_out_date`, past check-in dates, `adults < 1`, or `rooms_count != 1` (group bookings `> 1` must escalate).
- Compute `nightly_rate_vnd = round((inventory.price_override_vnd or room_type.base_rate_vnd) * rate_plan.price_multiplier, 0)`.

### 2. Canonical Row Locking (Deadlock-Free)
When creating, modifying, or cancelling a reservation inside an async SQLAlchemy transaction:
1. Build the exact sorted list of `(room_type_id, stay_date)` pairs needed (for modifications, take the **union** of old nights and new nights).
2. Sort ascending by `(room_type_id, stay_date)`.
3. Execute:
   ```python
   stmt = (
       select(RoomInventoryDaily)
       .where(...)
       .order_by(RoomInventoryDaily.room_type_id.asc(), RoomInventoryDaily.stay_date.asc())
       .with_for_update()
   )
   ```
4. Verify returned row count equals expected night count (reject `INVENTORY_NOT_INITIALIZED` if a night is missing).
5. Re-check `(row.total_rooms - row.reserved_rooms - row.blocked_rooms) >= 1` for every allocated night **after** locking.

### 3. Idempotency Guard
- Hash canonical JSON payload (`sha256`) and pair with `(actor_scope, endpoint, idempotency_key)`.
- Check `idempotency_records` inside the transaction:
  - Missing -> execute business logic, store response status/body, commit.
  - Exists & `request_hash` matches -> return stored response immediately without re-mutating inventory.
  - Exists & `request_hash` differs -> raise `409 IDEMPOTENCY_CONFLICT`.

### 4. Audit Logging & Guest Verification
- Wrap every state transition with an `audit_logs` insert capturing `trace_id`, `actor_type`, `actor_id`, `before_state`, and `after_state`.
- Implement `verify_guest_reservation(confirmation_code, contact)` comparing normalized `phone_e164` or case-insensitive `email` before returning PII or issuing a `verification_token`.

## Validation Steps

1. Run Alembic migrations on a clean PostgreSQL database (`alembic upgrade head` and `downgrade/upgrade` check).
2. Execute `pytest` concurrency tests using `asyncio.gather` against **real PostgreSQL** (10 parallel booking attempts for 1 remaining room -> assert exactly 1 `CONFIRMED` and 9 `INSUFFICIENT_INVENTORY`, and `reserved_rooms == total_rooms`).
3. Verify duplicate `Idempotency-Key` creates only 1 row in `reservations` and 1 increment in `room_inventory_daily`.
