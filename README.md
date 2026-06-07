# voice-assist

![voice-assist logo](assets/logo.svg)

`voice-assist` is a learning project for building modern low-latency voice-to-voice agents. The app is a browser-based voice scheduling assistant backed by FastAPI, Cloud Run, Pipecat, Gemini Live, Firestore invite links, and Google Calendar availability.

The goal is not just to book meetings. The project is a practical lab for understanding how realtime voice agents behave in production:

- latency from microphone input to first audible response
- voice-to-voice model orchestration with Pipecat and Gemini Live
- barge-in, interruption handling, and playback-buffer cleanup
- tool calls during a live audio session
- memory and session state for reconnects and confirmed facts
- privacy boundaries around calendar data
- deployment tradeoffs for Cloud Run, WebSockets, and cold starts

## Architecture

```text
Browser PWA
  -> FastAPI WebSocket transport
  -> Pipecat pipeline
  -> Gemini Live native audio model
  -> scheduling tools
  -> Google Calendar freebusy/events API
```

The browser captures microphone audio with the Web Audio API, sends 16 kHz PCM16 mono frames over WebSocket, and plays returned 24 kHz PCM16 audio. The backend uses Pipecat as the voice pipeline boundary and Gemini Live as the native audio-to-audio model.

## Public Logs And Secrets

This repository is intended to be public. For public GitHub repositories, GitHub Actions logs are publicly viewable. GitHub masks configured secret values, but anything printed directly by scripts can still become public if it is not registered as a secret or if it is derived from a secret.

Project rules:

- Do not print invite URLs in GitHub Actions logs. Invite links are bearer tokens.
- Do not print OAuth refresh tokens, API keys, credentials, or full environment dumps.
- Keep project IDs, calendar IDs, and deployment-specific values in environment variables or GitHub/GCP secrets.
- Treat workflow inputs as public unless the workflow is run in a private fork.

## Local Development

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
uvicorn src.main:app --reload --host 0.0.0.0 --port 8080
```

Required production environment variables:

- `GCP_PROJECT_ID`
- `GCP_LOCATION`
- `GOOGLE_CALENDAR_ID`
- `GEMINI_MODEL`
- Google Application Default Credentials or a Cloud Run service account with the required Vertex AI, Firestore, and Calendar access

## Tests

```bash
python3 -m unittest discover -s tests
```

## License

MIT
