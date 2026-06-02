# Interview Notes: Next-Level Voice App

**Date:** 2026-06-02  
**Participants:** Product owner and engineering assistant  
**Outcome:** Move from a thin Gemini Live socket proxy to a Pipecat-based voice pipeline using `GeminiLiveLLMService`, with explicit middleware points for custom text/audio filters and a phone-number-first booking flow.

## Interview Summary

### What are we really building?

A complex one-to-one voice scheduling app, not a simple calendar chatbot. The app should feel like a fast human conversation, tolerate interruptions, and allow future insertion of custom logic such as audio filters, text normalizers, transcript guards, compliance filters, analytics, custom VAD policy, and phone-number validation.

### What is the main architectural upgrade?

Use Pipecat as the orchestration layer and `GeminiLiveLLMService` as the Gemini Live integration. The backend should no longer treat Gemini Live as a raw socket to proxy. The backend should own a Pipecat pipeline with explicit processors around the model service.

Target shape:

```text
Browser PWA
  -> Pipecat transport
  -> input audio processors
  -> interruption and turn policy
  -> GeminiLiveLLMService
  -> output text/audio processors
  -> Pipecat transport
  -> Browser playback
```

### What should be easy to extend?

- Input audio filters: gain normalization, noise suppression hooks, VAD diagnostics, echo-risk detection.
- Text filters: no Markdown, spoken-number normalization, phone-number extraction and verification, privacy redaction.
- Conversation middleware: state persistence, interruption rewind, function-call filler policy, backchanneling rules.
- Observability processors: first-audio latency, interruption latency, tool-call latency, dropped-frame counters.

### What does best practice mean for this project?

- Optimize for first audible response under 500 ms after the user's turn is complete.
- Keep audio streaming end to end.
- Prefer Gemini Live native audio-to-audio over a cascaded STT -> LLM -> TTS chain.
- Treat interruption as a first-class state transition, not a side effect.
- Keep spoken output short, colloquial, and free of formatting artifacts.
- Make tool calls asynchronous where possible and keep the voice track alive with short filler.
- Persist session state enough to survive reconnects.
- Use browser and/or transport-level AEC, low-latency codecs, and measured VAD thresholds.

### What functional change is required?

Stop collecting email addresses in the booking flow. Ask whether the visitor wants to provide a phone number. The phone number is optional. If they provide one, collect it in chunks, handle country codes and leading zeros, and verify using local pacing such as three-four-three grouping for ten-digit numbers.

### What product tradeoff follows from removing email?

The MVP can no longer rely on Google Calendar's guest email notification as the visitor confirmation channel. The calendar event should still be created for Christian, but the visitor confirmation must be spoken in-session. If visitor-side confirmation outside the session is required later, add SMS or a post-booking confirmation page as a separate decision.

## Decisions Generated

- [ADR-009](ADR-009-pipecat-gemini-live-orchestration.md): Use Pipecat with `GeminiLiveLLMService`, not raw Gemini Live sockets.
- [ADR-010](ADR-010-voice-agent-runtime-best-practices.md): Adopt voice-agent runtime best practices as measurable engineering requirements.
- [ADR-011](ADR-011-optional-phone-number-collection.md): Replace required email collection with optional phone-number collection.
- [next-level-implementation-plan.md](next-level-implementation-plan.md): Implementation plan for the migration and product flow update.
