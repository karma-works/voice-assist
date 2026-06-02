# ADR-010: Voice-Agent Runtime Best Practices

**Status:** Decided  
**Date:** 2026-06-02

## Context

The project goal is no longer only "book a meeting by voice." The app should meet modern voice-agent expectations: low latency, natural barge-in, colloquial speech, resilient state, and high audio quality. These cannot be left as vague polish tasks because they affect architecture and test strategy.

## Decision

Adopt the following voice-agent best practices as product and engineering requirements for the runtime.

## Requirements

### Latency: Golden 500 ms

- Target first audible assistant audio within 500 ms after the user's turn is complete.
- Track p50, p90, and p95 first-audio latency per session.
- Stream audio end to end; do not buffer whole utterances before forwarding.
- Use Gemini Live native audio-to-audio for the primary path instead of a cascaded STT -> LLM -> TTS pipeline.
- Keep Cloud Run `min_instances=1` for visitor traffic.
- Prefer regional placement close to the primary users and the Gemini endpoint.

### Interruption Handling

- Treat user interruption as a state transition with three actions:
  1. Clear the client playback buffer immediately.
  2. Cancel or interrupt backend generation/tool continuation.
  3. Truncate assistant transcript history to what was actually played.
- Calibrate VAD for two classes of speech:
  - Backchannel: "uh-huh", "right", "okay" while the agent can continue.
  - Interruption: "no", "stop", "wait", correction, or a new request.
- Measure interruption-to-cleardown latency separately from normal response latency.

### Conversational Realism

- System prompt must require short spoken turns, contractions, and conversational phrasing.
- Model output must not contain Markdown, bullets, code formatting, emoji, or visual punctuation intended for reading.
- Numbers, dates, temperatures, symbols, and phone numbers must be spoken in TTS-friendly text.
- Backchanneling is allowed only when it does not commit to an action or interrupt the user's thought.

### State and Tool Calls

- Persist short-term session state outside the live model connection so reconnects can resume without asking the visitor to repeat everything.
- Tool calls that read availability may run asynchronously with short filler speech.
- Tool calls that write calendar state require explicit confirmation and must not be hidden behind filler.
- Conversation history must record confirmed facts separately from raw transcript text.

### Readiness and Outage Behavior

- The UI must show whether the voice API is active/usable before the visitor starts.
- The UI must show whether Google Calendar is connected before the visitor starts.
- If either dependency is unavailable, the Start control must be disabled and the visitor must see an outage message.
- Readiness checks must not reveal calendar contents.

### Audio Quality

- Browser capture must use echo cancellation, noise suppression, and auto gain where supported.
- Transport should prefer low-latency audio framing. If WebRTC is adopted, prefer Opus at 24 kHz or 48 kHz.
- The app must expose diagnostics for microphone permission, input level, output playback state, and dropped audio frames.

## Acceptance Criteria

- A local QA script can report first-audio latency, interruption-cleardown latency, and tool-call latency.
- UI displays voice and calendar readiness separately; voice-down or calendar-down states produce outage messages.
- Manual barge-in test: user says "stop" while the agent is speaking; audio stops immediately and the next assistant answer does not include the interrupted tail as if it had been spoken.
- Prompt test: assistant does not speak Markdown phrases such as "bullet point" or read punctuation literally.
- Reconnect test: a visitor can reconnect during a booking flow and the app remembers selected slot, name, optional phone-number state, and confirmation status.

## Consequences

- The implementation plan must include metrics before polish.
- The PWA needs explicit playback-buffer control, not only passive audio playback.
- Session state needs a structured model, not only a Gemini conversation transcript.
- Tool handlers must distinguish read tools from write tools.
