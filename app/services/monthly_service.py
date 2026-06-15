from datetime import datetime
from app.db.sheets import sheets_client

# --- Monthly / Transaction Services ---

def get_monthly_customers(db=None):
    customers = [c for c in sheets_client.get_all_customers() if c.is_monthly]
    txns = sheets_client.get_all_transactions()
    for c in customers:
        c.transactions = [t for t in txns if t.customer_id == c.id]
    return customers

def get_customer_by_id(customer_id: str, db=None):
    return sheets_client.get_customer(customer_id)

def toggle_monthly_status_service(customer_id: str, is_monthly: bool, db=None):
    return sheets_client.update_customer(customer_id, {"is_monthly": is_monthly})

def create_transaction(data: dict, db=None):
    return sheets_client.add_transaction(data)

def get_transactions_by_customer(customer_id: str, db=None):
    return sheets_client.get_transactions_by_customer(customer_id)

def update_transaction(transaction_id: int, data: dict, db=None):
    return sheets_client.update_transaction(transaction_id, data)

def delete_transaction(transaction_id: int, db=None):
    return sheets_client.delete_transaction(transaction_id)

def calculate_totals(customer_id: str, db=None):
    transactions = get_transactions_by_customer(customer_id)
    total_jars = sum(t.jar_count for t in transactions)
    total_amount = sum(t.amount for t in transactions)
    return {"total_jars": total_jars, "total_amount": total_amount}


