# Next-Level Implementation Plan

**Date:** 2026-06-02  
**Scope:** Migrate voice orchestration to Pipecat + `GeminiLiveLLMService`, add middleware-ready pipeline structure, implement optional phone-number collection, and align runtime behavior with voice-agent best practices.

## Phase 1: Specification Alignment

- [x] Update product docs to replace required visitor email with optional phone number.
- [x] Update success criteria to remove dependency on automatic Google Calendar guest email.
- [x] Add ADR-009, ADR-010, and ADR-011 to the specs index.
- [x] Mark raw Gemini socket proxy language as superseded by the Pipecat pipeline architecture.

## Phase 2: Pipecat Pipeline Migration

- [x] Update dependencies for the current Pipecat Gemini Live integration.
- [x] Refactor `src/session.py` into a Pipecat pipeline builder.
- [x] Instantiate `GeminiLiveLLMService` with:
  - Gemini Live model setting.
  - Voice setting.
  - System instruction builder.
  - Scheduling tool schema.
  - Realtime-service-aware context aggregation.
- [x] Keep FastAPI as the backend HTTP/WebSocket host.
- [ ] Make the browser transport feed Pipecat frames rather than manually forwarding Gemini socket messages.
- [ ] Add lifecycle cleanup for disconnect, cancellation, timeout, and reconnect.

## Phase 3: Middleware Insertion Points

- [x] Add input audio processor interface:
  - frame counters
  - sample-rate validation
  - input-level metrics
  - future noise/gain filters
- [x] Add interruption processor:
  - classifies backchannel versus interruption
  - emits client cleardown signal
  - cancels active assistant output
  - marks transcript rewind boundary
- [x] Add text output processor:
  - strips Markdown
  - normalizes dates and numbers for speech
  - formats phone-number readback
  - blocks accidental calendar-detail leakage
- [x] Add state processor:
  - separates confirmed facts from raw transcript
  - records spoken assistant text only after playback progress
  - supports reconnect resume

## Phase 4: Optional Phone-Number Flow

- [x] Replace email prompt with optional phone-number prompt.
- [x] Add phone state fields:
  - `visitor_phone`
  - `visitor_phone_confirmed`
  - `phone_collection_declined`
- [ ] Implement chunked phone capture:
  - accept country codes
  - preserve leading zeros
  - allow pauses after three or four digits
  - re-ask only low-confidence chunks
- [x] Implement readback grouping:
  - three-four-three for ten-digit local numbers
  - country-code plus two-to-four digit chunks for international numbers
- [x] Require explicit confirmation before storing a phone number.
- [x] Allow booking to complete when phone number is declined.
- [x] Update calendar event creation so visitor email is not required.

## Phase 5: Readiness and Outage UX

- [x] Add backend readiness endpoint that reports:
  - voice API active/usable
  - calendar connected and freebusy query possible
  - no calendar event titles, descriptions, or participants in readiness responses
- [x] Check voice readiness by validating Gemini/Pipecat configuration and performing the lightest safe session or configuration check available.
- [x] Check calendar readiness by validating credentials and performing a minimal freebusy-safe connectivity check.
- [x] Update UI before voice start:
  - show "Voice ready" only when the voice API is usable
  - show "Calendar connected" only when Google Calendar is connected
  - disable Start if either dependency is unavailable
  - show an outage message instead of opening a broken voice session
- [ ] Add tests for voice-down, calendar-down, both-down, and all-ready states.

## Phase 6: Voice Best-Practice Runtime Work

- [ ] Add metrics:
  - first-audio latency
  - interruption-to-cleardown latency
  - tool-call latency
  - reconnect recovery time
  - dropped audio frames
- [x] Configure browser audio capture with echo cancellation, noise suppression, and auto gain where available.
- [ ] Add immediate playback-buffer cleardown on interruption.
- [ ] Add server cancellation for active model output and pending non-critical tool work.
- [ ] Add transcript rewind so interrupted assistant text is not treated as spoken context.
- [x] Update system prompt:
  - short spoken turns
  - contractions
  - no Markdown
  - TTS-friendly numbers and dates
  - backchanneling rules
  - phone-number procedure
- [ ] Distinguish read tools from write tools:
  - availability lookup can use filler speech
  - booking/rescheduling requires explicit confirmation

## Phase 7: Persistence and Reconnect

- [x] Define `SessionState` with confirmed facts:
  - invite id
  - language
  - visitor name
  - optional phone state
  - meeting type
  - topic
  - selected slot
  - confirmation status
- [x] Store short-term state in memory for local dev.
- [ ] Store reconnectable session state in Firestore or another server-side store before production.
- [ ] On reconnect, resume from confirmed facts rather than raw transcript.
- [ ] Expire abandoned session state after a short TTL.

## Phase 8: QA and Acceptance Tests

- [ ] Readiness test: UI displays voice readiness and calendar readiness separately, and shows outage when either dependency is unavailable.
- [ ] Latency test: p50 first audible response under 500 ms after user turn completion in local/regional test conditions.
- [ ] Barge-in test: user says "stop" during assistant speech; playback clears immediately and transcript excludes the unplayed tail.
- [ ] Backchannel test: user says "uh-huh" while assistant speaks; app does not incorrectly cancel unless intent is corrective.
- [ ] Phone test: collect a number with country code, leading zero, and pauses; verify grouped readback and optional skip.
- [ ] Prompt test: assistant never speaks Markdown or visual formatting.
- [ ] Privacy test: asking what is on Christian's calendar does not reveal titles, participants, or topics.
- [ ] Reconnect test: disconnect after slot selection; reconnect resumes with selected slot and phone state intact.

## Open Product Decisions

- Whether optional phone numbers should be stored only in a server-side booking record or also in Christian's calendar event description.
- Whether post-booking visitor confirmation should be added later through SMS, WhatsApp, or a confirmation page.
- Whether WebSocket transport is sufficient for MVP or whether WebRTC should be adopted earlier for Opus, AEC behavior, and network resilience.
