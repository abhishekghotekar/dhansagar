import os
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from sqlalchemy import text
from app.routes.customer_routes import router
from app.db.postgres import engine, Base
from app.db.models import CustomerModel, TransactionModel

# Create DB tables
Base.metadata.create_all(bind=engine)

# 🔄 Auto-Migration: Add missing columns if they don't exist
try:
    with engine.connect() as connection:
        connection.execute(text("ALTER TABLE customers ADD COLUMN IF NOT EXISTS created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW();"))
        connection.execute(text("ALTER TABLE customers ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW();"))
        # Add 'pending' column if missing
        connection.execute(text("ALTER TABLE customers ADD COLUMN IF NOT EXISTS pending FLOAT DEFAULT 0.0;"))
        # Add 'date' column if missing
        connection.execute(text("ALTER TABLE customers ADD COLUMN IF NOT EXISTS date TIMESTAMP WITH TIME ZONE;"))
        # Add 'is_monthly' column if missing
        connection.execute(text("ALTER TABLE customers ADD COLUMN IF NOT EXISTS is_monthly BOOLEAN DEFAULT FALSE;"))
        
        # We can also create transactions table if not exists via SQL, but create_all above should handle new tables.
        
        connection.commit()
        print("Schema migration checked/applied successfully.")
except Exception as e:
    print(f"Schema migration warning: {e}")

app = FastAPI(title="Customer Vector API", version="1.0")

# Get absolute path to the directory containing main.py
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")

# Mount static files
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

# include routes
app.include_router(router)
from app.routes.monthly_routes import router as monthly_router
app.include_router(monthly_router)

@app.get("/")
async def read_index():
    return FileResponse(os.path.join(STATIC_DIR, 'index.html'))
