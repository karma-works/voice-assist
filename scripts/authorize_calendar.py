#!/usr/bin/env python3
"""One-time Google OAuth authorization. Run locally to get a refresh token and store it in Secret Manager."""
import os
import sys
import json
from google_auth_oauthlib.flow import InstalledAppFlow
from google.cloud import secretmanager

SCOPES = ["https://www.googleapis.com/auth/calendar"]
PROJECT_ID = os.environ.get("GCP_PROJECT_ID", "")
SECRET_NAME = "GOOGLE_CALENDAR_REFRESH_TOKEN"

CLIENT_ID = os.environ.get("GOOGLE_OAUTH_CLIENT_ID")
CLIENT_SECRET = os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET")

if not CLIENT_ID or not CLIENT_SECRET:
    print("Set GOOGLE_OAUTH_CLIENT_ID and GOOGLE_OAUTH_CLIENT_SECRET env vars first.", file=sys.stderr)
    sys.exit(1)
if not PROJECT_ID:
    print("Set GCP_PROJECT_ID first.", file=sys.stderr)
    sys.exit(1)

client_config = {
    "installed": {
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "redirect_uris": ["urn:ietf:wg:oauth:2.0:oob", "http://localhost"],
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
    }
}

flow = InstalledAppFlow.from_client_config(client_config, SCOPES)
creds = flow.run_local_server(port=0)

print("\nRefresh token obtained.")

# Store in Secret Manager
sm_client = secretmanager.SecretManagerServiceClient()
secret_path = f"projects/{PROJECT_ID}/secrets/{SECRET_NAME}"

try:
    sm_client.access_secret_version(name=f"{secret_path}/versions/latest")
    version = sm_client.add_secret_version(
        parent=secret_path,
        payload={"data": creds.refresh_token.encode()},
    )
    print(f"Updated secret: {version.name}")
except Exception:
    try:
        sm_client.create_secret(
            parent=f"projects/{PROJECT_ID}",
            secret_id=SECRET_NAME,
            secret={"replication": {"automatic": {}}},
        )
        version = sm_client.add_secret_version(
            parent=secret_path,
            payload={"data": creds.refresh_token.encode()},
        )
        print(f"Created secret: {version.name}")
    except Exception as e:
        print(f"Secret Manager error: {e}")
        print("Refresh token was not printed. Re-run after fixing Secret Manager access.")
