# ADR-009: Pipecat + GeminiLiveLLMService Orchestration

**Status:** Decided  
**Date:** 2026-06-02

## Context

The original voice architecture described a backend WebSocket proxy between the browser and Gemini Live. That is workable for a prototype, but it makes every advanced voice behavior a custom protocol problem: middleware insertion, interruption state, tool-call handling, audio filtering, transcript cleanup, and observability all have to be hand-built around raw socket messages.

The product direction is now a complex one-to-one voice app where custom middleware and text/audio filters must be easy to add. Pipecat is built for composable real-time voice pipelines and provides a Gemini Live integration through `GeminiLiveLLMService`.

Current Pipecat documentation confirms that `GeminiLiveLLMService` supports real-time Gemini conversations with audio, transcription, streaming audio responses, and tool usage. The same docs also note an important constraint: the service does not emit all local user start/stop speaking frames when relying only on Gemini server-side VAD, so processors that need those frames require local turn heuristics or realtime-service-aware context aggregation.

## Decision

Use **Pipecat as the backend voice orchestration layer** and use **`GeminiLiveLLMService`** as the Gemini Live service. Do not implement a raw Gemini Live socket proxy as the primary architecture.

The backend WebSocket remains the browser transport entrypoint, but its responsibility changes:

```text
Browser PWA
  -> backend transport
  -> Pipecat pipeline
  -> GeminiLiveLLMService
  -> Pipecat pipeline
  -> backend transport
  -> Browser PWA
```

## Rationale

- Pipecat gives the project a real pipeline boundary for custom processors.
- Audio and text filters can be inserted without rewriting Gemini socket code.
- Tool dispatch can be centralized as Pipecat function handlers.
- Pipeline metrics can measure first-audio latency, tool latency, interruption latency, and dropped frames.
- The architecture keeps Gemini Live's native audio-to-audio latency benefit while avoiding a bespoke protocol layer.

## Pipeline Requirements

The first production pipeline should include these logical stages:

1. Transport input from the PWA.
2. Input audio policy processor for sample-rate validation, frame counters, and future audio filters.
3. Interruption processor that turns valid user barge-in into client cleardown plus model cancellation.
4. `GeminiLiveLLMService` with scheduling tools.
5. Text output filter for no-Markdown, spoken-number normalization, and phone-number readback formatting.
6. Transcript/state processor that records only what was actually spoken.
7. Transport output to the PWA.

## Consequences

- `src/session.py` should become a Pipecat pipeline builder, not a raw Gemini socket loop.
- Dependencies should include the Pipecat Google/Gemini extras required by the current Pipecat release.
- The app must explicitly handle Pipecat/Gemini turn-frame limitations. If processors need `UserStartedSpeakingFrame` or `UserStoppedSpeakingFrame`, add local VAD/turn tracking or use Pipecat's realtime service mode where appropriate.
- Integration tests should exercise pipeline behavior, not just WebSocket byte forwarding.
- Debugging should use Pipecat pipeline logs and metrics before inspecting raw Gemini messages.

## Rejected Option

Continue with a hand-written WebSocket proxy to Gemini Live.

Rejected because it optimizes the prototype but makes the desired next-level app harder: every middleware hook, barge-in edge case, and state rewind would be custom infrastructure.

## Source Notes

- Pipecat describes itself as a composable real-time voice and multimodal AI framework with WebSocket/WebRTC transports and Gemini Multimodal Live support.
- Pipecat API docs describe `GeminiLiveLLMService` as the service for Google's Gemini Live API and document its turn-frame behavior.
