# Aurora Domain, Transaction & Security Invariants

These invariants apply across all backend services, LangGraph tools, voice handlers, and tests.

---

## 1. Booking & Inventory Transaction Invariants

1. **Daily Night Accounting (Half-Open Interval `[check_in_date, check_out_date)`):**
   - Night `check_out_date` is never allocated.
   - `available_rooms = total_rooms - reserved_rooms - blocked_rooms`.
   - Enforce DB `CHECK` constraint: `reserved_rooms >= 0 AND blocked_rooms >= 0 AND (reserved_rooms + blocked_rooms) <= total_rooms`.
2. **Mandatory Canonical Lock Ordering (Deadlock Prevention):**
   - Before any `INSERT`/`UPDATE`/`DELETE` affecting `room_inventory_daily`, acquire `SELECT ... FROM room_inventory_daily ... ORDER BY room_type_id ASC, stay_date ASC FOR UPDATE`.
   - For modifications touching old and new dates/rooms, lock the sorted union of old and new `(room_type_id, stay_date)` rows in a single transaction.
3. **Re-Check Under Lock:**
   - Never rely on an earlier `check_availability` call when committing a reservation. Re-verify `available_rooms >= 1` after acquiring `FOR UPDATE` locks.
4. **Idempotency Requirement:**
   - Every mutating reservation/ticket/escalation API and agent mutation tool requires `Idempotency-Key`.
   - Persist `(actor_scope, endpoint, idempotency_key, request_hash, response_status, response_body)` inside the same DB transaction or idempotency guard.
   - Identical payload replay returns the stored result without side effects; different payload with same key raises `409 IDEMPOTENCY_CONFLICT`.

---

## 2. Reservation State Machine Rules

- Allowed states: `PENDING_CONFIRMATION`, `CONFIRMED`, `CHECKED_IN`, `CHECKED_OUT`, `CANCELLED`, `NO_SHOW`, `EXPIRED`.
- Terminal states (`CHECKED_OUT`, `CANCELLED`, `NO_SHOW`, `EXPIRED`) reject all further mutations with `409 INVALID_STATE_TRANSITION`.
- `AI_AGENT` principal may **only** create `CONFIRMED` (after guest confirmation), modify eligible `CONFIRMED`, or cancel `CONFIRMED` bookings within refundable window (`rate_plans.is_refundable == True` and before `free_cancel_hours_before_checkin`).
- `CHECKED_IN`, `CHECKED_OUT`, `NO_SHOW`, and non-refundable penalty overrides require a `STAFF` JWT principal (`RECEPTIONIST`, `MANAGER`, `ADMIN`).

---

## 3. Structured Tool Contract & Zero Hallucination

- Every agent tool must return a typed Pydantic `ToolResult` envelope:
  - `ok: bool`
  - `data: dict | BaseModel | None`
  - `error_code: str | None` (e.g., `INSUFFICIENT_INVENTORY`, `IDENTITY_VERIFICATION_FAILED`, `CANCELLATION_WINDOW_EXPIRED`, `PROPERTY_NOT_FOUND`)
  - `message: str`
  - `trace_id: str`
- Tools must never accept `confirmation_code`, `total_amount_vnd`, or `status` as caller-supplied prices/codes for new bookings; the backend computes and generates them.

---

## 4. Security & Privacy Boundaries

1. **Guest Verification Gate:**
   - Never reveal guest PII (`full_name`, `email`, `phone_e164`) or mutate an existing reservation solely from a `confirmation_code`.
   - Require `verify_guest_reservation(confirmation_code, phone_or_email)` to succeed and issue a conversation-scoped `verification_token` first.
2. **Confirmation Gate:**
   - The AI agent must present exact property, dates, room type, rate plan, and total VND and obtain explicit user approval before executing `create_reservation`, `modify_reservation`, or `cancel_reservation`.
3. **Audit Logging:**
   - Every state change (`RESERVATION_CREATED`, `RESERVATION_MODIFIED`, `RESERVATION_CANCELLED`, `CHECK_IN`, `CHECK_OUT`, `TICKET_CREATED`, `ESCALATION_CREATED`, `ESCALATION_RESOLVED`) must write an immutable `audit_logs` row with `trace_id`, `actor_type`, `actor_id`, `before_state`, and `after_state`.
4. **No Arbitrary Text-to-SQL & No Raw Payment Cards:**
   - Reject raw PAN/CVV inputs; never expose SQL generation to the LLM.
