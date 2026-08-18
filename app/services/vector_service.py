from datetime import datetime
from app.db.sheets import sheets_client


# Helper to parse date
def parse_date(date_val):
    if not date_val:
        return datetime.now()
    if isinstance(date_val, datetime):
        return date_val
    try:
        return datetime.strptime(date_val, "%Y-%m-%d")
    except ValueError:
        try:
            return datetime.fromisoformat(date_val)
        except:
            return datetime.now()

# 🔵 Store customer in Google Sheets
def store_customer(data: dict, db=None):
    return sheets_client.store_customer(data)


# ✏️ Update customer in Google Sheets
def update_customer(doc_id: str, data: dict, db=None):
    return sheets_client.update_customer(doc_id, data)


def safe_iso(val):
    if not val:
        return None
    if isinstance(val, datetime):
        return val.isoformat()
    return str(val)

# 🔍 Search customer in Google Sheets
def search_customer(query: str, db=None):
    res = sheets_client.search_customers(query)
    formatted_results = []
    for customer in res:
        formatted_txns = []
        for t in customer.transactions:
            formatted_txns.append({
                "id": str(t.id),
                "date": safe_iso(t.date) or "",
                "jar": t.jar_count,
                "paid": t.amount,
                "pending": t.pending
            })
        item = {
            "id": customer.id,
            "customer_name": customer.customer_name,
            "customer_address": customer.customer_address,
            "customer_number": customer.customer_number,
            "jar": customer.jar,
            "paid": customer.paid,
            "pending": customer.pending,
            "success": customer.success,
            "date": safe_iso(customer.date),
            "is_monthly": customer.is_monthly,
            "created_at": safe_iso(customer.created_at),
            "updated_at": safe_iso(customer.updated_at),
            "transactions": formatted_txns,
            "distance": 0.0 # Mock distance
        }
        item['document'] = f"{customer.customer_name} | {customer.customer_address} | {customer.customer_number}"
        formatted_results.append(item)
    return formatted_results


def get_all_customers(db=None, exclude_monthly: bool = False, force_refresh: bool = False):
    customers = sheets_client.get_all_customers(exclude_monthly=exclude_monthly, force_refresh=force_refresh)
    try:
        customers.sort(key=lambda x: x.updated_at or x.created_at or datetime.min, reverse=True)
    except:
        pass
    return customers


# ❌ Delete customer from Google Sheets
def delete_customer(doc_id: str, db=None):
    return sheets_client.delete_customer(doc_id)


