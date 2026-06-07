#!/usr/bin/env python3
"""One-time Google OAuth authorization.

Run locally to get a refresh token, then paste it when prompted so it
can be stored in Secret Manager from the dev server.

Requires only: google-auth-oauthlib
  pip install google-auth-oauthlib  (or: pip install --break-system-packages google-auth-oauthlib)
"""
import os
import sys
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/calendar"]

CLIENT_ID = os.environ.get("GOOGLE_OAUTH_CLIENT_ID")
CLIENT_SECRET = os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET")

if not CLIENT_ID or not CLIENT_SECRET:
    print("Set GOOGLE_OAUTH_CLIENT_ID and GOOGLE_OAUTH_CLIENT_SECRET env vars first.", file=sys.stderr)
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

print("\n" + "=" * 60)
print("SUCCESS — copy this refresh token and store it in Secret Manager:")
print("=" * 60)
print(creds.refresh_token)
print("=" * 60)
print("\nRun on the dev server:")
print(f'  echo -n "PASTE_TOKEN_HERE" | gcloud secrets versions add GOOGLE_CALENDAR_REFRESH_TOKEN --data-file=- --project=gaphunter-496315')
