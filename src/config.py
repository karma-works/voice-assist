import os
from dotenv import load_dotenv

load_dotenv()

# Uses Application Default Credentials / Vertex AI — no API key needed
GCP_PROJECT_ID = os.environ.get("GCP_PROJECT_ID", "gaphunter-496315")
GCP_LOCATION = os.environ.get("GCP_LOCATION", "us-central1")
GOOGLE_CALENDAR_ID = os.environ.get("GOOGLE_CALENDAR_ID", "chris.haegele@gmail.com")
INVITE_EXPIRY_DAYS = int(os.environ.get("INVITE_EXPIRY_DAYS", "10"))
BUFFER_MINUTES = int(os.environ.get("BUFFER_MINUTES", "15"))
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "google/gemini-live-2.5-flash-native-audio")
APP_BASE_URL = os.environ.get("APP_BASE_URL", "http://localhost:8080")
