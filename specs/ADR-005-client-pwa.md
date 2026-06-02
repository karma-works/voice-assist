# ADR-005: Client — Progressive Web App (PWA)

**Status:** Decided  
**Date:** 2026-05-30

## Context

The assistant needs a client interface where Christian speaks and listens. Options:
- **Mobile app (native):** Most capable for microphone access, push notifications, wake word. Requires App Store submission or TestFlight. Significant dev overhead.
- **Desktop app (Electron):** Cross-platform, good audio APIs, no App Store. Still more complex than a web app.
- **CLI (Python):** Simple, fast to build. No visual feedback, awkward for audio.
- **PWA (web browser):** Works on any device, no install required (or optional install), uses Web Audio API and MediaStream API, serves from Cloud Run, HTTPS required (already provided by Cloud Run).

## Decision

Build the client as a **Progressive Web App (PWA)** using vanilla HTML/JavaScript or a minimal framework (no React, no heavy build toolchain for MVP). Served by the FastAPI backend from Cloud Run.

## Rationale

- No separate deployment: the PWA is served by the same Cloud Run container as the backend. Single deploy unit.
- Web Audio API + MediaStream API provide sufficient microphone capture and audio playback for the use case.
- Installable on mobile or desktop via "Add to Home Screen" when needed.
- Fastest path to a working prototype — no build toolchain, no native SDK.
- HTTPS from Cloud Run means browser microphone permissions work without special configuration.

**PWA features for MVP:**
- `manifest.json` (name, icon, theme color)
- Single-page app: one view, push-to-talk button, status indicator, recent utterances
- Explicit readiness display for voice API usability and Google Calendar connectivity
- Outage message with Start disabled when voice or calendar readiness fails
- Service worker: offline page only (not full offline capability — real-time voice requires connectivity)
- Web Audio API: capture mic audio as PCM, play back received audio
- WebSocket: connect to backend WebSocket endpoint for voice streaming

## What This Option Does NOT Do Well

- **Safari (iOS/macOS):** Autoplay audio policy requires a user gesture before any audio plays. The push-to-talk button IS a user gesture, so this works — but automated audio responses (e.g., proactive reminders) cannot play automatically.
- **Background audio:** Browser tabs can't play audio when the tab is not active in most browsers. For an always-on assistant, a PWA is insufficient.
- **Wake word detection:** Not possible in a browser without a persistent background process. Push-to-talk is the only activation model.
- **Push notifications:** Possible via Web Push API but requires notification permission and a service worker — adds complexity, post-MVP.

## Consequences

- The FastAPI backend must serve static PWA files (or use a CDN-mapped GCS bucket, but that's extra complexity for MVP — serve from FastAPI).
- PWA must request microphone permission on first load and explain why.
- Audio context must be initialized inside a click handler (Safari/Chrome autoplay policy).
- WebSocket reconnect with exponential backoff is required in the PWA JavaScript.
- The PWA must call a backend readiness endpoint before opening voice and must not start the session if the voice API or calendar connection is unavailable.
- The PWA must handle the Google OAuth redirect flow (receive the auth code and exchange it for a session).
