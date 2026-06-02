# Tech Stack: voice-assist

_Revised 2026-06-02 — Pipecat orchestration boundary_

## Summary Table

| Layer | Technology | Status | ADR |
|---|---|---|---|
| Voice model (STT + LLM + TTS) | Gemini 2.0 Flash Live | Decided | ADR-001 |
| Text LLM (insights/summaries) | Nemotron 3 Nano (OpenRouter) | Post-MVP | ADR-004 |
| Invite link storage | Firestore | Decided | ADR-008 |
| Calendar semantic search | moss | Post-MVP | ADR-006 |
| Client | PWA (HTML/JS, Web Audio API) | Decided | ADR-005 |
| Backend | Python 3.12 + FastAPI | Decided | — |
| Calendar API | Google Calendar API v3 (freebusy) | Decided | — |
| Auth (visitors) | UUID invite link (Firestore) | Decided | ADR-008 |
| Auth (admin/Christian) | Google OAuth 2.0 | Decided | ADR-003 |
| Deployment | GCP Cloud Run | Decided | ADR-002 |
| IaC | Terraform | Decided | ADR-007 |
| CI/CD + invite generation | GitHub Actions | Decided | ADR-007 |
| Secrets | GitHub Secrets + GCP Secret Manager | Decided | ADR-002 |
| Voice pipeline framework | Pipecat + `GeminiLiveLLMService` | Decided | ADR-009 |

---

## Decided Choices

### Gemini 2.0 Flash Live

**Rationale:** Native German support, barge-in via server-side VAD, WebSocket protocol, function calling mid-stream, ~$0.01/min pricing, and natural GCP integration (same billing account, same network region reduces latency). As of 2025, the only production-ready end-to-end voice-to-voice model at this price point.

**Trade-off accepted:** Vendor lock-in to Google. If Google degrades the model, changes pricing, or the API breaks, there's no drop-in replacement — switching to OpenAI Realtime would require a new WebSocket protocol implementation. Accepted because the cost and quality delta justify it for a personal project.

**What this does NOT do well:** Not on OpenRouter (original requirement dropped). Per-minute billing means cost is proportional to talk time, not computation — 30 min/day = ~$270/year. Long pauses with an open session still accrue cost.

---

### Python 3.12 + FastAPI

**Rationale:** Google's official Python client libraries for both Gemini Live (via `google-genai`) and Google Calendar API are mature and well-documented. FastAPI provides async WebSocket support out of the box and generates OpenAPI docs. Python is Christian's likely development language given the context.

**Trade-off accepted:** Python has higher memory usage than Go or Node for long-running WebSocket connections. On Cloud Run with auto-scaling to zero, this matters less.

---

### Pipecat (voice pipeline framework)

**Rationale:** Pipecat (by Daily.co) is the leading open-source framework for building real-time voice AI pipelines. It has first-class Gemini Live support through `GeminiLiveLLMService`, supports composable processors, and gives the app explicit insertion points for middleware, audio filters, text filters, metrics, and state handling.

**Trade-off accepted:** Another dependency. Pipecat is relatively new (v0.x in 2024, v1.x in 2025). If it breaks or the project stalls, the wrapping abstractions become a liability.

**Alternative considered:** Build raw WebSocket proxy with `websockets` library. Rejected: too much low-level audio handling that's already solved in Pipecat, and too little structure for the custom middleware goal.

---

### Firestore

**Rationale:** Stores invite link documents (`{uuid, created_at, label, status}`). GCP-native, serverless, scales to zero, free tier covers personal scale indefinitely. TTL-based document expiry means expired links can be auto-deleted without a cleanup job. No schema migrations.

**Trade-off accepted:** Firestore is a NoSQL document store — over-engineered for a simple key-value use case. But it's the right GCP-native answer. SQLite on Cloud Run would reset on redeploy. GCS JSON is not atomic. Firestore's free tier (1GB storage, 50K reads/day) will never be exceeded here.

---

### GCP Cloud Run

**Rationale:** Scales to zero (important for personal project with sporadic usage), HTTPS out of the box, WebSocket support with configurable timeout up to 3600s, GitHub Actions has a first-class `deploy-to-cloud-run` action, and co-location with Gemini Live reduces latency.

**Trade-off accepted:** WebSocket sessions can't survive Cloud Run instance restarts (scale-down). Client reconnect logic is required. Not suitable if the project later needs persistent background processes (e.g., always-on wake word detection).

---

### Terraform

**Rationale:** Standard IaC tool, GCP provider is first-class, GitHub Actions integration is simple. State stored in GCS.

**Trade-off accepted:** Bootstrap chicken-and-egg: need GCP credentials to create GCS state bucket. Requires a one-time `bootstrap.sh` pre-step.

---

### moss (semantic search)

**Rationale:** Provides sub-10ms local semantic search for calendar event retrieval without a vector database. Pipecat integration exists. Good for injecting relevant context before each voice session. Valuable learning exercise on how retrieval augments voice pipelines.

**Trade-off accepted:** At personal calendar scale (<500 events), moss adds complexity for a benefit that could be achieved by dumping all events into the system prompt. Justified as a learning exercise, not a performance requirement.

---

## What We Explicitly Chose NOT to Use

| Technology | Reason |
|---|---|
| OpenAI Realtime API | 10x more expensive ($0.10/min vs $0.01/min). No GCP integration advantage. |
| Ultravox (self-hosted) | Requires a persistent GPU VM (~$0.50/hr+). High ops burden for a learning project. |
| Calendly / cal.com | Can't reschedule existing meetings, can't apply context-inferred availability rules, no voice. Defeats the point. |
| Siri / Google Assistant API | Black box, no extensibility. |
| AWS / Azure | No GCP billing advantage. GCP + Gemini is the natural stack. |
| LangChain / LangGraph | Unnecessary abstraction for a fixed-flow application. |
| Redis / Postgres / Cloud SQL | Firestore covers the invite link storage use case at zero cost. No relational data model needed. |
| Docker Compose (local dev) | Plain `python -m uvicorn` + Firestore emulator is sufficient. |
| OpenRouter (voice) | OpenRouter has no voice/audio models. Used only for text LLM (Nemotron, post-MVP). |
| Google Calendar Events API (for availability) | freebusy API is the right tool: it returns only busy ranges, never event details — which is exactly what the privacy model requires. |
| moss (MVP) | The freebusy API makes semantic calendar search irrelevant for availability queries. Moved to post-MVP. |
