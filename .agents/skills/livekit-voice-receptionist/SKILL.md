---
name: livekit-voice-receptionist
description: >-
  Guides implementation and testing of Aurora's real-time LiveKit voice receptionist,
  including streaming STT/TTS, VAD turn-taking, barge-in interruption handling,
  and spoken read-back confirmation protocols. Use when working on Milestone 5 or Milestone 6 voice integration.
---

# LiveKit Real-Time Voice Receptionist Runbook

Apply this skill when integrating the LiveKit voice worker with the LangGraph receptionist core.

## Voice Pipeline Architecture

1. **Shared Brain, Voice-Adapted Output:**
   - Route voice transcripts directly into the exact same LangGraph agent and deterministic M2/M3 backend tools used by web chat (`channel="voice"`).
   - Keep spoken responses concise (1–3 short natural sentences), avoid markdown tables/URLs/bullet lists, and format VND amounts clearly for speech.
2. **Streaming & Barge-In Handling:**
   - Configure Voice Activity Detection (`VAD`) and turn detection to minimize dead air while preventing premature cut-offs on natural Vietnamese/English pauses.
   - On barge-in (guest speaks while TTS is playing): immediately stop/flush TTS audio queue, record only the portion actually spoken in `conversation_messages` (or mark truncated), and process the guest's interruption cleanly.
3. **Voice-Specific Reliability Rules:**
   - **Read-Back Protocol:** Before mutating any reservation over voice, explicitly read back property name, check-in/check-out dates with weekday, room type, rate plan, and total price, and wait for clear verbal confirmation.
   - **Confirmation Code Spelling:** Read alphanumeric codes (`AUR-DAD-8F29K1`) slowly or phonetically when speaking and confirm back what STT heard during lookup.
   - **Latency Logging:** Log timestamps per turn (`vad_eou_at`, `stt_final_at`, `llm_first_token_at`, `tts_first_audio_at`) with `trace_id` to measure real observed latency—never estimate or invent timings.

## Validation Steps

1. Run automated voice session tests (simulated STT turns + barge-in interrupt signal) verifying TTS cancellation, truncated state consistency, and read-back gate enforcement before booking.
2. Test full escalation handoff from a voice room ensuring `escalations.context_snapshot` captures the voice transcript cleanly for the Staff Dashboard.
