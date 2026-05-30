from datetime import datetime, timezone, timedelta
from google.cloud import firestore
from src.config import GCP_PROJECT_ID, INVITE_EXPIRY_DAYS

_db: firestore.AsyncClient | None = None


def get_db() -> firestore.AsyncClient:
    global _db
    if _db is None:
        _db = firestore.AsyncClient(project=GCP_PROJECT_ID)
    return _db


async def validate_invite(invite_id: str) -> bool:
    if not invite_id or len(invite_id) != 36:
        return False
    db = get_db()
    doc = await db.collection("invite_links").document(invite_id).get()
    if not doc.exists:
        return False
    data = doc.to_dict()
    if data.get("status") != "active":
        return False
    created_at = data.get("created_at")
    if created_at is None:
        return False
    if hasattr(created_at, "tzinfo") and created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    age = now - created_at
    return age.days < INVITE_EXPIRY_DAYS
