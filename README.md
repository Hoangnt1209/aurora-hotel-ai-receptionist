# Aurora AI Hotel Receptionist

Production-oriented **AI Virtual Receptionist & Booking Operations Platform** built for **Aurora Hospitality Group** (`SGN` Aurora Saigon Central, `DAD` Aurora My Khe Beach, `PQC` Aurora Sunset Bay).

## Architecture: Deterministic Core + Probabilistic Shell

- **Deterministic Core (FastAPI + PostgreSQL 16 + SQLAlchemy 2.0 Async + Alembic):**
  - Daily room inventory ledger (`room_inventory_daily`) with database `CHECK` constraints preventing overbooking.
  - Deadlock-free `SELECT ... FOR UPDATE` sorted by `(room_type_id ASC, stay_date ASC)` across half-open `[check_in_date, check_out_date)` stay nights.
  - SHA-256 `Idempotency-Key` guard with PostgreSQL advisory lock serialization.
  - Signed guest identity verification (`confirmation_code` + normalized phone/email) prior to PII access or booking mutations.
  - Immutable `audit_logs` with `trace_id` correlation, support tickets, and human escalation handoff snapshots.
- **Probabilistic Shell (LangGraph + Dual-Mode Gemini/OpenAI LLM + `pgvector` RAG + VieNeu-TTS v3 Turbo Voice):**
  - Compiled `langgraph.graph.StateGraph` supporting live **Google Gemini 2.5 Flash** or **OpenAI `gpt-4o-mini`** (via `.env` keys or console UI headers) with deterministic safety fallback when offline, enforcing Prompt Injection guards, Group Booking auto-escalation (`2+` rooms), Verification Gate, Confirmation Gate, and Output Code Verification.
  - Property-scoped hybrid `pgvector` policy RAG (`WHERE property_code IN (:prop, 'GROUP')`) with automatic abstention on out-of-scope queries.
  - Voice controller powered by **VieNeu-TTS v3 Turbo** (`48 kHz` regional presets: `Thùy Dung` SGN, `Ngọc Trân` DAD, `Mai Anh` PQC), spoken VND/weekday Read-Back Protocol, VAD Barge-In cancellation, LiveKit JWT issuance (`POST /api/v1/voice/livekit-token`), mobile call dialer (`/call`), and Twilio PSTN TwiML webhooks (`/api/v1/telephony/twilio/*`).
- **Staff RBAC Operations & Unified Web Console:**
  - JWT Role-Based Access Control (`RECEPTIONIST`, `MANAGER`, `ADMIN`) with property scoping and `AI_AGENT` principal rejection on staff endpoints.
  - Single-page 3-in-1 console served at `/` (Guest Chat + Real-Time Voice + Staff Operations Dashboard).

## Quickstart (Local Development)

```bash
# 1. Start PostgreSQL 16 + pgvector
docker compose up -d postgres --wait

# 2. Install dependencies into virtual environment
uv venv .venv --python 3.11
uv pip install -e . --python .venv/Scripts/python.exe

# 3. Run Alembic migrations & idempotent seed
.venv/Scripts/alembic.exe upgrade head
.venv/Scripts/python.exe -m aurora.db.seed

# 4. Run full real-PostgreSQL test suite (M1-M6)
.venv/Scripts/pytest.exe -v

# 5. Launch FastAPI & Aurora Hospitality Console at http://localhost:8000
.venv/Scripts/uvicorn.exe aurora.main:app --reload --port 8000
```

Or launch the full containerized stack (`postgres` + `api`):

```bash
docker compose up --build
```

## Demo Staff Accounts (Seeded)

| Role | Property Scope | Email | Password |
| :--- | :--- | :--- | :--- |
| `ADMIN` | All (`SGN`, `DAD`, `PQC`) | `admin@aurora-hotel.vn` | `AuroraAdmin!2026` |
| `MANAGER` | `SGN` | `manager.sgn@aurora-hotel.vn` | `AuroraSgn!2026` |
| `RECEPTIONIST` | `DAD` | `reception.dad@aurora-hotel.vn` | `AuroraDad!2026` |
| `RECEPTIONIST` | `PQC` | `reception.pqc@aurora-hotel.vn` | `AuroraPqc!2026` |

## Milestone Progression (Staged Engineering History)

Each stage is preserved as an atomic commit and Git tag so reviewers can inspect the system layer by layer:

| Tag | Milestone | Scope & Verification |
| :--- | :--- | :--- |
| `m0-architecture` | **M0: Blueprint** | Requirements, ER & state machine diagrams, trust boundaries, `.agents/` rules & skills |
| `m1-database-schema` | **M1: Data Foundation** | 15 PostgreSQL + `pgvector` tables, Alembic migrations, `CHECK` constraints, idempotent seed |
| `m2-booking-engine` | **M2: Booking Core** | `SELECT ... FOR UPDATE` sorted locking, SHA-256 idempotency, verification tokens, audit logs, 10-way race test |
| `m3-policy-rag` | **M3: Policy RAG** | Bilingual `pgvector` hybrid retrieval, `property_code` SQL filtering, abstention threshold & 20-query eval |
| `m4-langgraph-agent` | **M4: LangGraph Agent** | 4-node `StateGraph`, prompt-injection & group escalation guards, verification & confirmation gates |
| `m5-voice-receptionist` | **M5: Voice Pipeline** | Spoken VND/weekday read-back protocol, phonetic codes, VAD barge-in TTS cancellation |
| `m6-production-console` | **M6: Gateway & UI** | FastAPI REST gateway, JWT RBAC (`RECEPTIONIST`/`MANAGER`/`ADMIN`), `X-Trace-Id`, 3-in-1 Web Console, E2E suite |

## Architecture Documentation

- [00_ARCHITECTURE_AND_REQUIREMENTS.md](./docs/00_ARCHITECTURE_AND_REQUIREMENTS.md) — Canonical system blueprint, Mermaid ER/state/sequence diagrams, security boundaries, and evaluation strategy.
