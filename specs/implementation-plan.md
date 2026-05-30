# Implementation Plan: voice-assist

_Revised 2026-05-30 — visitor-facing scheduling tool_

## Realistic Timeline Assessment

Solo developer, moderate infrastructure complexity. The core voice + availability loop can be working in 1–2 weeks. Full production infrastructure (Terraform, Firestore, two GitHub Actions workflows, privacy-correct tool design, timezone handling) realistically takes 5–7 weeks. The plan below is honest about that.

---

## Phase 0: MVP (Weeks 1–7)

### Week 1: Core Voice Loop Prototype

**Goal:** Prove Gemini Live function calling works with availability data. Nothing deployed, no real calendar yet.

- [ ] Run `bootstrap.sh` once (create GCS bucket, enable APIs, set up WIF)
- [ ] Set up Python project: `pyproject.toml`, `requirements.txt`, venv
- [ ] Install: `pipecat-ai[gemini]`, `google-auth`, `google-api-python-client`, `google-cloud-firestore`
- [ ] Write a minimal Pipecat + Gemini Live prototype:
  - System prompt (English + multilingual detection instructions)
  - `get_available_slots` tool with **hardcoded fake slots** (no real calendar yet)
  - `book_meeting` tool stub (prints to stdout, no calendar write)
- [ ] Verify: Gemini Live calls the tool, receives the result, speaks a confirmation
- [ ] Verify: barge-in works (interrupt a response mid-sentence)
- [ ] Verify: multilingual — speak English → agent responds in English; speak German → agent responds in German
- [ ] **Milestone:** Voice session with tool call working locally. Latency from speech-end to first audio < 1.5s.

---

### Week 2: Google Calendar Integration (Availability + Booking)

**Goal:** Real calendar data. Privacy-correct freebusy API. Booking creates actual events.

- [ ] Run `scripts/authorize_calendar.py` locally → capture refresh token → store in `.env` (local only, never committed)
- [ ] Implement `CalendarService`:
  ```python
  async def get_freebusy(time_min: datetime, time_max: datetime) -> list[tuple[datetime, datetime]]
  # Calls freebusy API across ALL calendars from calendarList.list()
  # Returns list of (busy_start, busy_end) in UTC

  async def get_available_slots(
      date_range_start: datetime,
      date_range_end: datetime,
      duration_minutes: int,
      slot_type: Literal["business", "private"],
  ) -> list[dict]
  # Applies slot_type rules in Europe/Berlin timezone
  # Adds 15-min buffers around busy blocks
  # Returns up to 6 candidate slots as {start, end, display_str}

  async def create_event(
      title: str,
      start: datetime,
      end: datetime,
      visitor_name: str,
      visitor_email: str,
      topic: str,
  ) -> str  # returns event_id
  # Creates event, adds visitor as guest → Google sends them a calendar invite
  ```
- [ ] Verify: freebusy query covers ALL Christian's calendars (calendarList.list), not just primary
- [ ] Verify: business slots (Mon–Fri 7–15 Berlin) correctly computed
- [ ] Verify: private slots (any day 0–22 Berlin) correctly computed
- [ ] Verify: DST edge case — test a slot around last Sunday of March and October
- [ ] Verify: event creation adds visitor email as guest (Google Calendar sends invite automatically)
- [ ] Wire `CalendarService` into the Pipecat prototype from Week 1
- [ ] Test end-to-end: voice query → real calendar slots → booking appears in Google Calendar
- [ ] **Milestone:** Full booking flow on real calendar, locally

---

### Week 3: Reschedule + Privacy Tools

**Goal:** `find_meeting_at` and `reschedule_meeting` work correctly. Privacy guarantees verified.

- [ ] Implement `find_meeting_at(approx_datetime, visitor_email, tolerance_minutes=30)`:
  - Queries Google Calendar events API for events in ±tolerance window
  - Returns `{event_id, start, end, found: bool}` — **no title, no description, no other guests**
  - If visitor_email provided: only returns found=True if visitor is in the guest list
  - If multiple matches: returns all of them (agent disambiguates)
- [ ] Implement `reschedule_meeting(event_id, new_start, new_end)`:
  - Calls Google Calendar events.patch()
  - Google Calendar sends updated invite to all existing guests automatically
- [ ] Implement `get_available_slots` for reschedule context (same logic as booking)
- [ ] Add all 4 tools to Gemini Live session config
- [ ] Privacy verification:
  - [ ] System prompt explicitly states model cannot reveal event details
  - [ ] Confirm `find_meeting_at` never returns event title in any code path
  - [ ] Manual test: ask the agent "What's on Christian's calendar Tuesday?" → agent must refuse / say it doesn't have access
  - [ ] Manual test: "Is he in a meeting called [X]?" → agent must not confirm or deny
- [ ] Handle edge cases:
  - [ ] Recurring event reschedule → single instance only
  - [ ] No matching event → agent says it couldn't find it, suggests booking instead
  - [ ] Multiple matching events → agent asks for duration or other disambiguating detail
- [ ] **Milestone:** Reschedule flow works. Privacy holds under adversarial questions.

---

### Week 4: Invite Links (Firestore + GitHub Actions)

**Goal:** Invite link generation and validation working end-to-end.

- [ ] Write `scripts/generate_invite.py` — generates UUID, writes to Firestore, prints URL
- [ ] Set up Firestore locally (Firestore emulator or real GCP project with Terraform)
- [ ] Write `validate_invite(uuid)` in the backend:
  - Firestore doc lookup
  - Check: exists, status == "active", age < 10 days (server-side, don't rely on TTL deletion)
- [ ] Write `.github/workflows/generate-invite.yml`:
  - `workflow_dispatch` with optional `label` input
  - Runs `generate_invite.py`, outputs URL to job log
- [ ] Test: run workflow → link appears in job log → open link → backend validates it
- [ ] Test: run workflow → wait (or manually set created_at to 11 days ago in Firestore) → link rejected
- [ ] Test: status = "revoked" → link rejected
- [ ] Implement PWA invite extraction: read `?invite=` from URL, pass as WebSocket query param
- [ ] Implement static error page for expired/invalid links (no voice session, clear message)
- [ ] **Milestone:** Invite links generate via GitHub Actions. Valid links open a session. Invalid/expired links show error page.

---

### Week 5: Backend Server + FastAPI

**Goal:** Full backend wired up. WebSocket, auth validation, session lifecycle.

- [ ] Implement FastAPI app structure:
  ```
  src/
    main.py          # FastAPI app, routes
    calendar.py      # CalendarService
    invite.py        # validate_invite(), Firestore client
    session.py       # Pipecat session builder + tool dispatch
    tools.py         # Tool definitions (Gemini format)
    availability.py  # Slot computation, timezone logic
    config.py        # Settings from env vars
  static/            # PWA files (served by FastAPI)
  ```
- [ ] `GET /health` → `{"status": "ok"}`
- [ ] `WebSocket /ws?invite=<uuid>`:
  1. Validate invite UUID (Firestore)
  2. If invalid: `close(4001)`
  3. If valid: build Pipecat pipeline, open Gemini Live session
  4. Session lifecycle: on disconnect, clean up Pipecat pipeline
- [ ] Implement system prompt builder:
  - Inject: current datetime (Europe/Berlin, in English)
  - Privacy rules: "You can only see time availability. Never reveal what existing meetings are called."
  - Language detection: "Detect the visitor's language from their first message and respond in that language for the entire session."
  - Meeting type inference rules
  - Confirmation policy: "Always confirm visitor name and email letter-by-letter before booking."
- [ ] Tool dispatch wired: all 4 tools routed to `CalendarService`
- [ ] Session error handling: if Gemini Live drops, log and send a WS close frame so PWA can show a reconnect prompt
- [ ] **Milestone:** Full voice → calendar session runs end-to-end via WebSocket, locally

---

### Week 6: PWA Client

**Goal:** Browser UI that works on Chrome and Safari.

- [ ] `static/index.html`:
  - On load: extract `?invite=` from URL
  - If missing: show "This link is invalid" static page
  - If present: show "Start" button
  - Status indicator: Connecting / Ready / Listening / Speaking / Error
  - "Restart session" button
- [ ] `static/app.js`:
  - Audio capture (MediaStream, 16kHz PCM via AudioWorklet)
  - AudioContext initialized inside click handler (Safari policy)
  - WebSocket connect with `?invite=<uuid>` query param
  - Handle WS close code 4001: show "This link has expired" message
  - WebSocket reconnect with exponential backoff (max 3 retries, then show error)
  - Barge-in: detect mic activity, send interrupt signal to backend while assistant is speaking
- [ ] `static/manifest.json`: PWA installable
- [ ] `static/sw.js`: offline fallback page only
- [ ] Test on Chrome desktop: full flow
- [ ] Test on Safari iOS: mic permission, audio playback (AudioContext user-gesture requirement)
- [ ] **Milestone:** Full flow working in Chrome and Safari. Invite link validated in browser.

---

### Week 7: Terraform + CI/CD + Deployment

**Goal:** Full infrastructure as code. Push to main → auto-deploy. Everything reproducible.

- [ ] Write all Terraform modules (cloudrun, firestore, iam, secretmanager, artifactregistry)
- [ ] `lifecycle { prevent_destroy = true }` on: Firestore database, GCS state bucket
- [ ] Cloud Run: `min_instances = 1` (no cold starts for visitors), `timeout = 3600s`
- [ ] Write `Dockerfile`:
  - `python:3.12-slim`
  - Install deps, copy `src/` + `static/`
  - `CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8080"]`
- [ ] Write `.github/workflows/deploy.yml` (deploy on push to main)
- [ ] Write `scripts/set_secrets.sh` — sets all GitHub Secrets via `gh secret set`
- [ ] Write `scripts/authorize_calendar.py` — one-time Google OAuth, writes refresh token to Secret Manager
- [ ] Write `bootstrap.sh` — one-time GCP setup (bucket, APIs, WIF)
- [ ] **Implementing agent runs `scripts/set_secrets.sh`** to set all required GitHub Secrets
- [ ] Push to `main` → verify GitHub Actions deploys successfully
- [ ] Verify Cloud Run URL is live, invite generation workflow works in production
- [ ] Test full visitor flow end-to-end in production (open invite URL → voice conversation → event in Google Calendar)
- [ ] **Milestone: MVP complete.** All 7 success criteria from `vision.md` are met.

---

### MVP Launch Criteria

- [ ] Invite link generation workflow produces a valid URL via `workflow_dispatch`
- [ ] Expired/invalid links show error page with no voice session
- [ ] Visitor can book a meeting via voice (event appears in Google Calendar with guest invite)
- [ ] Visitor can reschedule an existing meeting via voice
- [ ] Agent never reveals existing meeting titles under direct questioning
- [ ] Business slots land only Mon–Fri 07:00–15:00 Europe/Berlin
- [ ] Private slots land any day 00:00–22:00 Europe/Berlin
- [ ] Push to `main` deploys automatically, no manual steps
- [ ] `terraform destroy && terraform apply` recreates infrastructure from scratch

---

## Phase 1: Polish (Weeks 8–11, outline only)

- Admin endpoint: `GET /admin/invite/new?label=X` authenticated via Google OAuth (faster than GitHub Actions)
- Invite link revocation: update Firestore `status` to `revoked` via admin endpoint
- Email spell-out confirmation: agent reads visitor email letter-by-letter before booking
- Proactive slot suggestion: agent suggests 3 concrete slots upfront rather than asking visitor to propose dates
- Meeting duration inference: agent asks visitor how long the meeting should be (default: 30 min if not specified)
- Visitor timezone detection: agent asks and confirms time in both timezones

## Phase 2: Extensions (Post Phase 1, outline only)

- Google Calendar event description: include meeting topic from the voice conversation
- Post-meeting follow-up: if event is created today, send reminder 1 day before (requires background job)
- Multiple-language system prompt variants (system prompt currently English-only for language detection)
- Moss integration for historical visitor context (who has booked before, what topics)
- OpenRouter / Nemotron daily briefing for Christian (list of upcoming bookings from invite sessions)
- Invite analytics: how many times was each link opened (Firestore read counter)
