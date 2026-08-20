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

app = FastAPI(title="Customer Vector API", version="1.0", lifespan=lifespan)

# Configure CORS Middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# include routes
app.include_router(customer_router)

@app.get("/")
async def root():
    return {"message": "Customer Vector API is running (Backend Only)"}


if __name__ == "__main__":
    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, reload=True)

