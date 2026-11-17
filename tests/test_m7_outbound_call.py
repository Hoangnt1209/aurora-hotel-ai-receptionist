"""Tests for outbound phone call dispatching, VieNeu-TTS synthesis, and phone number validation."""

import httpx
import pytest
from httpx import ASGITransport, AsyncClient, Response as HttpxResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aurora.db.models import AuditLog, Conversation, ConversationMessage
from aurora.db.seed import seed_database
from aurora.domain.errors import DomainError
from aurora.main import app
from aurora.services.outbound_call import (
    OutboundCallRequest,
    dispatch_outbound_call,
    normalize_vietnamese_phone,
)
from aurora.voice.vieneu_tts import get_audio_wav


def test_normalize_vietnamese_phone_formats():
    """Verify phone normalization handles common Vietnamese mobile formats."""
    assert normalize_vietnamese_phone("0989068343") == "+84989068343"
    assert normalize_vietnamese_phone("+84989068343") == "+84989068343"
    assert normalize_vietnamese_phone("+840989068343") == "+84989068343"
    assert normalize_vietnamese_phone("84989068343") == "+84989068343"
    assert normalize_vietnamese_phone("840989068343") == "+84989068343"
    assert normalize_vietnamese_phone("098 906 8343") == "+84989068343"
    assert normalize_vietnamese_phone("098-906-8343") == "+84989068343"
    assert normalize_vietnamese_phone("0907123456") == "+84907123456"

    # Invalid cases
    with pytest.raises(DomainError) as exc_info:
        normalize_vietnamese_phone("012345")
    assert exc_info.value.error_code == "INVALID_PHONE_NUMBER"

    with pytest.raises(DomainError) as exc_info2:
        normalize_vietnamese_phone("0123456789")  # 01 prefix is discontinued in Vietnam
    assert exc_info2.value.error_code == "INVALID_PHONE_NUMBER"


async def test_dispatch_outbound_call_synthesizes_voice_and_records_audit(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Outbound call request synthesizes 48kHz VieNeu-TTS WAV, creates conversation, and writes audit log."""
    await seed_database(db_session)

    orig_post = httpx.AsyncClient.post

    async def mock_post(self, url, **kwargs):
        if "api.twilio.com" in str(url):
            return HttpxResponse(201, json={"sid": "CAmock123456789", "status": "queued"})
        return await orig_post(self, url, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "post", mock_post)

    req = OutboundCallRequest(
        to_phone="0989068343",
        message="Xin chào mình là nhân viên từ khách sạn gọi đến để xác nhận",
        property_code="SGN",
    )

    result = await dispatch_outbound_call(
        session=db_session,
        req=req,
        trace_id="test-outbound-trace-001",
    )

    assert result.ok is True
    assert result.to_phone == "+84989068343"
    assert result.property_code == "SGN"
    assert result.voice_preset == "Thùy Dung"
    assert result.tts_engine == "vieneu-v3-turbo"
    assert result.audio_url.startswith("/api/v1/telephony/tts/")
    assert result.audio_url.endswith(".wav")
    assert result.byte_length > 0

    # Verify audio is cached and valid WAV
    wav_bytes = get_audio_wav(result.audio_id)
    assert wav_bytes is not None
    assert wav_bytes[:4] == b"RIFF"
    assert b"WAVE" in wav_bytes[:16]

    # Verify Conversation was stored in PostgreSQL
    conv = await db_session.scalar(
        select(Conversation).where(Conversation.id == result.conversation_id)
    )
    assert conv is not None
    assert conv.channel == "phone_outbound"
    assert conv.external_session_id == "+84989068343"
    assert conv.status == "ACTIVE"

    # Verify initial greeting utterance was saved
    msg = await db_session.scalar(
        select(ConversationMessage).where(ConversationMessage.conversation_id == conv.id)
    )
    assert msg is not None
    assert msg.role == "assistant"
    assert "gọi đến để xác nhận" in msg.content

    # Verify AuditLog record
    audit = await db_session.scalar(
        select(AuditLog).where(
            AuditLog.entity_id == str(conv.id),
            AuditLog.action == "OUTBOUND_PHONE_CALL",
        )
    )
    assert audit is not None
    assert audit.actor_type == "AI_RECEPTIONIST"
    assert audit.after_state["to_phone"] == "+84989068343"


async def test_api_outbound_call_endpoint(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify HTTP POST /api/v1/telephony/outbound-call endpoint."""
    await seed_database(db_session)

    orig_post = httpx.AsyncClient.post

    async def mock_post(self, url, **kwargs):
        if "api.twilio.com" in str(url):
            return HttpxResponse(201, json={"sid": "CAmockendpoint999", "status": "queued"})
        return await orig_post(self, url, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "post", mock_post)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/telephony/outbound-call",
            json={
                "to_phone": "0989068343",
                "message": "Xin chào, em là nhân viên khách sạn Aurora gọi để hỗ trợ quý khách.",
                "property_code": "DAD",
            },
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["data"]["to_phone"] == "+84989068343"
        assert body["data"]["property_code"] == "DAD"
        assert body["data"]["voice_preset"] == "Ngọc Trân"
        assert body["data"]["audio_url"].endswith(".wav")
