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

# Postgres Database URL
# Database credentials
POSTGRES_USER = os.getenv("POSTGRES_USER", "postgres")
POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD", "password")
POSTGRES_HOST = os.getenv("POSTGRES_HOST", "localhost")
POSTGRES_PORT = os.getenv("POSTGRES_PORT", "5432")
POSTGRES_DB = os.getenv("POSTGRES_DB", "customer_db")

# Postgres Database URL
POSTGRES_URL = os.getenv("POSTGRES_URL")
if not POSTGRES_URL:
    user_pass = f"{POSTGRES_USER}:{POSTGRES_PASSWORD}" if POSTGRES_PASSWORD else POSTGRES_USER
    POSTGRES_URL = f"postgresql://{user_pass}@{POSTGRES_HOST}:{POSTGRES_PORT}/{POSTGRES_DB}"
