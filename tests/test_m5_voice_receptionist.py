"""Milestone 5 verification tests: voice read-back protocol, phonetic codes, and barge-in."""

from datetime import date, timedelta
from uuid import uuid4

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aurora.db.models import ConversationMessage, Escalation, Reservation
from aurora.db.seed import seed_database
from aurora.voice.speech_formatter import format_for_speech, spell_code_for_speech
from aurora.voice.vieneu_tts import VieNeuTTSProvider, get_audio_wav
from aurora.voice.worker import VoiceSessionController, VoiceState


async def test_voice_readback_confirmation_and_phonetic_spelling(
    db_session: AsyncSession,
) -> None:
    """Voice controller must read back exact weekday/dates/price and spell confirmation code."""
    await seed_database(db_session)
    controller = VoiceSessionController()

    ci = date.today() + timedelta(days=22)
    co = ci + timedelta(days=2)
    email = f"voice-{uuid4().hex[:6]}@example.com"

    turn1 = await controller.process_voice_turn(
        db_session,
        transcript=(
            f"Book Deluxe Ocean room in Da Nang from {ci.isoformat()} to {co.isoformat()} "
            f"for 2 adults, name Vo Hoang Nam, phone 0907778888, email {email}"
        ),
        stt_ms=145.0,
    )
    assert turn1.state == VoiceState.SPEAKING
    assert turn1.requires_confirmation is True
    # Spoken read-back must include weekday name and spoken VND amount without markdown
    assert ci.strftime("%A") in turn1.spoken_text
    assert "*" not in turn1.spoken_text
    assert len(turn1.tts_sentences) >= 1
    assert turn1.telemetry.stt_ms == 145.0
    assert turn1.telemetry.agent_ms > 0
    assert turn1.tts_engine == "vieneu-v3-turbo"
    assert turn1.sample_rate_hz == 48000
    assert turn1.voice_preset == "Ngọc Trân"
    assert turn1.audio_url is not None and turn1.audio_url.endswith(".wav")

    audio_id = turn1.audio_url.rsplit("/", 1)[-1].replace(".wav", "")
    wav_bytes = get_audio_wav(audio_id)
    assert wav_bytes is not None and wav_bytes[:4] == b"RIFF" and b"WAVE" in wav_bytes[:16]

    # Verify remote VieNeu-TTS v3 Turbo OpenAI-compatible HTTP endpoint integration
    mock_wav = b"RIFF\x24\x00\x00\x00WAVEfmt " + b"\x00" * 28
    provider = VieNeuTTSProvider(
        mode="remote_api",
        api_url="http://vieneu.local",
        http_transport=httpx.MockTransport(
            lambda req: httpx.Response(200, content=mock_wav)
        ),
    )
    remote_meta = await provider.synthesize_wav("Chào mừng đến với Aurora", property_code="SGN")
    assert remote_meta["tts_backend"] == "remote_api"
    assert remote_meta["voice_preset"] == "Thùy Dung"
    assert get_audio_wav(remote_meta["audio_id"]) == mock_wav

    # Confirm verbally -> real reservation created & code spelled for TTS
    turn2 = await controller.process_voice_turn(
        db_session,
        transcript="Yes, confirm booking",
        conversation_id=turn1.conversation_id,
    )
    assert turn2.requires_confirmation is False
    assert "dash" in turn2.spoken_text

    res = await db_session.scalar(
        select(Reservation).order_by(Reservation.created_at.desc()).limit(1)
    )
    assert res is not None
    spelled = spell_code_for_speech(res.confirmation_code, "en")
    assert spelled in turn2.spoken_text


async def test_voice_barge_in_flushes_tts_and_corrects_dates(
    db_session: AsyncSession,
) -> None:
    """Mid-utterance barge-in must flush queued TTS chunks, annotate DB, and switch to LISTENING."""
    await seed_database(db_session)
    controller = VoiceSessionController()

    ci1 = date.today() + timedelta(days=14)
    co1 = ci1 + timedelta(days=2)

    turn1 = await controller.process_voice_turn(
        db_session,
        transcript=f"Kiểm tra phòng trống ở Sài Gòn từ {ci1.isoformat()} đến {co1.isoformat()} cho 2 người",
    )
    assert turn1.state == VoiceState.SPEAKING
    assert len(controller.pending_tts_chunks) >= 2

    # Emit only the first sentence before caller interrupts
    first_chunk = controller.emit_next_tts_chunk()
    assert first_chunk is not None
    remaining_before_interrupt = len(controller.pending_tts_chunks)
    assert remaining_before_interrupt >= 1

    # Caller speaks mid-reply -> VAD triggers barge-in
    barge = await controller.handle_barge_in(
        db_session, conversation_id=turn1.conversation_id
    )
    assert barge.state == VoiceState.LISTENING
    assert barge.flushed_chunks_count == remaining_before_interrupt
    assert barge.spoken_chunks_count == 1
    assert len(controller.pending_tts_chunks) == 0

    # Verify DB message recorded barge-in interruption metadata
    msg = await db_session.scalar(
        select(ConversationMessage).where(
            ConversationMessage.id == barge.annotated_message_id
        )
    )
    assert msg is not None
    assert msg.tool_calls["barge_in"]["interrupted"] is True

    # Caller immediately corrects dates and requests human manager escalation
    esc_turn = await controller.process_voice_turn(
        db_session,
        transcript="Khoan đã, chuyển cho quản lý giúp tôi!",
        conversation_id=turn1.conversation_id,
    )
    assert esc_turn.escalation_code is not None
    esc = await db_session.scalar(
        select(Escalation).where(Escalation.escalation_code == esc_turn.escalation_code)
    )
    assert esc is not None
    assert format_for_speech("6,900,000 VND", "vi") == "6 triệu 900 nghìn đồng"
