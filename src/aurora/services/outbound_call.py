"""Outbound telephony service connecting VieNeu-TTS v3 Turbo, LangGraph, and carrier PSTN/Twilio."""

import re
from typing import Any
from uuid import UUID, uuid4

import httpx
from xml.sax.saxutils import escape as xml_escape

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aurora.config import get_settings
from aurora.db.models import AuditLog, Conversation, ConversationMessage, Property
from aurora.domain.errors import DomainError
from aurora.voice.vieneu_tts import VieNeuTTSProvider


class OutboundCallRequest(BaseModel):
    """Payload to trigger an outbound AI phone call to a guest."""

    to_phone: str = Field(..., description="Target phone number (e.g. 0989068343 or +84989068343)")
    message: str = Field(
        default="Xin chào, mình là nhân viên từ khách sạn Aurora gọi đến để xác nhận thông tin đặt phòng.",
        min_length=5,
        max_length=2000,
        description="Greeting spoken by AI receptionist upon pick-up",
    )
    property_code: str = Field(default="SGN", max_length=10)
    voice_preset: str | None = Field(default=None, description="Optional VieNeu voice override")


class OutboundCallResult(BaseModel):
    """Structured response detailing call dispatch or simulation status."""

    ok: bool
    status: str  # DIALING_CARRIER | SYNTHESIZED_SIMULATION
    to_phone: str
    property_code: str
    voice_preset: str
    tts_engine: str
    tts_backend: str
    audio_url: str
    audio_id: str
    byte_length: int
    conversation_id: str
    carrier_status: str
    carrier_call_sid: str | None = None
    note: str
    trace_id: str


def normalize_vietnamese_phone(raw_phone: str) -> str:
    """Normalize and validate Vietnamese mobile numbers into E.164 (+84...) format."""
    clean = re.sub(r"[\s\-\.\(\)]", "", raw_phone)
    if clean.startswith("+840"):
        clean = "+84" + clean[4:]
    elif clean.startswith("840"):
        clean = "+84" + clean[3:]
    elif clean.startswith("0"):
        clean = "+84" + clean[1:]
    elif clean.startswith("84") and not clean.startswith("+84"):
        clean = "+" + clean

    # Vietnamese mobile phone pattern: +84 followed by 3, 5, 7, 8, 9 and 8 digits
    pattern = r"^\+84(3|5|7|8|9)\d{8}$"
    if not re.match(pattern, clean):
        raise DomainError(
            error_code="INVALID_PHONE_NUMBER",
            message=f"Số điện thoại '{raw_phone}' không hợp lệ. Vui lòng nhập số di động Việt Nam (vd: 0989068343).",
            status_code=400,
            details={"raw": raw_phone, "normalized": clean},
        )
    return clean


async def dispatch_outbound_call(
    session: AsyncSession,
    req: OutboundCallRequest,
    trace_id: str,
) -> OutboundCallResult:
    """Synthesize greeting with VieNeu-TTS v3 Turbo, record session, and dispatch to carrier or simulation."""
    settings = get_settings()
    normalized_phone = normalize_vietnamese_phone(req.to_phone)
    prop_code = req.property_code.strip().upper()

    # 1. Synthesize greeting with VieNeu-TTS v3 Turbo
    tts_provider = VieNeuTTSProvider()
    tts_res = await tts_provider.synthesize_wav(
        req.message,
        property_code=prop_code,
        voice_preset=req.voice_preset,
    )
    audio_id = tts_res["audio_id"]
    audio_url = tts_res["audio_url"]

    # 2. Lookup property
    prop = (
        await session.execute(
            select(Property).where(Property.code == prop_code)
        )
    ).scalar_one_or_none()

    # 3. Create conversation in database
    conv = Conversation(
        property_id=prop.id if prop else None,
        channel="phone_outbound",
        language="vi",
        status="ACTIVE",
        external_session_id=normalized_phone,
    )
    session.add(conv)
    await session.flush()

    # Record first assistant greeting utterance
    msg = ConversationMessage(
        conversation_id=conv.id,
        role="assistant",
        content=req.message,
        trace_id=trace_id,
    )
    session.add(msg)

    # 4. Insert audit log
    audit = AuditLog(
        trace_id=trace_id,
        actor_type="AI_RECEPTIONIST",
        actor_id="aurora-outbound",
        action="OUTBOUND_PHONE_CALL",
        entity_type="CONVERSATION",
        entity_id=str(conv.id),
        before_state=None,
        after_state={
            "to_phone": normalized_phone,
            "property_code": prop_code,
            "voice_preset": tts_res["voice_preset"],
            "tts_backend": tts_res["tts_backend"],
            "audio_id": audio_id,
        },
    )
    session.add(audit)
    await session.commit()

    # 5. Check if Twilio / Carrier credentials are configured to ring physical handset
    has_twilio = bool(
        settings.twilio_account_sid
        and settings.twilio_auth_token
        and settings.twilio_from_phone
    )

    if has_twilio:
        base_url = (settings.public_base_url or "http://localhost:8000").rstrip("/")
        twiml_url = f"{base_url}/api/v1/telephony/twilio/outbound-twiml?conv_id={conv.id}&audio_id={audio_id}"

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    f"https://api.twilio.com/2010-04-01/Accounts/{settings.twilio_account_sid}/Calls.json",
                    auth=(settings.twilio_account_sid, settings.twilio_auth_token),
                    data={
                        "To": normalized_phone,
                        "From": settings.twilio_from_phone,
                        "Url": twiml_url,
                    },
                )
                carrier_data = resp.json() if resp.status_code < 500 else {}
                call_sid = carrier_data.get("sid")
                carrier_status = carrier_data.get("status", "queued")

                if resp.is_success:
                    return OutboundCallResult(
                        ok=True,
                        status="DIALING_CARRIER",
                        to_phone=normalized_phone,
                        property_code=prop_code,
                        voice_preset=tts_res["voice_preset"],
                        tts_engine=tts_res["tts_engine"],
                        tts_backend=tts_res["tts_backend"],
                        audio_url=audio_url,
                        audio_id=audio_id,
                        byte_length=tts_res["byte_length"],
                        conversation_id=str(conv.id),
                        carrier_status=carrier_status,
                        carrier_call_sid=call_sid,
                        note="Cuộc gọi đã được đẩy tới tổng đài viễn thông Twilio PSTN để rung chuông số điện thoại.",
                        trace_id=trace_id,
                    )
                else:
                    err_msg = carrier_data.get("message") or f"HTTP {resp.status_code}"
                    err_code = carrier_data.get("code")
                    return OutboundCallResult(
                        ok=False,
                        status="CARRIER_REJECTED",
                        to_phone=normalized_phone,
                        property_code=prop_code,
                        voice_preset=tts_res["voice_preset"],
                        tts_engine=tts_res["tts_engine"],
                        tts_backend=tts_res["tts_backend"],
                        audio_url=audio_url,
                        audio_id=audio_id,
                        byte_length=tts_res["byte_length"],
                        conversation_id=str(conv.id),
                        carrier_status=f"rejected_code_{err_code}",
                        carrier_call_sid=None,
                        note=f"Twilio từ chối cuộc gọi: {err_msg}",
                        trace_id=trace_id,
                    )
        except Exception as exc:
            # Fall back to simulation report with exact network error details
            return OutboundCallResult(
                ok=False,
                status="CARRIER_DISPATCH_ERROR",
                to_phone=normalized_phone,
                property_code=prop_code,
                voice_preset=tts_res["voice_preset"],
                tts_engine=tts_res["tts_engine"],
                tts_backend=tts_res["tts_backend"],
                audio_url=audio_url,
                audio_id=audio_id,
                byte_length=tts_res["byte_length"],
                conversation_id=str(conv.id),
                carrier_status="error",
                carrier_call_sid=None,
                note=f"Lỗi kết nối Twilio carrier: {exc}. Câu thoại VieNeu-TTS đã được lưu sẵn sàng phát.",
                trace_id=trace_id,
            )

    # Simulation mode (When carrier credentials are not in .env)
    return OutboundCallResult(
        ok=True,
        status="SYNTHESIZED_SIMULATION",
        to_phone=normalized_phone,
        property_code=prop_code,
        voice_preset=tts_res["voice_preset"],
        tts_engine=tts_res["tts_engine"],
        tts_backend=tts_res["tts_backend"],
        audio_url=audio_url,
        audio_id=audio_id,
        byte_length=tts_res["byte_length"],
        conversation_id=str(conv.id),
        carrier_status="credentials_not_configured",
        carrier_call_sid=None,
        note=(
            "VieNeu-TTS v3 Turbo đã tổng hợp thành công câu thoại 48 kHz. "
            "Để rung chuông điện thoại thật qua mạng GSM viễn thông, bạn cần cấu hình TWILIO_ACCOUNT_SID, "
            "TWILIO_AUTH_TOKEN và TWILIO_FROM_PHONE trong file .env hoặc sử dụng giao diện /call trên trình duyệt."
        ),
        trace_id=trace_id,
    )
