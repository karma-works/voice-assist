# ADR-007: Infrastructure as Code — Terraform + GitHub Actions

**Status:** Decided  
**Date:** 2026-05-30  
_Revised 2026-05-30 — added invite link generation workflow_

## Context

The project requires:
- All GCP infrastructure defined in code (reproducible, destroyable, versionable)
- Two distinct GitHub Actions workflows:
  1. **Deploy workflow** — triggered on push to `main`, builds and deploys the app
  2. **Invite link generation workflow** — triggered manually (`workflow_dispatch`), generates and outputs an invite URL
- No long-lived service account keys in GitHub Secrets
- Secrets stored in GCP Secret Manager, referenced by Cloud Run

## Decision

**Terraform** for all GCP infrastructure. **GitHub Actions** for CI/CD and invite generation. **GCP Workload Identity Federation** for both workflows to authenticate to GCP. **GCP Secret Manager** for runtime secrets.

## Workflow 1: Deploy (`.github/workflows/deploy.yml`)

```yaml
on:
  push:
    branches: [main]

jobs:
  deploy:
    runs-on: ubuntu-latest
    permissions:
      contents: read
      id-token: write   # Required for WIF
    steps:
      - uses: actions/checkout@v4
      - uses: google-github-actions/auth@v2
        with:
          workload_identity_provider: ${{ secrets.GCP_WORKLOAD_IDENTITY_PROVIDER }}
          service_account: ${{ secrets.GCP_SERVICE_ACCOUNT }}
      - name: Build and push Docker image
        run: |
          gcloud auth configure-docker ${{ secrets.GCP_REGION }}-docker.pkg.dev
          docker build -t $IMAGE_TAG .
          docker push $IMAGE_TAG
      - name: Terraform apply
        run: |
          cd terraform
          terraform init
          terraform apply -auto-approve -var="image_tag=$IMAGE_TAG"
      - name: Smoke test
        run: curl -f https://${{ steps.deploy.outputs.url }}/health
```

## Workflow 2: Generate Invite Link (`.github/workflows/generate-invite.yml`)

```yaml
on:
  workflow_dispatch:
    inputs:
      label:
        description: 'Reference note for this invite (e.g. "Meeting with Max M.")'
        required: false
        default: ''

jobs:
  generate-invite:
    runs-on: ubuntu-latest
    permissions:
      contents: read
      id-token: write
    steps:
      - uses: actions/checkout@v4
      - uses: google-github-actions/auth@v2
        with:
          workload_identity_provider: ${{ secrets.GCP_WORKLOAD_IDENTITY_PROVIDER }}
          service_account: ${{ secrets.GCP_SERVICE_ACCOUNT }}
      - name: Install dependencies
        run: pip install google-cloud-firestore
      - name: Generate invite link
        env:
          APP_BASE_URL: ${{ secrets.APP_BASE_URL }}
          INPUT_LABEL: ${{ github.event.inputs.label }}
          GCP_PROJECT_ID: ${{ secrets.GCP_PROJECT_ID }}
        run: python scripts/generate_invite.py
      # The invite URL is printed to the job log (visible to Christian, private repo)
```

## Infrastructure Managed by Terraform

```
terraform/
  main.tf              # provider config, GCS backend
  variables.tf         # project_id, region, image_tag, app_base_url
  cloudrun.tf          # Cloud Run service (min_instances=1, timeout=3600s)
  firestore.tf         # Firestore database (Native mode), TTL policy
  iam.tf               # service accounts, WIF pool/provider, role bindings
  secretmanager.tf     # all secrets (GEMINI_API_KEY, GOOGLE_OAUTH_*, GOOGLE_CALENDAR_REFRESH_TOKEN)
  artifactregistry.tf  # Docker image registry
  outputs.tf           # Cloud Run service URL
```

**Firestore-specific Terraform:**
```hcl
resource "google_firestore_database" "default" {
  project     = var.project_id
  name        = "(default)"
  location_id = var.region
  type        = "FIRESTORE_NATIVE"
}

resource "google_firestore_field" "invite_ttl" {
  project    = var.project_id
  database   = google_firestore_database.default.name
  collection = "invite_links"
  field      = "ttl"
  ttl_config {}   # Enables TTL-based auto-delete on this field
}
```

## GitHub Secrets Set by Implementing Agent

The implementing agent sets these via `gh secret set`:

| Secret | Value source |
|---|---|
| `GCP_PROJECT_ID` | GCP project ID |
| `GCP_REGION` | e.g. `europe-west1` |
| `GCP_WORKLOAD_IDENTITY_PROVIDER` | Output from `bootstrap.sh` |
| `GCP_SERVICE_ACCOUNT` | Output from `bootstrap.sh` |
| `APP_BASE_URL` | Cloud Run service URL (output of first `terraform apply`) |

Runtime secrets in GCP Secret Manager (values set by implementing agent):

| Secret | Set via |
|---|---|
| `GEMINI_API_KEY` | `gh secret set` → Terraform pushes to Secret Manager |
| `GOOGLE_OAUTH_CLIENT_ID` | Same |
| `GOOGLE_OAUTH_CLIENT_SECRET` | Same |
| `GOOGLE_CALENDAR_REFRESH_TOKEN` | `scripts/authorize_calendar.py` writes directly to Secret Manager |
| `ALLOWED_GOOGLE_ACCOUNTS` | Christian's Google account ID (for future admin endpoint) |

## What This Option Does NOT Do Well

- `terraform destroy` will delete the Firestore database and all invite links. Add `lifecycle { prevent_destroy = true }` on Firestore.
- Bootstrap step is still required once (creates GCS bucket, enables APIs, sets up WIF).
- `APP_BASE_URL` is a chicken-and-egg: the Cloud Run URL isn't known until after the first deploy. First deploy sets it, then it's stored as a GitHub Secret and Terraform uses it in subsequent deploys.
- The invite URL appears in the GitHub Actions job log in plaintext. The repository must remain private.

## Consequences

- `scripts/generate_invite.py` is committed to the repo — must not contain hardcoded secrets.
- `scripts/authorize_calendar.py` is committed to the repo — runs locally by Christian for OAuth.
- `scripts/set_secrets.sh` is committed — the implementing agent runs this to set all GitHub Secrets via `gh secret set`.
- `bootstrap.sh` is committed — documents the one-time manual setup.
- Private repo is a hard requirement: job logs contain invite URLs.
