# ADR-006: Semantic Search — moss

**Status:** Decided  
**Date:** 2026-05-30

## Context

The voice assistant needs to inject relevant calendar context into the Gemini Live system prompt before each session. Options:
- **Dump all calendar events into system prompt:** Simple, works at personal scale (<500 events). No extra dependencies.
- **Google Calendar API real-time queries:** Fetch events on demand inside tool calls. Low latency, always fresh, but requires a tool call round-trip for every lookup.
- **moss (semantic search runtime):** Index events locally, sub-10ms retrieval, no vector DB required, Pipecat integration, supports hybrid semantic+keyword search.
- **SQLite FTS5:** Full-text search, simple, fast, no semantic understanding.

The argument for moss:
1. **Performance:** Sub-10ms retrieval means no added latency before session start.
2. **Semantic understanding:** "Was hab ich kommende Woche bezüglich Arbeit?" — a semantic query can match "Quarterly Review", "1:1 with Team" without those words appearing in the query.
3. **Learning value:** Understanding how retrieval augments real-time voice pipelines is a core learning goal of this project.
4. **Pipecat native integration:** moss + Pipecat have existing integration patterns.

The argument against:
- At <500 calendar events, semantic search isn't solving a retrieval problem. It's solving a "make context smaller" problem that doesn't exist with a 1M-token context window.
- Adds a sync loop to keep the index current (Challenge 12 in `challenges.md`).
- moss is the authoritative source of truth risk — **never use moss as authoritative; always validate against Google Calendar API.**

## Decision

Use **moss** as a **retrieval hint layer** for pre-session context injection. moss is never the authoritative source of calendar data. All writes and confirmations go through Google Calendar API directly. moss is rebuilt/refreshed at session start with a fresh Google Calendar API fetch.

## Rationale

- The semantic search capability enables more natural pre-session context injection ("upcoming work events" vs. "all events in next 7 days") at no added latency cost.
- The rebuild-on-session-start approach (not a background sync loop) eliminates the stale data risk entirely. Each session starts with a fresh index from the live Calendar API.
- Pipecat integration simplifies the code.
- It's a legitimate learning exercise that demonstrates RAG in a real-time voice context.

## What This Option Does NOT Do Well

- Rebuild-on-session-start adds ~100–500ms of latency before the first session message (index building). This is acceptable for a push-to-talk model where the user hasn't spoken yet.
- moss is not a persistent index in this architecture — it's rebuilt every session. This means no cross-session memory or learning.
- Adds a Python dependency (`moss` package) and its embedding model to the container image, increasing image size and cold start time.

## Consequences

- On every new WebSocket session: fetch upcoming 4 weeks of calendar events from Google Calendar API → build moss index → retrieve semantically relevant events for the current date → inject into Gemini Live system prompt.
- moss is never queried INSTEAD of a Google Calendar API tool call — only used for context pre-loading.
- The `MOSS_INDEX` is in-memory per session, not persisted to disk or GCS.
- If moss indexing fails (import error, OOM), the session falls back to dumping all events directly — not a blocking failure.
