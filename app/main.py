import os
import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from app.routes.customer_routes import router
from app.core.config import USE_GOOGLE_SHEETS
from app.db.sheets import sheets_client

# Initialize Google Sheets
if USE_GOOGLE_SHEETS:
    print("Google Sheets backend is active. Initializing worksheets and headers...")
    sheets_client.initialize()

app = FastAPI(title="Customer Vector API", version="1.0")

# include routes
app.include_router(router)
from app.routes.monthly_routes import router as monthly_router
app.include_router(monthly_router)

@app.get("/")
async def root():
    return {"message": "Customer Vector API is running (Backend Only)"}


if __name__ == "__main__":
    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, reload=True)

