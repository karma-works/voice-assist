#!/usr/bin/env bash
# Set all required GitHub Secrets for the voice-assist repo.
# Run: GCP_PROJECT_ID=xxx GOOGLE_CALENDAR_ID=primary GOOGLE_OAUTH_CLIENT_ID=xxx ... ./scripts/set_secrets.sh
set -euo pipefail

REPO="karma-works/voice-assist"
PROJECT_ID="${GCP_PROJECT_ID:-}"
REGION="${GCP_REGION:-europe-west6}"
WIF_PROVIDER="${GCP_WORKLOAD_IDENTITY_PROVIDER:-}"
DEPLOY_SA="${GCP_SERVICE_ACCOUNT:-voice-assist-deploy@${PROJECT_ID}.iam.gserviceaccount.com}"

echo "Setting GitHub Secrets for $REPO..."

# Required env vars check
required=(GCP_PROJECT_ID GOOGLE_CALENDAR_ID GOOGLE_OAUTH_CLIENT_ID GOOGLE_OAUTH_CLIENT_SECRET)
for var in "${required[@]}"; do
  if [ -z "${!var:-}" ]; then
    echo "ERROR: $var is not set" >&2
    exit 1
  fi
done

# Auto-discover WIF provider if not set
if [ -z "$WIF_PROVIDER" ]; then
  WIF_PROVIDER=$(gcloud iam workload-identity-pools providers describe voice-assist-provider \
    --workload-identity-pool=gaphunter-github \
    --location=global \
    --project="$PROJECT_ID" \
    --format="value(name)" 2>/dev/null || echo "")
fi

if [ -z "$WIF_PROVIDER" ]; then
  echo "ERROR: Could not determine GCP_WORKLOAD_IDENTITY_PROVIDER" >&2
  exit 1
fi

gh secret set GCP_PROJECT_ID --repo "$REPO" --body "$PROJECT_ID"
gh secret set GCP_REGION --repo "$REPO" --body "$REGION"
gh secret set GCP_WORKLOAD_IDENTITY_PROVIDER --repo "$REPO" --body "$WIF_PROVIDER"
gh secret set GCP_SERVICE_ACCOUNT --repo "$REPO" --body "$DEPLOY_SA"
gh secret set GOOGLE_CALENDAR_ID --repo "$REPO" --body "$GOOGLE_CALENDAR_ID"
gh secret set GOOGLE_OAUTH_CLIENT_ID --repo "$REPO" --body "$GOOGLE_OAUTH_CLIENT_ID"
gh secret set GOOGLE_OAUTH_CLIENT_SECRET --repo "$REPO" --body "$GOOGLE_OAUTH_CLIENT_SECRET"

# APP_BASE_URL will be set after first deploy
if [ -n "${APP_BASE_URL:-}" ]; then
  gh secret set APP_BASE_URL --repo "$REPO" --body "$APP_BASE_URL"
fi

echo "Done. GitHub Secrets set for $REPO"
