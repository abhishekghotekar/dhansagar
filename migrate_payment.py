import sys
import os

# Add current directory to sys.path to ensure 'app' module can be found
sys.path.append(os.getcwd())

from sqlalchemy import text
from app.db.postgres import engine

def migrate_schema():
    print("Migrating schema to add 'type' to transactions...")
    
    with engine.connect() as connection:
        try:
            # Add 'is_payment' column
            # Default to TRUE because previous logic assumed 'amount' was Payment (Paid)
            # Or wait, 'amount' in 'store_customer' came from 'paid'. 
            # So yes, existing data is Payments.
            connection.execute(text("ALTER TABLE transactions ADD COLUMN IF NOT EXISTS is_payment BOOLEAN DEFAULT TRUE"))
            
            # We also might need 'cost' instead of 'amount' if we want to store both in one row?
            # No, let's keep it simple: One transaction is one event.
            # Either a "Delivery" (Jars + Cost) OR "Payment" (Amount).
            # If Delivery: jar_count > 0, amount = Cost, is_payment = False.
            # If Payment: jar_count = 0, amount = Paid, is_payment = True.
            
            print("Migration successful: Added is_payment to transactions.")
            connection.commit()
            
        except Exception as e:
            print(f"Migration failed: {e}")
            
if __name__ == "__main__":
    migrate_schema()
