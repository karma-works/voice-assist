import os
from dotenv import load_dotenv

load_dotenv()

# Uses Application Default Credentials / Vertex AI — no API key needed
GCP_PROJECT_ID = os.environ.get("GCP_PROJECT_ID", "")
GCP_LOCATION = os.environ.get("GCP_LOCATION", "europe-west1")
GOOGLE_CALENDAR_ID = os.environ.get("GOOGLE_CALENDAR_ID", "primary")
INVITE_EXPIRY_DAYS = int(os.environ.get("INVITE_EXPIRY_DAYS", "10"))
BUFFER_MINUTES = int(os.environ.get("BUFFER_MINUTES", "15"))
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "google/gemini-live-2.5-flash-native-audio")
APP_BASE_URL = os.environ.get("APP_BASE_URL", "http://localhost:8080")

# Client-side ambient comfort bed (faint room tone under the conversation).
# Off by default; toggled via the COMFORT_BED_ENABLED GitHub Actions variable.
COMFORT_BED_ENABLED = os.environ.get("COMFORT_BED_ENABLED", "false").strip().lower() in ("1", "true", "yes", "on")
