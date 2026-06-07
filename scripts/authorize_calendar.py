#!/usr/bin/env python3
"""One-time Google OAuth authorization.

Run on the dev server. Opens an auth URL — visit it in your browser,
approve access, then paste the full redirect URL back here.

Requires only: google-auth-oauthlib (already in requirements.txt)
"""
import os
import sys
from urllib.parse import urlparse, parse_qs
from google_auth_oauthlib.flow import Flow

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
        "redirect_uris": ["http://localhost"],
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
    }
}

flow = Flow.from_client_config(client_config, scopes=SCOPES, redirect_uri="http://localhost")
auth_url, _ = flow.authorization_url(prompt="consent", access_type="offline")

print("\nOpen this URL in your browser and sign in as chris.haegele@gmail.com:")
print(f"\n{auth_url}\n")
print("After approving, your browser will try to load http://localhost/?code=...")
print("It will show an error (connection refused) — that is expected.")
print("Copy the FULL URL from the browser address bar and paste it here.\n")

redirect_url = input("Paste the full redirect URL: ").strip()

parsed = urlparse(redirect_url)
params = parse_qs(parsed.query)
code = params.get("code", [None])[0]
if not code:
    print("ERROR: could not find 'code' in the URL. Make sure you pasted the full URL.", file=sys.stderr)
    sys.exit(1)

flow.fetch_token(code=code)
creds = flow.credentials

if not creds.refresh_token:
    print("ERROR: no refresh token returned. Try revoking access at https://myaccount.google.com/permissions and re-running.", file=sys.stderr)
    sys.exit(1)

print("\n" + "=" * 60)
print("SUCCESS — storing refresh token in Secret Manager...")
print("=" * 60)

import subprocess, sys
result = subprocess.run(
    ["gcloud", "secrets", "versions", "add", "GOOGLE_CALENDAR_REFRESH_TOKEN",
     "--data-file=-", "--project=gaphunter-496315"],
    input=creds.refresh_token.encode(),
    capture_output=True,
)
if result.returncode == 0:
    print("Refresh token stored in Secret Manager.")
    print("Run: gcloud run services update voice-assist --region=europe-west6 --project=gaphunter-496315 (or push a commit to redeploy)")
else:
    print("Secret Manager store failed:", result.stderr.decode())
    print("\nRefresh token (store manually):")
    print(creds.refresh_token)
