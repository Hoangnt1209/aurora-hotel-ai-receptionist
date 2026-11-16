"""Milestone 6 end-to-end verification: FastAPI gateway, RBAC boundaries, voice/chat API, and audit trace."""

import re
from collections.abc import AsyncGenerator
from datetime import date, timedelta
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aurora.db.seed import seed_database
from aurora.db.session import get_db_session
from aurora.main import app
from aurora.rag.retriever import ingest_policy_corpus
from aurora.services.verification import issue_verification_token


@pytest.fixture
async def api_client(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncGenerator[AsyncClient, None]:
    """Provide an HTTPX AsyncClient wired to NullPool PostgreSQL sessions."""
    async with session_factory() as s:
        await seed_database(s)
        await ingest_policy_corpus(s)

    async def _override_db() -> AsyncGenerator[AsyncSession, None]:
        async with session_factory() as session:
            try:
                yield session
            finally:
                await session.rollback()

    app.dependency_overrides[get_db_session] = _override_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    app.dependency_overrides.clear()


async def test_health_ui_and_properties_endpoints(api_client: AsyncClient) -> None:
    """Verify /health, / web console UI, and /api/v1/properties."""
    health = await api_client.get("/health")
    assert health.status_code == 200
    assert health.json()["status"] == "ok"
    assert health.json()["database"] == "connected"
    assert "X-Trace-Id" in health.headers

    ui = await api_client.get("/")
    assert ui.status_code == 200
    assert "Aurora Hospitality Group" in ui.text

    props = await api_client.get("/api/v1/properties")
    assert props.status_code == 200
    codes = {p["code"] for p in props.json()}
    assert codes == {"SGN", "DAD", "PQC"}


async def test_staff_rbac_and_property_scope_enforcement(
    api_client: AsyncClient,
) -> None:
    """Verify unauthenticated, guest-token, role-restricted, and cross-property blocks."""
    # 1. Unauthenticated caller -> 401
    r_unauth = await api_client.get("/api/v1/staff/dashboard")
    assert r_unauth.status_code == 401

    # 2. Guest verification token on staff endpoint -> 403 RBAC_FORBIDDEN
    guest_tok, _ = issue_verification_token("AUR-SGN-123456")
    r_guest = await api_client.get(
        "/api/v1/staff/dashboard",
        headers={"Authorization": f"Bearer {guest_tok}"},
    )
    assert r_guest.status_code == 403
    assert r_guest.json()["detail"]["error_code"] == "RBAC_FORBIDDEN"

    # 3. DAD Receptionist login
    login_dad = await api_client.post(
        "/api/v1/auth/login",
        json={"email": "reception.dad@aurora-hotel.vn", "password": "AuroraDad!2026"},
    )
    assert login_dad.status_code == 200
    dad_token = login_dad.json()["access_token"]
    dad_headers = {"Authorization": f"Bearer {dad_token}"}

    # 4. Receptionist cannot view audit logs (MANAGER/ADMIN only)
    r_audit = await api_client.get("/api/v1/staff/audit-logs", headers=dad_headers)
    assert r_audit.status_code == 403
    assert r_audit.json()["detail"]["error_code"] == "INSUFFICIENT_ROLE"

    # 5. DAD Receptionist cannot query SGN property reservations
    r_cross = await api_client.get(
        "/api/v1/staff/reservations?property_code=SGN", headers=dad_headers
    )
    assert r_cross.status_code == 403
    assert r_cross.json()["detail"]["error_code"] == "FORBIDDEN_PROPERTY_SCOPE"


async def test_full_end_to_end_guest_to_staff_lifecycle(
    api_client: AsyncClient,
) -> None:
    """E2E: Chat book -> Ticket -> Voice barge-in -> Admin login -> Check-in -> Audit log."""
    ci = (date.today() + timedelta(days=30)).isoformat()
    co = (date.today() + timedelta(days=32)).isoformat()
    guest_email = f"e2e-{uuid4().hex[:6]}@example.com"

    # 1. Guest Chat Turn 1 -> Quote & Confirmation Gate
    c1 = await api_client.post(
        "/api/v1/chat/turn",
        json={
            "message": (
                f"Book Superior room in Saigon from {ci} to {co} for 2 adults, "
                f"name Tran Gia Huy, phone 0903456789, email {guest_email}"
            ),
            "property_code": "SGN",
        },
        headers={"X-Trace-Id": "e2e-trace-chat-1"},
    )
    assert c1.status_code == 200
    assert c1.headers["X-Trace-Id"] == "e2e-trace-chat-1"
    c1_body = c1.json()
    assert c1_body["requires_confirmation"] is True
    conv_id = c1_body["conversation_id"]

    # 2. Guest Chat Turn 2 -> Confirm booking
    c2 = await api_client.post(
        "/api/v1/chat/turn",
        json={"message": "Yes, confirm", "conversation_id": conv_id},
    )
    assert c2.status_code == 200
    conf_code = c2.json()["tool_traces"][-1]["data"]["confirmation_code"]
    assert conf_code.startswith("AUR-SGN-")

    # 3. Guest creates an Airport Transfer ticket via chat
    c3 = await api_client.post(
        "/api/v1/chat/turn",
        json={
            "message": f"Please arrange airport pickup from Tan Son Nhat for flight VN210 for {conf_code}",
            "conversation_id": conv_id,
            "property_code": "SGN",
        },
    )
    assert c3.status_code == 200
    ticket_code = c3.json()["tool_traces"][-1]["data"]["ticket_code"]
    assert ticket_code.startswith("TCK-SGN-")

    # 4. Voice turn + Barge-in endpoint check
    v1 = await api_client.post(
        "/api/v1/voice/turn",
        json={
            "transcript": "Xin chào, giờ nhận phòng ở Sài Gòn là mấy giờ?",
            "property_code": "SGN",
            "stt_ms": 110.5,
        },
    )
    assert v1.status_code == 200
    voice_conv_id = v1.json()["conversation_id"]

    barge = await api_client.post(
        "/api/v1/voice/barge-in",
        json={"conversation_id": voice_conv_id},
    )
    assert barge.status_code == 200
    assert barge.json()["state"] == "LISTENING"

    # 5. Admin login -> Dashboard -> Check-In -> Resolve Ticket -> Audit Trail
    login = await api_client.post(
        "/api/v1/auth/login",
        json={"email": "admin@aurora-hotel.vn", "password": "AuroraAdmin!2026"},
    )
    assert login.status_code == 200
    admin_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    dash = await api_client.get("/api/v1/staff/dashboard", headers=admin_headers)
    assert dash.status_code == 200
    assert dash.json()["metrics"]["total_reservations"] >= 1

    checkin = await api_client.post(
        f"/api/v1/staff/reservations/{conf_code}/status",
        json={"target_status": "CHECKED_IN", "reason": "Guest arrived at front desk"},
        headers={**admin_headers, "X-Trace-Id": "e2e-trace-checkin"},
    )
    assert checkin.status_code == 200
    assert checkin.json()["data"]["status"] == "CHECKED_IN"

    resolve_tck = await api_client.patch(
        f"/api/v1/staff/tickets/{ticket_code}",
        json={"status": "RESOLVED", "note": "Sedan driver assigned"},
        headers=admin_headers,
    )
    assert resolve_tck.status_code == 200
    assert resolve_tck.json()["data"]["status"] == "RESOLVED"

    audits = await api_client.get("/api/v1/staff/audit-logs?limit=50", headers=admin_headers)
    assert audits.status_code == 200
    trace_ids = {a["trace_id"] for a in audits.json()}
    assert "e2e-trace-checkin" in trace_ids


async def test_telephony_twilio_webhook_and_mobile_call_ui(
    api_client: AsyncClient,
) -> None:
    """Verify /call dialer UI, VieNeu TTS WAV endpoint, and Twilio TwiML PSTN webhooks."""
    dialer = await api_client.get("/call")
    assert dialer.status_code == 200
    assert "VieNeu-TTS v3 Turbo" in dialer.text
    assert "1900-AURORA" in dialer.text

    # Incoming phone call -> TwiML with <Gather bargeIn="true"> and <Play> WAV
    incoming = await api_client.post(
        "/api/v1/telephony/twilio/incoming",
        data={"CallSid": "CA_TEST_001", "From": "+84901234567", "To": "+842899998888"},
    )
    assert incoming.status_code == 200
    assert "application/xml" in incoming.headers["content-type"]
    assert "<Gather" in incoming.text and 'bargeIn="true"' in incoming.text

    play_match = re.search(r"<Play>(https?://[^<]+/api/v1/telephony/tts/[^<]+\.wav)</Play>", incoming.text)
    assert play_match is not None
    wav_path = "/" + play_match.group(1).split("/", 3)[-1]
    wav_resp = await api_client.get(wav_path)
    assert wav_resp.status_code == 200
    assert wav_resp.headers["content-type"] == "audio/wav"
    assert wav_resp.content[:4] == b"RIFF"

    # Caller speaks via phone -> Twilio sends SpeechResult to /gather
    gather = await api_client.post(
        "/api/v1/telephony/twilio/gather",
        data={
            "CallSid": "CA_TEST_001",
            "SpeechResult": "Xe đón sân bay Tân Sơn Nhất giá bao nhiêu?",
        },
    )
    assert gather.status_code == 200
    assert "<Play>" in gather.text and ".wav</Play>" in gather.text
