import os
from dotenv import load_dotenv

load_dotenv()

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
# Calendar uses Application Default Credentials (ADC).
# Set to Christian's email address — the SA must be granted "Make changes" on this calendar.
GOOGLE_CALENDAR_ID = os.environ.get("GOOGLE_CALENDAR_ID", "chris.haegele@gmail.com")
ALLOWED_GOOGLE_ACCOUNTS = os.environ.get("ALLOWED_GOOGLE_ACCOUNTS", "")
GCP_PROJECT_ID = os.environ.get("GCP_PROJECT_ID", "gaphunter-496315")
INVITE_EXPIRY_DAYS = int(os.environ.get("INVITE_EXPIRY_DAYS", "10"))
BUFFER_MINUTES = int(os.environ.get("BUFFER_MINUTES", "15"))
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "models/gemini-2.5-flash-native-audio-preview-12-2025")
APP_BASE_URL = os.environ.get("APP_BASE_URL", "http://localhost:8080")
