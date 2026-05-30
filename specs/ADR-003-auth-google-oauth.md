# ADR-003: Authentication — Two-Track Auth Model

**Status:** Decided  
**Date:** 2026-05-30  
_Revised 2026-05-30 — visitor auth is now invite links, not Google OAuth_

## Context

The system has two distinct user types with entirely different auth requirements:

**Visitors** (people scheduling meetings with Christian):
- Have no account in this system
- Must not be required to create one
- Their credential is an invite link (UUID in URL)
- Must be fast to validate (< 100ms at session start)

**Christian (admin)**:
- Needs to authorize the backend to access his Google Calendar (read + write)
- This is a server-to-server credential, not a session-by-session auth
- He only interacts with the admin path when generating invite links (GitHub Actions)

These are separate concerns that should not be conflated.

## Decision

**For visitors:** UUID invite link validation against Firestore (see ADR-008). No Google sign-in, no session cookie, no JWT. The UUID in the URL query param IS the credential. Validated on every WebSocket connect.

**For Christian's calendar access:** A one-time Google OAuth 2.0 authorization flow that produces a refresh token. The refresh token is stored in GCP Secret Manager. The backend uses it to call the Google Calendar API server-to-server on every voice session.

**There is no visitor-facing login page.** Either the invite link is valid or it isn't.

## Google Calendar OAuth (server-to-server)

Scopes required:
- `https://www.googleapis.com/auth/calendar` (read + write: create events, update events, freebusy query, calendarList read)

Token storage:
- Refresh token stored in GCP Secret Manager as `GOOGLE_CALENDAR_REFRESH_TOKEN`
- Client ID + Client Secret stored as `GOOGLE_OAUTH_CLIENT_ID` / `GOOGLE_OAUTH_CLIENT_SECRET`
- The backend loads these at startup, never exposes them to visitors

One-time authorization flow:
```bash
# Run locally by Christian, once. Not automated.
python scripts/authorize_calendar.py
# → Opens browser, Google consent, captures refresh token
# → Writes to GCP Secret Manager automatically
```

## Visitor Auth Flow

```
Visitor opens: https://voice-assist.example.com/?invite=<uuid>
  ↓
PWA extracts UUID, connects WebSocket: wss://backend/ws?invite=<uuid>
  ↓
Backend: validate_invite(uuid) → Firestore lookup
  ↓ valid              ↓ invalid/expired
Open Gemini Live    WS close(4001) + PWA shows error
session
```

The UUID is transmitted as a WebSocket query parameter over HTTPS/WSS. It is never stored client-side (no localStorage, no cookie). Each page load re-reads it from the URL.

## What This Option Does NOT Do Well

- **Invite link forwarding risk:** Anyone who receives the URL can use it within 10 days. This is a deliberate trade-off (see challenges.md #3).
- **No visitor identity verification:** The system trusts that the visitor is who they say they are (name + email they provide in the voice session). There's no proof the email belongs to them.
- **Google OAuth 7-day token expiry for unverified apps:** If the OAuth app is not published/verified by Google, the refresh token expires after 7 days. Must either (a) complete Google OAuth verification, or (b) implement a re-auth script that Christian runs when the token expires. Option (b) is simpler for a personal tool.

## Consequences

- No Google sign-in UI in the visitor-facing PWA.
- `authorize_calendar.py` script committed to `scripts/` — Christian runs this once, or when the refresh token expires.
- Backend reads refresh token from GCP Secret Manager at startup, refreshes access token automatically via `google-auth` library.
- WebSocket handler validates invite UUID before any Gemini Live session is opened.
- Terraform provisions: Secret Manager secrets for OAuth credentials and refresh token; IAM binding for Cloud Run service account to read those secrets.
