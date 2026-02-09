import chromadb
from app.core.config import VECTOR_DB_PATH

# Create persistent chroma client (data stored locally)
client = chromadb.PersistentClient(path=VECTOR_DB_PATH)

# Create or get collection
collection = client.get_or_create_collection(
    name="customer_collection"
)

print("ChromaDB connected successfully")
