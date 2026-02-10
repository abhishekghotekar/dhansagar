from sqlalchemy import create_engine, text
from app.db.postgres import engine

# Add columns to 'transactions' table to support Detailed Ledger
# We need:
# - bill_amount (Float): Cost of goods/services (Debit)
# - payment_amount (Float): Amount paid (Credit)
# Currently we have 'amount'. We will migrate data:
# IF amount > 0 AND jar_count == 0 -> payment_amount = amount.
# IF amount > 0 AND jar_count > 0 -> bill_amount = amount? Or mixed?
# To succeed request "proper pending amount", we need clear Debit/Credit.

# Plan:
# 1. Add 'bill_amount' column (default 0).
# 2. Add 'payment_amount' column (default 0).
# 3. Rename/Deprecate 'amount' or just migrate data from 'amount' to 'payment_amount' (since user said POSITIVE is payment).
# 4. If 'amount' was used for Payment, we move it to 'payment_amount'.
# 5. We keep 'amount' for backward compatibility or drop it? Let's just Add columns for now and update code to use them.
# Actually, let's just ADD 'bill_amount'. 
# 'amount' stays as 'payment_amount' (money received).
# 'jar_count' stays.
# 'bill_amount' tracks the Money Value of the Jars (Cost). 

def migrate_ledger():
    print("Migrating schema to add ledger columns...")
    
    with engine.connect() as connection:
        try:
            # 1. Add bill_amount
            connection.execute(text("ALTER TABLE transactions ADD COLUMN IF NOT EXISTS bill_amount FLOAT DEFAULT 0.0"))
            
            # 2. Add payment_amount (We can just use 'amount', but 'payment_amount' is clearer. Let's alias or just use 'amount' as payment for now to avoid breaking changes, BUT user wants "proper pending".
            # If I stick to 'amount' = Payment, I just need 'bill_amount'.
            # Pending = Sum(bill_amount) - Sum(amount).
            # Perfect.
            
            # Additional: User wants "proper pending amount". 
            # Does CustomerModel have 'rate'? No.
            # I will add 'rate' to CustomerModel so we can auto-calculate bill_amount.
            connection.execute(text("ALTER TABLE customers ADD COLUMN IF NOT EXISTS default_rate FLOAT DEFAULT 0.0"))
            
            print("Migration successful: Added bill_amount to transactions and default_rate to customers.")
            connection.commit()
            
        except Exception as e:
            print(f"Migration failed: {e}")
            
if __name__ == "__main__":
    migrate_ledger()
