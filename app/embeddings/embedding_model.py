from sentence_transformers import SentenceTransformer
from app.core.config import EMBEDDING_MODEL

# load embedding model once
model = SentenceTransformer(EMBEDDING_MODEL)

print("Embedding model loaded")

# function to create embedding
def create_embedding(text: str):
    embedding = model.encode(text)
    return embedding.tolist()
