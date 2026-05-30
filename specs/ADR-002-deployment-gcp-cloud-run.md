# ADR-002: Deployment — GCP Cloud Run + Terraform + GitHub Actions

**Status:** Decided  
**Date:** 2026-05-30

## Context

The project needs a deployment platform for the backend (Python/FastAPI WebSocket server). Requirements:
- Low cost for personal use (sporadic usage, ideally scales to zero)
- HTTPS out of the box
- WebSocket support for long-lived voice sessions
- IaC-manageable via Terraform
- CI/CD via GitHub Actions on push to `main`
- Secrets stored in GitHub Secrets, auto-set by the implementing agent

Alternatives evaluated:
- **GCP Compute Engine (VM):** Always-on, persistent. ~$25/month minimum for a small VM. Overkill for personal use, manual patching burden.
- **GCP GKE (Kubernetes):** Significant ops overhead. Unjustified for a single-container personal app.
- **Cloudflare Workers:** No persistent WebSocket server support (Workers are edge functions, not long-lived processes). Cannot proxy Gemini Live's multi-minute WebSocket sessions.
- **Cloudflare Tunnels + VPS:** Possible, but Cloudflare's free tier doesn't support persistent server-side WebSockets at the required connection duration.
- **GCP Cloud Run:** Serverless containers, scales to zero, WebSocket support (up to 3600s), HTTPS default, GitHub Actions deploy action exists, GCP-native.

## Decision

Deploy the backend on **GCP Cloud Run**, managed with **Terraform**, deployed via **GitHub Actions** on every push to `main`. Use **GCP Secret Manager** for runtime secrets. Use **GCP Workload Identity Federation** for GitHub Actions to authenticate to GCP without long-lived service account keys.

## Rationale

- Cloud Run scales to zero: $0 cost when not in use. For personal use (10–30 min/day), actual compute cost is negligible.
- HTTPS is automatic via Cloud Run's managed TLS. No certificate management.
- WebSocket sessions up to 3600s: sufficient for voice conversations (typical session < 30 min).
- Workload Identity Federation eliminates the need to store GCP service account JSON keys in GitHub Secrets — more secure and avoids key rotation.
- Co-location: Cloud Run in `europe-west1` (Frankfurt) + Gemini Live in the same region minimizes audio proxy latency.

**Infrastructure managed by Terraform:**
- Cloud Run service (container image, env vars, concurrency, timeout)
- IAM service account for Cloud Run
- GCP Secret Manager secrets (Gemini API key, Google OAuth credentials, OpenRouter key)
- GCS bucket for Terraform state
- Artifact Registry repository for container images
- Workload Identity Pool and Provider for GitHub Actions

**CI/CD flow:**
1. Push to `main` → GitHub Actions triggered
2. Build Docker image → push to GCP Artifact Registry
3. `terraform apply` → update Cloud Run service with new image
4. Health check on new revision

## What This Option Does NOT Do Well

- WebSocket sessions don't survive Cloud Run instance restarts (scale-down after idle). Client must implement reconnect.
- Minimum instance count = 0 means cold starts (~1–3s). For a voice assistant, this means the first connection after idle may be slow.
- Cloud Run is not suitable if the project later needs always-on background processing (e.g., proactive reminder push, wake word detection).
- Terraform state in GCS requires a one-time bootstrap before Terraform can manage anything.

## Consequences

- A `bootstrap.sh` script is required for first-time setup (create GCS state bucket, enable GCP APIs, set up Workload Identity Federation).
- The PWA client must implement WebSocket reconnect with exponential backoff.
- Cloud Run minimum instances can be set to 1 to eliminate cold starts (adds ~$15/month).
- GitHub Actions secrets required: `GCP_PROJECT_ID`, `GCP_REGION`, `GCP_WORKLOAD_IDENTITY_PROVIDER`, `GCP_SERVICE_ACCOUNT` — set via `gh secret set`.
- Runtime secrets (Gemini API key, OAuth credentials) stored in GCP Secret Manager, referenced in Terraform, mounted as environment variables in Cloud Run.
