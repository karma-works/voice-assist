#!/usr/bin/env python3
"""Generate an invite link and write it to Firestore. Run by GitHub Actions workflow_dispatch."""
import os
import sys
import uuid
from datetime import datetime, timezone, timedelta

from google.cloud import firestore

PROJECT_ID = os.environ.get("GCP_PROJECT_ID", "gaphunter-496315")
APP_BASE_URL = os.environ.get("APP_BASE_URL", "").rstrip("/")
LABEL = os.environ.get("INPUT_LABEL", "")
EXPIRY_DAYS = int(os.environ.get("INVITE_EXPIRY_DAYS", "10"))

if not APP_BASE_URL:
    print("ERROR: APP_BASE_URL environment variable is required", file=sys.stderr)
    sys.exit(1)

db = firestore.Client(project=PROJECT_ID)
invite_id = str(uuid.uuid4())
now = datetime.now(timezone.utc)

db.collection("invite_links").document(invite_id).set({
    "created_at": now,
    "label": LABEL,
    "status": "active",
    "ttl": now + timedelta(days=EXPIRY_DAYS),
})

url = f"{APP_BASE_URL}/?invite={invite_id}"
print(f"Invite URL: {url}")
print(f"Label: {LABEL or '(none)'}")
print(f"Expires: {(now + timedelta(days=EXPIRY_DAYS)).strftime('%Y-%m-%d %H:%M UTC')}")
