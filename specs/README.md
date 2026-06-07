# voice-assist — Specs Index

_Revised 2026-06-02 — Pipecat pipeline + optional phone collection_

A voice-based meeting scheduler. External visitors receive an invite link, open it in a browser, and use a voice conversation to book or reschedule a meeting with Christian. The agent respects availability rules (business vs. private time windows), never exposes calendar contents, and uses a Pipecat pipeline around Gemini Live so custom voice middleware can be inserted cleanly.

## Documents

| File | Description |
|---|---|
| [thesis.md](thesis.md) | Why this exists: replacing email back-and-forth scheduling with a single voice conversation |
| [vision.md](vision.md) | What it is, what it is not, two user types (visitor + Christian as admin), 7 measurable MVP success criteria |
| [product.md](product.md) | Core flows (book, reschedule, invite generation), tool definitions, availability rules, meeting type inference, feature list |
| [challenges.md](challenges.md) | 12 assumptions that could fail — privacy leakage, timezone bugs, wrong meeting matched, invite forwarding |
| [tech-stack.md](tech-stack.md) | All technology choices with rationale, Firestore addition, moss demoted to post-MVP |
| [ADR-001](ADR-001-voice-api-gemini-live.md) | Voice API: Gemini 2.0 Flash Live (multilingual, barge-in, function calling) |
| [ADR-002](ADR-002-deployment-gcp-cloud-run.md) | Deployment: Cloud Run (min_instances=1) + Terraform + GitHub Actions + WIF |
| [ADR-003](ADR-003-auth-google-oauth.md) | Auth: two-track — visitors use invite links; Google OAuth for calendar server-to-server only |
| [ADR-004](ADR-004-text-llm-openrouter-nemotron.md) | Text LLM: Nemotron 3 Nano via OpenRouter (post-MVP, briefings only) |
| [ADR-005](ADR-005-client-pwa.md) | Client: PWA (push-to-talk, invite link extraction, error page for invalid links) |
| [ADR-006](ADR-006-search-moss.md) | Semantic search: moss (post-MVP — freebusy API makes it irrelevant for MVP) |
| [ADR-007](ADR-007-iac-terraform-github-actions.md) | IaC: Terraform + two GitHub Actions workflows (deploy + invite generation) |
| [ADR-008](ADR-008-invite-links-firestore.md) | Invite link storage: Firestore with TTL-based expiry |
| [ADR-009](ADR-009-pipecat-gemini-live-orchestration.md) | Voice orchestration: Pipecat + `GeminiLiveLLMService`, not raw Gemini sockets |
| [ADR-010](ADR-010-voice-agent-runtime-best-practices.md) | Runtime best practices: latency, interruption, state, prompting, audio quality |
| [ADR-011](ADR-011-optional-phone-number-collection.md) | Visitor contact collection: optional phone number instead of required email |
| [gemini-live-function-calling.md](gemini-live-function-calling.md) | Deep dive: protocol, tool definitions, blocking vs non-blocking, barge-in during tool calls, latency budget |
| [implementation-plan.md](implementation-plan.md) | Week-by-week 7-week Phase 0 + Phase 1/2 outlines |
| [interview-next-level-2026-06-02.md](interview-next-level-2026-06-02.md) | Interview notes and resulting next-level decisions |
| [next-level-implementation-plan.md](next-level-implementation-plan.md) | Migration plan for Pipecat, middleware, voice best practices, and phone-number flow |
| [excellent-voice-agent-plan.md](excellent-voice-agent-plan.md) | Execution plan for a production-quality voice scheduling agent: observability, deterministic booking, latency, audio, evaluation, and hardening |

## Key Decisions (one line each)

- **Visitor auth is an invite link, not a login.** UUID in URL, validated against Firestore, 10 days TTL.
- **Privacy by API design, not just system prompt.** The freebusy API returns only time ranges — no event titles ever reach the model.
- **Business vs. private time windows are server-side enforced.** The agent infers type from context; the backend enforces the window. Agent reasoning can't override the rules.
- **Meeting type inference defaults to business.** When ambiguous, conservative is better than wrong.
- **Invite generation via GitHub Actions `workflow_dispatch`.** No admin UI at MVP — Christian opens the Actions tab.
- **Firestore for invite storage.** GCP-native, free tier, TTL auto-expiry, atomic reads.
- **Cloud Run min_instances=1.** Visitors should never hit a cold start when opening a link they just received.
- **Pipecat is the orchestration boundary.** The backend builds a Pipecat pipeline around `GeminiLiveLLMService` so text/audio filters and custom middleware can be inserted cleanly.
- **Visitor phone number is optional.** The agent asks whether the visitor wants to provide one, captures it in chunks, and confirms it with grouped readback.
- **Readiness is visible before voice starts.** The UI must show whether the voice API is usable and whether Google Calendar is connected; missing dependencies produce an outage message.
- **Reschedule by time match, not title.** `find_meeting_at` identifies events by approximate datetime — never by title (which is private).
- **moss is post-MVP.** The freebusy API makes semantic calendar indexing irrelevant for the core use case.

## Critical Risk

**Calendar content leakage.** The freebusy API is the primary protection — it returns no event details. The system prompt is secondary. If a code path ever calls the Events API and passes event titles to the model, the privacy model is broken. Audit every tool implementation against this.
