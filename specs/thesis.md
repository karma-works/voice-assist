# Thesis: Why voice-assist Needs to Exist

_Revised 2026-05-30 — pivot from personal assistant to external scheduling tool_

## The Broken Status Quo

Scheduling a meeting with someone you don't know well is one of the most friction-filled interactions in everyday professional and personal life. The current options:

- **Email back-and-forth:** "Does Tuesday work?" / "Tuesday I'm busy, how about Thursday?" / "Thursday afternoon?" — 3–6 messages, 24–48 hours per round-trip.
- **Calendly / cal.com / TidyCal:** Better, but still form-based. The person books a slot from a fixed grid. If their preferred time isn't on the grid, they email anyway. If they want to reschedule something already booked, they email anyway.
- **Group polls (Doodle, When2meet):** For multi-person scheduling, not for single bilateral meetings.
- **AI scheduling tools (Reclaim, Clara, x.ai):** Expensive, complex, require both parties to use the same platform or tolerate email bots.

None of these solutions handle: (a) rescheduling an existing meeting via a natural conversation, (b) inferring meeting type from context and applying different availability rules, (c) doing all of this in the caller's language.

## Why Existing Solutions Fail for This Use Case

- **Calendly:** Can't reschedule existing meetings. Can't apply different time windows by meeting type (only one availability grid per event type, unless you create multiple event types). No voice. The person still has to switch to a browser form.
- **Google Calendar "Find a time":** Only works when both parties are in the same Google Workspace. Irrelevant for external visitors.
- **x.ai / Clara:** Require the calendar owner to CC a bot on every scheduling email. Heavy workflow change, not suitable for ad-hoc use.

The real gap: **a lightweight, link-based, zero-installation scheduling experience that handles natural language, respects context-dependent rules, and doesn't require the calendar owner to be present or online**.

## The Signal

Voice interfaces for scheduling are becoming viable because:
1. End-to-end voice models (Gemini Live, GPT-4o Realtime) handle multilingual conversational audio in 2025 at cents-per-minute pricing.
2. A "send someone a link to book time with me" pattern is already culturally established via Calendly — the barrier is UI, not concept.
3. People routinely expect to talk to voice agents for appointments (doctor, bank, service). A scheduling agent is in the same category.

## The Claim

voice-assist replaces the email back-and-forth for scheduling a meeting with Christian with a single voice conversation: click link, speak, done — in whatever language the visitor speaks, regardless of whether it's a rescheduling or a new booking, and without exposing Christian's calendar contents to the visitor.

This is also a learning project. The implementation teaches: invite-link auth patterns, privacy-preserving calendar availability APIs, multi-language real-time voice, Firestore for ephemeral token storage, and GitHub Actions as an admin workflow trigger.
