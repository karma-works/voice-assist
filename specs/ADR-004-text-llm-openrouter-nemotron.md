# ADR-004: Text LLM — Nemotron 3 Nano via OpenRouter (Post-MVP)

**Status:** Decided (Post-MVP only)  
**Date:** 2026-05-30

## Context

The original project spec called for NVIDIA Nemotron as the primary model billed via OpenRouter. Investigation revealed:
- The model available on OpenRouter (`nvidia/nemotron-3-nano-30b-a3b:free`) is a text-only MoE model — not an audio model.
- It is free on OpenRouter's free tier with a 256K context window.
- OpenRouter does not support audio input/output or real-time WebSocket streaming.

The voice layer (Gemini 2.0 Flash Live) already handles all in-session reasoning including calendar tool use. This raises the question: what role, if any, does a separate text LLM play?

Potential use cases for a text LLM alongside the voice model:
- **Daily briefing generation:** Fetch the day's events and produce a structured German summary injected as session context.
- **Batch event analysis:** "Which weeks am I most overloaded?" — not a voice query, a data analysis task.
- **Calendar insight reports:** Weekly recaps, pattern detection.

These are not MVP features. Gemini Flash (the text version) could equally serve these use cases via the same Google billing account.

## Decision

Include **OpenRouter + Nemotron 3 Nano** as a **post-MVP** text LLM integration, used exclusively for batch calendar analysis and daily briefing generation — not for voice session reasoning. Do not implement this at MVP.

## Rationale

- Nemotron 3 Nano is free (0 API cost). Using it for batch tasks costs nothing.
- OpenRouter provides a single API endpoint for potential future model swapping.
- Keeping OpenRouter in the stack satisfies the original project goal of having AI billing via OpenRouter, even if the voice layer can't use it.
- The post-MVP positioning avoids adding complexity (a second LLM, a second API key, a second billing account) before the core voice loop is validated.

## What This Option Does NOT Do Well

- Nemotron 3 Nano's German quality for generating natural German text summaries is untested. It may produce grammatically awkward output.
- The free tier has weekly token limits (35.6B tokens/week — more than sufficient for personal use, but worth noting).
- OpenRouter cannot route voice requests, so the "all AI billing via OpenRouter" goal is partially unmet for the voice layer.
- Adds a second API key and dependency to the project.

## Consequences

- `OPENROUTER_API_KEY` secret added to GCP Secret Manager and GitHub Secrets at post-MVP phase.
- Backend gains a `/briefing` endpoint that calls OpenRouter with the `nvidia/nemotron-3-nano-30b-a3b:free` model.
- The briefing output is German text, injected as system context into new Gemini Live sessions.
- At MVP, this endpoint is a stub that returns an empty string — the architecture supports it without requiring it.
