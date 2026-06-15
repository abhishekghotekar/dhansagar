import os
from dotenv import load_dotenv

# load .env file
load_dotenv()

# vector database storage path
VECTOR_DB_PATH = os.getenv("VECTOR_DB_PATH", "./vector_store")

# embedding model name
EMBEDDING_MODEL = os.getenv(
    "EMBEDDING_MODEL",
    "all-MiniLM-L6-v2"
)

# Google Sheets Configuration
USE_GOOGLE_SHEETS = os.getenv("USE_GOOGLE_SHEETS", "true").lower() == "true"
GOOGLE_SHEET_ID = os.getenv("GOOGLE_SHEET_ID", "")
GOOGLE_SERVICE_ACCOUNT_JSON = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "google-credentials.json")
GOOGLE_SERVICE_ACCOUNT_DICT = os.getenv("GOOGLE_SERVICE_ACCOUNT_DICT", "")


