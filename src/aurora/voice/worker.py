"""LiveKit-compatible real-time voice receptionist controller with barge-in & latency telemetry."""

import time
from collections import deque
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any
from uuid import UUID

import jwt
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aurora.agent.graph import ReceptionistAgentService
from aurora.config import get_settings
from aurora.db.models import ConversationMessage
from aurora.voice.speech_formatter import (
    build_spoken_readback,
    format_for_speech,
    split_into_tts_sentences,
)
from aurora.voice.vieneu_tts import (
    VIENEU_ENGINE_NAME,
    VIENEU_SAMPLE_RATE,
    VieNeuTTSProvider,
)


class VoiceState(StrEnum):
    IDLE = "IDLE"
    LISTENING = "LISTENING"
    THINKING = "THINKING"
    SPEAKING = "SPEAKING"
    INTERRUPTED = "INTERRUPTED"


class VoiceTurnTelemetry(BaseModel):
    trace_id: str
    stt_ms: float
    agent_ms: float
    tts_prep_ms: float
    total_ms: float


class VoiceTurnResult(BaseModel):
    conversation_id: UUID
    spoken_text: str
    tts_sentences: list[str]
    tts_engine: str = VIENEU_ENGINE_NAME
    tts_backend: str = "fast_wav_fallback"
    voice_preset: str = "Mai Anh"
    sample_rate_hz: int = VIENEU_SAMPLE_RATE
    audio_url: str | None = None
    state: VoiceState
    language: str
    intent: str
    requires_confirmation: bool
    pending_action: dict[str, Any] | None = None
    escalation_code: str | None = None
    telemetry: VoiceTurnTelemetry


class BargeInResult(BaseModel):
    conversation_id: UUID
    state: VoiceState
    flushed_chunks_count: int
    spoken_chunks_count: int
    annotated_message_id: UUID | None = None


class VoiceSessionController:
    """Manages streaming TTS chunks, VAD turn-taking, and barge-in cancellation."""

    def __init__(self) -> None:
        self.state: VoiceState = VoiceState.IDLE
        self.pending_tts_chunks: deque[str] = deque()
        self.spoken_chunks: list[str] = []
        self.interruption_count: int = 0
        self.last_trace_id: str | None = None

    async def process_voice_turn(
        self,
        session: AsyncSession,
        transcript: str,
        conversation_id: UUID | None = None,
        property_code: str | None = None,
        stt_ms: float = 0.0,
        trace_id: str | None = None,
        ai_provider: str | None = None,
        ai_api_key: str | None = None,
    ) -> VoiceTurnResult:
        """Route finalized STT transcript through LangGraph and queue streaming TTS chunks."""
        t_start = time.perf_counter()
        self.state = VoiceState.THINKING
        self.pending_tts_chunks.clear()
        self.spoken_chunks.clear()

        t_agent_start = time.perf_counter()
        agent = ReceptionistAgentService(
            session,
            ai_provider=ai_provider,
            ai_api_key=ai_api_key,
        )
        turn = await agent.handle_turn(
            user_message=transcript,
            conversation_id=conversation_id,
            channel="voice",
            property_code=property_code,
            trace_id=trace_id,
        )
        agent_ms = (time.perf_counter() - t_agent_start) * 1000.0
        self.last_trace_id = turn.trace_id

        t_tts_start = time.perf_counter()
        raw_reply = turn.reply
        if turn.requires_confirmation and turn.pending_action:
            readback = build_spoken_readback(turn.pending_action, turn.language)
            if readback:
                raw_reply = readback

        spoken_text = format_for_speech(raw_reply, turn.language)
        sentences = split_into_tts_sentences(spoken_text)
        self.pending_tts_chunks.extend(sentences)
        self.state = VoiceState.SPEAKING if sentences else VoiceState.IDLE

        tts_meta = await VieNeuTTSProvider().synthesize_wav(
            spoken_text or raw_reply,
            property_code=property_code or turn.property_code,
        )
        tts_prep_ms = (time.perf_counter() - t_tts_start) * 1000.0
        total_ms = stt_ms + (time.perf_counter() - t_start) * 1000.0

        return VoiceTurnResult(
            conversation_id=turn.conversation_id,
            spoken_text=spoken_text,
            tts_sentences=sentences,
            tts_engine=tts_meta["tts_engine"],
            tts_backend=tts_meta["tts_backend"],
            voice_preset=tts_meta["voice_preset"],
            sample_rate_hz=tts_meta["sample_rate_hz"],
            audio_url=tts_meta["audio_url"],
            state=self.state,
            language=turn.language,
            intent=turn.intent,
            requires_confirmation=turn.requires_confirmation,
            pending_action=turn.pending_action,
            escalation_code=turn.escalation_code,
            telemetry=VoiceTurnTelemetry(
                trace_id=turn.trace_id,
                stt_ms=round(stt_ms, 2),
                agent_ms=round(agent_ms, 2),
                tts_prep_ms=round(tts_prep_ms, 2),
                total_ms=round(total_ms, 2),
            ),
        )

    def emit_next_tts_chunk(self) -> str | None:
        """Consume and emit the next streaming TTS sentence to LiveKit audio output."""
        if not self.pending_tts_chunks:
            self.state = VoiceState.IDLE
            return None
        chunk = self.pending_tts_chunks.popleft()
        self.spoken_chunks.append(chunk)
        if not self.pending_tts_chunks:
            self.state = VoiceState.IDLE
        return chunk

    async def handle_barge_in(
        self,
        session: AsyncSession,
        conversation_id: UUID,
    ) -> BargeInResult:
        """Immediately cancel TTS queue on VAD barge-in and mark truncated output in DB."""
        flushed = len(self.pending_tts_chunks)
        spoken_cnt = len(self.spoken_chunks)
        spoken_prefix = " ".join(self.spoken_chunks).strip()

        self.pending_tts_chunks.clear()
        self.state = VoiceState.INTERRUPTED
        self.interruption_count += 1

        last_assistant = await session.scalar(
            select(ConversationMessage)
            .where(
                ConversationMessage.conversation_id == conversation_id,
                ConversationMessage.role == "assistant",
            )
            .order_by(ConversationMessage.created_at.desc())
            .limit(1)
        )
        annotated_id: UUID | None = None
        if last_assistant is not None:
            meta = dict(last_assistant.tool_calls or {})
            meta["barge_in"] = {
                "interrupted": True,
                "spoken_prefix": spoken_prefix,
                "flushed_chunks": flushed,
            }
            last_assistant.tool_calls = meta
            annotated_id = last_assistant.id
            await session.commit()

        self.state = VoiceState.LISTENING
        return BargeInResult(
            conversation_id=conversation_id,
            state=self.state,
            flushed_chunks_count=flushed,
            spoken_chunks_count=spoken_cnt,
            annotated_message_id=annotated_id,
        )


def create_livekit_room_token(
    room_name: str,
    participant_identity: str,
    participant_name: str = "Guest",
    ttl_seconds: int = 3600,
) -> dict[str, Any]:
    """Generate a standard LiveKit JWT room grant when LIVEKIT_* keys are configured."""
    settings = get_settings()
    if not (settings.livekit_url and settings.livekit_api_key and settings.livekit_api_secret):
        return {
            "configured": False,
            "livekit_url": None,
            "room_name": room_name,
            "token": None,
            "message": "LIVEKIT_URL / LIVEKIT_API_KEY / LIVEKIT_API_SECRET are not configured; using HTTP/Web Speech voice pipeline.",
        }

    now = datetime.now(UTC)
    payload = {
        "iss": settings.livekit_api_key,
        "sub": participant_identity,
        "name": participant_name,
        "nbf": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=ttl_seconds)).timestamp()),
        "video": {
            "roomJoin": True,
            "room": room_name,
            "canPublish": True,
            "canSubscribe": True,
        },
    }
    token = jwt.encode(payload, settings.livekit_api_secret, algorithm="HS256")
    return {
        "configured": True,
        "livekit_url": settings.livekit_url,
        "room_name": room_name,
        "token": token,
        "message": "LiveKit room token issued.",
    }
