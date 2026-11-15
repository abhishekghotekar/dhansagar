import os
from dotenv import load_dotenv

# Load local .env file if present
load_dotenv()

# Google Sheets Configuration
USE_GOOGLE_SHEETS = os.getenv("USE_GOOGLE_SHEETS", "true").lower() == "true"
GOOGLE_SHEET_ID = os.getenv("GOOGLE_SHEET_ID", "").strip()
GOOGLE_SERVICE_ACCOUNT_JSON = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "google-credentials.json").strip()
GOOGLE_SERVICE_ACCOUNT_DICT = os.getenv("GOOGLE_SERVICE_ACCOUNT_DICT", "").strip()
