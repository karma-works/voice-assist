# ADR-001: Voice API — Gemini 2.0 Flash Live

**Status:** Decided  
**Date:** 2026-05-30

## Context

voice-assist needs a voice-to-voice model that:
- Handles German STT + reasoning + TTS in a single API (end-to-end, not a 3-piece pipeline)
- Supports barge-in (user interrupts mid-response)
- Streams audio via WebSocket (not request/response REST)
- Supports function calling (tool use for Google Calendar operations)
- Is cost-effective for a personal project (~10–30 min/day usage)

Alternatives evaluated:
- **GPT-4o Realtime (OpenAI):** ~$0.10/min, requires separate OpenAI account, not on OpenRouter, same WebSocket protocol complexity.
- **Ultravox v0.5 (self-hosted):** Open source, free per-call, but requires a persistent GPU VM (~$0.50–1.50/hr on GCP), high operational burden.
- **Separate STT + LLM + TTS pipeline:** Whisper + Nemotron + Kokoro. Full control, zero cost if self-hosted, but requires building and tuning the full audio pipeline, VAD, turn detection, and barge-in from scratch.
- **Gemini 2.0 Flash Live:** ~$0.01/min, native German, native barge-in, function calling, WebSocket protocol, GCP-native.

Original requirement included "NVIDIA Nemotron Audio via OpenRouter." Investigation revealed: (1) Nemotron 3 Nano on OpenRouter is text-only, not audio; (2) OpenRouter has no audio output / voice-to-voice models; (3) OpenRouter does not support WebSocket-based real-time streaming.

## Decision

Use **Gemini 2.0 Flash Live** as the voice-to-voice model, accessed directly via Google's API (WebSocket), not through OpenRouter.

## Rationale

- 10x cheaper than GPT-4o Realtime for equivalent functionality.
- Native VAD + barge-in requires zero custom audio pipeline logic.
- Function calling mid-stream allows inline calendar tool use without interrupting the audio session.
- GCP-native: same billing account, same network region as Cloud Run deployment, minimizes latency.
- German language performance is production-validated.
- Pipecat has a first-class Gemini Live integration, reducing boilerplate.

## What This Option Does NOT Do Well

- Vendor lock-in: switching to another voice API requires a new client implementation.
- Per-minute billing accrues even during silent pauses if the WebSocket session is open.
- Not on OpenRouter — original goal of consolidating all AI billing to OpenRouter is not achievable for the voice layer.
- Gemini Live's function calling tool use during streaming has less community battle-testing than OpenAI Realtime.

## Consequences

- Backend must implement a WebSocket proxy (PWA → backend → Gemini Live).
- Google Cloud billing must be enabled for the `generativelanguage.googleapis.com` API.
- A `GEMINI_API_KEY` secret must be provisioned and stored in GCP Secret Manager.
- Session timeout must be managed: Gemini Live sessions are not indefinitely persistent.
- Pipecat is the recommended framework to manage this integration.
