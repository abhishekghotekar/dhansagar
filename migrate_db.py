from sqlalchemy import text
from app.db.postgres import engine

def migrate_schema():
    print("Migrating schema to add missing columns...")
    
    with engine.connect() as connection:
        # Add created_at if not exists (using manual check/try-except logic or IF NOT EXISTS syntax if supported by postgres)
        # Postgres supports IF NOT EXISTS for ADD COLUMN in newer versions, but let's be safe with a try/catch or simple raw sql
        
        try:
            # We use text() for raw SQL execution
            alter_query_created = text("ALTER TABLE customers ADD COLUMN IF NOT EXISTS created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW();")
            connection.execute(alter_query_created)
            print("Added created_at column.")
        except Exception as e:
            print(f"Error adding created_at: {e}")

        try:
            alter_query_updated = text("ALTER TABLE customers ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW();")
            connection.execute(alter_query_updated)
            print("Added updated_at column.")
        except Exception as e:
            print(f"Error adding updated_at: {e}")

        try:
            # Check/Add 'pending' column
            alter_query_pending = text("ALTER TABLE customers ADD COLUMN IF NOT EXISTS pending FLOAT DEFAULT 0.0;")
            connection.execute(alter_query_pending)
            print("Added pending column.")
        except Exception as e:
            print(f"Error adding pending column: {e}")
            
        connection.commit()
    
    print("Schema migration completed.")

if __name__ == "__main__":
    migrate_schema()
