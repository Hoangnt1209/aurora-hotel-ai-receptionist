"""VieNeu-TTS v3 Turbo (48 kHz bilingual Vietnamese-English) audio synthesis provider."""

import asyncio
import io
import math
import struct
import threading
import wave
from collections import OrderedDict
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
import numpy as np

from aurora.config import get_settings

VIENEU_SAMPLE_RATE = 48_000
VIENEU_ENGINE_NAME = "vieneu-v3-turbo"

# Regional voice presets verified from vieneu/assets/voices_v3_turbo.json
PROPERTY_VOICE_PRESETS: dict[str, str] = {
    "SGN": "Thùy Dung",   # Southern Vietnamese female voice
    "DAD": "Ngọc Trân",   # Central Vietnamese female voice
    "PQC": "Mai Anh",     # Clear bilingual female voice
    "GROUP": "Mai Anh",
}

_MAX_CACHE_ITEMS = 128
_AUDIO_CACHE: OrderedDict[str, bytes] = OrderedDict()
_CACHE_LOCK = threading.Lock()
_SDK_INSTANCE: Any = None
_SDK_LOCK = threading.Lock()


def select_voice_for_property(
    property_code: str | None, override_voice: str | None = None
) -> str:
    """Resolve VieNeu-TTS v3 Turbo preset voice by property region."""
    if override_voice and override_voice.strip():
        return override_voice.strip()
    if property_code:
        code = property_code.strip().upper()
        if code in PROPERTY_VOICE_PRESETS:
            return PROPERTY_VOICE_PRESETS[code]
    return get_settings().vieneu_default_voice


def store_audio_wav(wav_bytes: bytes) -> str:
    """Store WAV audio in bounded memory cache and return its audio_id."""
    audio_id = uuid4().hex
    with _CACHE_LOCK:
        _AUDIO_CACHE[audio_id] = wav_bytes
        while len(_AUDIO_CACHE) > _MAX_CACHE_ITEMS:
            _AUDIO_CACHE.popitem(last=False)
    return audio_id


def get_audio_wav(audio_id: str) -> bytes | None:
    """Fetch cached WAV audio bytes by audio_id."""
    with _CACHE_LOCK:
        return _AUDIO_CACHE.get(audio_id)


def pcm_float32_to_wav_bytes(
    audio: np.ndarray, sample_rate: int = VIENEU_SAMPLE_RATE
) -> bytes:
    """Convert mono float32 waveform [-1, 1] into standard 16-bit PCM WAV bytes."""
    clipped = np.clip(audio, -1.0, 1.0)
    pcm16 = (clipped * 32767.0).astype(np.int16)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm16.tobytes())
    return buf.getvalue()


def _generate_fast_wav_placeholder(text: str) -> bytes:
    """Produce a valid 48 kHz mono PCM16 WAV chime proportional to text length."""
    duration_sec = min(max(len(text) * 0.015, 0.35), 1.8)
    total_samples = int(VIENEU_SAMPLE_RATE * duration_sec)
    t = np.arange(total_samples, dtype=np.float32) / float(VIENEU_SAMPLE_RATE)
    # Soft two-tone hospitality chime envelope
    envelope = np.minimum(t / 0.03, 1.0) * np.minimum((duration_sec - t) / 0.08, 1.0)
    envelope = np.clip(envelope, 0.0, 1.0)
    waveform = 0.12 * envelope * (
        0.6 * np.sin(2.0 * math.pi * 523.25 * t)
        + 0.4 * np.sin(2.0 * math.pi * 659.25 * t)
    )
    return pcm_float32_to_wav_bytes(waveform, sample_rate=VIENEU_SAMPLE_RATE)


def _is_v3turbo_cached_locally() -> bool:
    hf_cache = Path.home() / ".cache" / "huggingface" / "hub"
    return (hf_cache / "models--pnnbao-ump--VieNeu-TTS-v3-Turbo").exists()


def _get_local_vieneu_sdk() -> Any:
    global _SDK_INSTANCE
    with _SDK_LOCK:
        if _SDK_INSTANCE is None:
            from vieneu import Vieneu

            _SDK_INSTANCE = Vieneu(mode="v3turbo", precision="int8")
        return _SDK_INSTANCE


class VieNeuTTSProvider:
    """Synthesizes speech using VieNeu-TTS v3 Turbo (local SDK, remote API, or fast WAV fallback)."""

    def __init__(
        self,
        mode: str | None = None,
        api_url: str | None = None,
        http_transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        settings = get_settings()
        self.mode = (mode or settings.vieneu_mode or "auto").strip().lower()
        self.api_url = (api_url or settings.vieneu_api_url or "").strip().rstrip("/")
        self.http_transport = http_transport

    def _effective_mode(self) -> str:
        if self.mode == "fast_wav":
            return "fast_wav"
        if self.mode == "remote_api" and self.api_url:
            return "remote_api"
        if self.mode == "local_sdk":
            return "local_sdk"
        # auto mode
        if self.api_url:
            return "remote_api"
        if _is_v3turbo_cached_locally():
            return "local_sdk"
        return "fast_wav"

    async def synthesize_wav(
        self,
        text: str,
        *,
        property_code: str | None = None,
        voice_preset: str | None = None,
    ) -> dict[str, Any]:
        """Synthesize text into 48 kHz WAV bytes, cache it, and return metadata + audio_id."""
        preset = select_voice_for_property(property_code, voice_preset)
        effective = self._effective_mode()
        backend_used = effective

        wav_bytes: bytes | None = None

        if effective == "remote_api" and self.api_url:
            try:
                client_kwargs: dict[str, Any] = {"timeout": 20.0}
                if self.http_transport is not None:
                    client_kwargs["transport"] = self.http_transport
                async with httpx.AsyncClient(**client_kwargs) as client:
                    resp = await client.post(
                        f"{self.api_url}/v1/audio/speech",
                        json={
                            "model": "vieneu-v3-turbo",
                            "input": text,
                            "voice": preset,
                            "response_format": "wav",
                        },
                    )
                    resp.raise_for_status()
                    if resp.content[:4] == b"RIFF":
                        wav_bytes = resp.content
            except Exception:
                wav_bytes = None

        if wav_bytes is None and effective == "local_sdk":
            try:
                def _run_sdk() -> bytes:
                    sdk = _get_local_vieneu_sdk()
                    audio_arr = sdk.infer(text=text, voice=preset)
                    return pcm_float32_to_wav_bytes(
                        np.asarray(audio_arr, dtype=np.float32),
                        sample_rate=VIENEU_SAMPLE_RATE,
                    )

                wav_bytes = await asyncio.to_thread(_run_sdk)
            except Exception:
                wav_bytes = None

        if wav_bytes is None:
            wav_bytes = _generate_fast_wav_placeholder(text)
            backend_used = "fast_wav_fallback"

        audio_id = store_audio_wav(wav_bytes)
        return {
            "audio_id": audio_id,
            "audio_url": f"/api/v1/telephony/tts/{audio_id}.wav",
            "tts_engine": VIENEU_ENGINE_NAME,
            "tts_backend": backend_used,
            "voice_preset": preset,
            "sample_rate_hz": VIENEU_SAMPLE_RATE,
            "byte_length": len(wav_bytes),
        }
