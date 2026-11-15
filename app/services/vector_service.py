from datetime import datetime
from app.db.sheets import sheets_client


def safe_iso(val):
    if not val:
        return None
    if isinstance(val, datetime):
        return val.isoformat()
    return str(val)

def safe_date_str(val, fmt="%Y-%m-%d"):
    if not val:
        return ""
    if isinstance(val, datetime):
        return val.strftime(fmt)
    return str(val)

# 🔵 Store customer in Google Sheets
async def store_customer(data: dict):
    return await sheets_client.store_customer(data)

# ✏️ Update customer in Google Sheets
async def update_customer(doc_id: str, data: dict):
    return await sheets_client.update_customer(doc_id, data)

# 🔍 Search customer in Google Sheets
async def search_customer(query: str):
    res = await sheets_client.search_customers(query)
    formatted_results = []
    for customer in res:
        formatted_txns = []
        for t in customer.transactions:
            formatted_txns.append({
                "id": str(t.id),
                "date": safe_date_str(t.date) or "",
                "jar": t.jar_count,
                "paid": t.amount,
                "jama": t.amount,
                "pending": t.manual_bill,
                "baki": t.manual_bill,
                "running_balance": t.pending
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
            "date": safe_date_str(customer.date),
            "is_monthly": customer.is_monthly,
            "created_at": safe_iso(customer.created_at),
            "updated_at": safe_iso(customer.updated_at),
            "transactions": formatted_txns,
            "document": f"{customer.customer_name} | {customer.customer_address} | {customer.customer_number}"
        }
        formatted_results.append(item)
    return formatted_results

async def get_all_customers(exclude_monthly: bool = False, force_refresh: bool = False):
    customers = await sheets_client.get_all_customers(exclude_monthly=exclude_monthly, force_refresh=force_refresh)
    def sort_key(x):
        dt = x.updated_at or x.created_at
        return dt if isinstance(dt, datetime) else datetime.min
    customers.sort(key=sort_key, reverse=True)
    return customers

# ❌ Delete customer from Google Sheets
async def delete_customer(doc_id: str):
    return await sheets_client.delete_customer(doc_id)
