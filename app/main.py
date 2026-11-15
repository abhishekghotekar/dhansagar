import os
import uvicorn
from fastapi import FastAPI
from app.routes.customer_routes import router as customer_router
from app.core.config import USE_GOOGLE_SHEETS
from app.db.sheets import sheets_client

from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    if USE_GOOGLE_SHEETS:
        print("Google Sheets backend is active. Initializing worksheets and headers...")
        await sheets_client.initialize()
    yield

app = FastAPI(title="Dhansagar Customer Management API", version="1.0", lifespan=lifespan)

# Configure CORS Middleware (Compatible with wildcards)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routes
app.include_router(customer_router)

@app.get("/")
async def root():
    return {"message": "Dhansagar Customer Management API is running successfully"}


if __name__ == "__main__":
    port = int(os.getenv("PORT", 8000))
    uvicorn.run("app.main:app", host="0.0.0.0", port=port, reload=True)
