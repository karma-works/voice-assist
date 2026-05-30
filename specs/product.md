# Product Specification: voice-assist

_Revised 2026-05-30_

## User Types

| User | Goal | Auth mechanism | Trust level |
|---|---|---|---|
| Visitor | Book or reschedule a meeting with Christian | Valid invite link (UUID, ≤10 days old) | Limited: can book/reschedule meetings, cannot see calendar contents |
| Christian (admin) | Generate invite links, manage calendar | Google OAuth (whitelisted account) | Full: generates links via GitHub Actions |

---

## Core Flows

### Flow 1: Book a New Meeting

1. Christian generates an invite link via GitHub Actions `workflow_dispatch` → receives a URL like `https://voice-assist.example.com/?invite=<uuid>`.
2. Christian sends the URL to the visitor (email, message, etc.).
3. Visitor opens the URL in a browser.
4. Backend validates the invite UUID: exists in Firestore, not expired (< 10 days old). If invalid/expired → show static error page, no voice session.
5. Visitor clicks "Start" (push-to-talk activates; PWA requests mic permission).
6. Backend opens a Gemini Live session. System prompt contains: Christian's name, current datetime (Europe/Berlin), availability rules (business/private), strict privacy instructions.
7. Agent greets the visitor, asks what the meeting is about.
8. Based on the topic/context, agent infers meeting type:
   - Professional/work-related → applies business slot rules (Mon–Fri 07:00–15:00 Europe/Berlin)
   - Personal/private → applies private slot rules (any day 00:00–22:00 Europe/Berlin)
9. Agent calls `get_available_slots(date_range, meeting_duration_minutes, slot_type)`.
10. Backend calls Google Calendar API (freebusy query — no event details, just busy/free times).
11. Agent presents 3 concrete slot options: "I have Tuesday 10am, Wednesday 9am, or Thursday 2pm available."
12. Visitor picks one (or asks for alternatives).
13. Agent asks for visitor's name and email address.
14. Agent confirms: "I'll book Tuesday June 3rd at 10am for you, [Name]. Shall I confirm?"
15. Visitor confirms.
16. Agent calls `book_meeting(slot, visitor_name, visitor_email, topic, meeting_type)`.
17. Backend creates Google Calendar event with visitor as guest. Google Calendar sends the visitor an invite email automatically.
18. Agent confirms: "Done — you'll receive a calendar invite at [email]."
19. Session ends. Invite link remains valid until 10 days are up (multi-use).

### Flow 2: Reschedule an Existing Meeting

1. (Same steps 1–6 as above)
2. Visitor says: "I have a meeting with Christian on Tuesday the 3rd at 2pm — can we move it?"
3. Agent does NOT look up what the meeting is called. It calls `find_meeting_at(datetime)` which returns only: event_id, start time, end time, and whether the visitor's email is in the guest list (binary match, no title exposed).
4. If a match is found at that time: agent confirms "I found a meeting at that time. What would you like to change it to?"
5. Visitor proposes a new date/time range or asks for suggestions.
6. Agent calls `get_available_slots(...)` for the new range.
7. Agent presents 3 alternatives.
8. Visitor picks one.
9. Agent confirms the new time.
10. Agent calls `reschedule_meeting(event_id, new_start, new_end)`.
11. Backend updates the Google Calendar event. Google Calendar sends updated invite to all guests.
12. Agent confirms the reschedule.

### Flow 3: Invite Link Generation (Admin, GitHub Actions)

1. Christian navigates to the repository's GitHub Actions tab.
2. Selects the "Generate Invite Link" workflow.
3. Clicks "Run workflow". Optional input: a label/note (e.g., "Meeting with Max M.") for Christian's own reference.
4. Workflow runs: generates a UUID v4, writes a Firestore document `{uuid, created_at, label, status: "active"}`, outputs the invite URL to the job log.
5. Christian copies the URL from the job output and sends it to the visitor.

---

## Tool Definitions (Backend Functions)

| Tool | Description | What it does NOT expose |
|---|---|---|
| `get_available_slots(date_range, duration_min, slot_type)` | Returns list of available time windows | Does not reveal what is blocking each slot |
| `book_meeting(slot, visitor_name, visitor_email, topic, meeting_type)` | Creates calendar event with visitor as guest | — |
| `find_meeting_at(datetime_approx, visitor_email)` | Returns event_id + confirmed time if a match exists | Does NOT return title, description, or other guests |
| `reschedule_meeting(event_id, new_start_datetime, new_end_datetime)` | Updates event time, triggers Google Calendar guest notification | — |

The `find_meeting_at` tool matches on time (±30 min tolerance) and optionally validates that the visitor's email is a guest. If no match or email mismatch: returns `{found: false}`. The agent responds: "I couldn't find a meeting at that time associated with your email."

---

## Availability Rules

These are enforced server-side in the `get_available_slots` implementation, not left to the agent's interpretation.

```
slot_type = "business"
  → allowed window: Monday–Friday, 07:00–15:00 (Europe/Berlin)
  → minimum slot: 30 minutes

slot_type = "private"
  → allowed window: every day, 00:00–22:00 (Europe/Berlin)
  → minimum slot: 30 minutes

Both types:
  → subtract all existing freebusy blocks from Google Calendar (primary calendar)
  → add a 15-minute buffer before and after existing events
  → return at most 6 candidate slots (agent presents the 3 most suitable)
```

### Meeting Type Inference

The agent infers `slot_type` from the conversation. The system prompt instructs:
- If the visitor mentions a company, work project, job title, invoice, collaboration, or any professional context → `business`
- If the visitor mentions personal topics, family, friends, hobbies, health → `private`
- If ambiguous → agent asks one clarifying question: "Is this a personal or professional meeting?"
- The agent must commit to a type before calling `get_available_slots`. Server-side rules enforce the window.

---

## Feature List

| Feature | MVP | Post-MVP | Notes |
|---|---|---|---|
| Invite link generation (GitHub Actions) | ✅ | | Firestore write + URL output |
| 10-day invite link expiry | ✅ | | Checked at WebSocket connect |
| Multi-use links (expire by time, not by use) | ✅ | | |
| Book new meeting via voice | ✅ | | Full tool call flow |
| Reschedule existing meeting via voice | ✅ | | `find_meeting_at` + `reschedule_meeting` |
| Privacy: no calendar content exposed | ✅ | | System prompt + tool design |
| Business slot rules (Mon–Fri 7–15) | ✅ | | Server-side enforcement |
| Private slot rules (any day 0–22) | ✅ | | Server-side enforcement |
| Meeting type inference from context | ✅ | | System prompt + agent reasoning |
| Auto-detect visitor language | ✅ | | Gemini Live multi-language |
| Google Calendar invite to visitor | ✅ | | Add visitor as guest on event creation |
| 15-min buffer around existing events | ✅ | | In `get_available_slots` |
| Static expiry/invalid error page | ✅ | | No voice session if link invalid |
| Admin endpoint to generate links | | ✅ | GitHub Actions is sufficient at MVP |
| Link label/notes for Christian's reference | ✅ | | Stored in Firestore doc |
| Moss calendar indexing | | ✅ | Not needed: freebusy API is better for availability |
| Daily briefing (Nemotron/OpenRouter) | | ✅ | |
| Cancel a meeting via voice | | ✅ | Higher risk: no confirmation from Christian |
| Invite link revocation (before 10 days) | | ✅ | Update Firestore `status` to `revoked` |
| Meeting duration selection | | ✅ | At MVP: agent asks visitor how long the meeting will be |

---

## Cost Estimate (per month, personal scale)

| Item | Cost |
|---|---|
| Gemini Live (voice sessions, ~10/month × 5 min avg) | ~$0.50 |
| Cloud Run (scales to zero, personal scale) | ~$0–3 |
| Firestore (< 1K docs, < 1K reads/writes/day) | Free tier |
| Google Calendar API | Free |
| GitHub Actions (private repo, ~50 workflow runs) | Free tier |
| **Total** | **< $5/month** |
