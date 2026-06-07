# Excellent Voice Agent Plan

**Date:** 2026-06-07  
**Scope:** Turn the scheduling assistant into a production-quality, low-latency, observable, trustworthy voice agent.

## Goal

The app should feel like a competent human scheduling assistant: fast to answer, easy to interrupt, accurate about calendar state, honest about failures, and deterministic whenever it changes real-world state.

The model should handle conversation. The application should own business rules, booking state, timezone handling, idempotency, persistence, observability, and verification.

## Product Bar

- First audible response should normally arrive within 500-800 ms after user turn end, with no 10-15 second silent gap.
- Calendar writes must be verified before the assistant claims success.
- A repeated booking attempt in one session must not reuse stale tool state or silently no-op.
- Times are interpreted as local Europe/Berlin wall-clock time unless the user explicitly says otherwise.
- The assistant must recover cleanly from interruptions, reconnects, tool failures, unavailable calendar, and ambiguous user input.
- Users should never need to say "hello" multiple times to wake the agent.
- The app should produce enough trace data to explain every failed booking, delayed response, audio glitch, and interrupted turn.

## Architecture Principles

- **Conversation is probabilistic; booking is deterministic.** The LLM proposes intent and extracts facts, but a server-side state machine decides whether a booking can be created.
- **Calendar writes are two-phase.** Prepare and confirm first, write second, verify after write.
- **No unverified claims.** The assistant may say "I'm checking" or "that failed", but it may say "booked" only after a verified calendar event exists.
- **State lives outside the live model.** Confirmed facts, candidate slots, pending confirmations, tool results, and event IDs are persisted independently from transcripts.
- **Trace every boundary.** Browser, websocket, Pipecat pipeline, model events, tool calls, calendar API calls, and booking state transitions each emit structured events.
- **Optimize perceived latency and actual latency.** The app should start sessions proactively, stream continuously, avoid audio scheduling gaps, and use short acknowledgements only when they do not hide state-changing work.
- **Privacy by data minimization.** Availability tools expose free/busy ranges and generated slots, never calendar titles or participants.

## Workstreams

## 1. Observability and Session Traces

Status: started.

Implementation:

- Save per-session JSONL traces locally in `.traces/` for development.
- Save production traces to Firestore under `voice_sessions/{session_id}` or a trace subcollection.
- Redact invite tokens, phone numbers, and any user-provided contact data.
- Emit stable event names for:
  - websocket open/close/error
  - readiness result
  - mic start
  - first client audio
  - user turn start/end
  - assistant first audio
  - assistant audio chunk counts
  - playback queue depth
  - interruption requested
  - playback cleared
  - tool call start/end/error
  - calendar insert/get verification
  - booking state transition
  - session summary
- Add a debug endpoint that reads local traces in development only.

Acceptance:

- Given a failed booking, the trace shows the user request, extracted slot, confirmation state, calendar insert request, verification result, and spoken response category.
- Given a delayed first response, the trace shows whether delay came from browser mic setup, websocket setup, model session initialization, turn detection, tool call, or playback scheduling.
- Given distorted audio, the trace shows dropped frames, queue underruns, sample-rate assumptions, and playback chunk timing.

## 2. Deterministic Booking State Machine

Status: next.

Implementation:

- Add a server-side `BookingSession` state model with explicit states:
  - `idle`
  - `collecting_requirements`
  - `checking_availability`
  - `presenting_options`
  - `awaiting_slot_selection`
  - `awaiting_required_details`
  - `awaiting_explicit_confirmation`
  - `booking_in_progress`
  - `booked`
  - `failed_recoverable`
  - `cancelled`
- Add typed events:
  - `user_requested_booking`
  - `requirements_updated`
  - `availability_checked`
  - `slot_selected`
  - `details_completed`
  - `confirmation_received`
  - `confirmation_rejected`
  - `calendar_write_started`
  - `calendar_write_verified`
  - `calendar_write_failed`
  - `user_interrupted`
  - `session_reconnected`
- Store confirmed facts separately from candidate facts:
  - name
  - optional phone number
  - meeting type
  - date
  - start time
  - end time
  - timezone
  - selected slot ID
  - confirmation text
  - calendar event ID
- Represent available slots with opaque server-generated IDs. The model should ask the user to choose "the first option" or a spoken time, but the backend resolves that to a slot ID.
- Enforce idempotency with a booking operation ID. Repeated tool calls for the same confirmed slot should return the same verified event instead of creating duplicates or claiming a new booking.
- Reject state-changing tool calls unless the state machine is in `awaiting_explicit_confirmation`.
- Record every state transition in the trace log.

Acceptance:

- Consecutive bookings in one voice session work independently.
- If the model calls `book_meeting` twice for the same confirmation, only one calendar event exists.
- If calendar verification fails, the assistant says the booking did not complete.
- If the user changes the time after confirmation but before write, the pending confirmation is invalidated.
- If the session reconnects after slot selection, the app resumes from the selected slot and asks for confirmation instead of restarting.

## 3. Latency and Session Warmth

Implementation:

- Keep Cloud Run warm with `min_instances=1`.
- Start model/session setup as soon as the user opens a valid invite page, if cost and quota allow.
- Separate readiness checks from the first voice turn.
- Measure:
  - page load to websocket open
  - websocket open to model ready
  - first user audio to user turn end
  - user turn end to assistant first audio
  - tool latency
  - calendar API latency
- Avoid blocking first response on nonessential work.
- Use short initial acknowledgement only when the pipeline is truly ready to continue.

Acceptance:

- No normal path has a 15 second silent first response.
- p50 first assistant audio after user turn end is below 800 ms in the target region.
- p95 first assistant audio has a named bottleneck in traces if it exceeds 2 seconds.

## 4. Audio Quality and Turn Taking

Implementation:

- Keep browser capture using echo cancellation, noise suppression, and automatic gain where available.
- Use consistent sample-rate conversion and frame sizes.
- Detect and trace dropped input frames, queue underruns, and output scheduling drift.
- Clear playback immediately on interruption.
- Distinguish interruption from backchannel speech.
- Track what assistant audio was actually played before adding it to conversational context.

Acceptance:

- Barge-in clears audible assistant speech quickly.
- Backchannels like "okay" do not always cancel the assistant.
- No distorted voice from avoidable sample-rate mismatch or playback gaps.
- Audio trace data identifies whether artifacts are capture-side, transport-side, model-side, or playback-side.

## 5. Calendar Correctness

Implementation:

- Treat user-spoken times as local Europe/Berlin wall-clock time by default.
- Convert only at the calendar boundary.
- Return available slots to the model as local ISO strings and human-readable display strings.
- Verify every created event by reading it back from Google Calendar.
- Store verified calendar ID, event ID, start time, and end time in booking state.
- Use free/busy for availability and avoid exposing event metadata to the model.

Acceptance:

- "Book 16:00" creates a 16:00 Europe/Berlin event, not 17:00.
- A 13:30-14:30 appointment plus 15 minute buffer does not incorrectly block a valid 15:00 start.
- The assistant cannot book outside server-side business/private windows.

## 6. Evaluation and Regression Testing

Implementation:

- Add scripted voice-flow tests for:
  - first booking
  - consecutive booking
  - ambiguous time
  - timezone-sensitive time
  - calendar failure
  - interruption during assistant speech
  - reconnect after selected slot
  - declined optional phone number
- Add deterministic unit tests for state transitions.
- Add golden trace assertions for important flows.
- Add manual QA scripts that print latency summaries from traces.

Acceptance:

- Calendar logic and booking state machine tests run in CI.
- A failed voice QA run links to the exact trace session ID.
- Regressions in "booked but no event exists" are caught by tests.

## 7. Production Hardening

Implementation:

- Add Firestore TTL for traces and session state.
- Add trace sampling controls.
- Add quota and failure handling for Gemini and Google Calendar.
- Add structured error categories:
  - dependency unavailable
  - invalid invite
  - no available slot
  - confirmation missing
  - calendar conflict
  - calendar write failed
  - calendar verification failed
- Add admin-facing trace lookup by invite/session ID.
- Add privacy review for all persisted trace fields.

Acceptance:

- Production traces are useful without storing sensitive raw audio.
- Dependency outages produce clear user-facing behavior.
- The operator can diagnose a failed booking without reproducing it live.

## Recommended Sequence

1. Finish deterministic booking state machine.
2. Add state transition traces and tests.
3. Add consecutive-booking and idempotency tests.
4. Tighten first-response latency instrumentation.
5. Improve interruption and playback-cleardown behavior.
6. Add voice-flow QA scripts using recorded or synthetic conversations.
7. Move reconnectable session state and production traces to Firestore with TTL.

## Open Decisions

- Whether a single voice session should allow multiple booked appointments by design, or should end after one successful booking unless the user explicitly asks for another.
- Whether booking state should be stored in Firestore immediately or remain in memory until reconnect support is prioritized.
- Whether to adopt WebRTC earlier for stronger audio transport behavior.
- Whether to store trace summaries only, full JSONL event streams, or both in production.
