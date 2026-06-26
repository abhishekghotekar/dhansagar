import os
import json
import uuid
import threading
import time
from datetime import datetime
import gspread
from google.oauth2.service_account import Credentials
from app.core.config import (
    GOOGLE_SHEET_ID,
    GOOGLE_SERVICE_ACCOUNT_JSON,
    GOOGLE_SERVICE_ACCOUNT_DICT
)

# Lock for sheet operations to prevent race conditions during updates
sheets_lock = threading.Lock()

def calculate_new_balance(current_balance: float, jar_count: int, amount: float, is_monthly: bool, rate: float = 25.0) -> tuple[float, float]:
    """
    Calculates the new outstanding balance and the excess amount.
    Returns: (remaining_balance, excess_amount)
    """
    current_balance = float(current_balance)
    jar_count = int(jar_count)
    amount = float(amount)
    excess_amount = 0.0

    if is_monthly:
        if jar_count > 0:
            # Delivery: Amount is treated as cost (debit)
            cost = abs(amount)
            remaining_balance = current_balance + cost
        else:
            if amount > 0:
                # Payment (credit)
                if amount > current_balance:
                    excess_amount = amount - current_balance
                    remaining_balance = 0.0
                else:
                    remaining_balance = current_balance - amount
            else:
                # Cost adjustment
                cost = abs(amount)
                remaining_balance = current_balance + cost
    else:
        # Standard customer (pay-per-can)
        if jar_count > 0:
            # Jar delivery: Baki = prev_baki + (jars × rate) − payment
            cost = jar_count * rate
            if amount > 0:
                cost_minus_payment = cost - amount
                if current_balance + cost_minus_payment < 0:
                    excess_amount = abs(current_balance + cost_minus_payment)
                    remaining_balance = 0.0
                else:
                    remaining_balance = current_balance + cost_minus_payment
            else:
                remaining_balance = current_balance + cost
        else:
            # Payment-only entry (jar_count == 0)
            if amount > 0:
                if amount > current_balance:
                    excess_amount = amount - current_balance
                    remaining_balance = 0.0
                else:
                    remaining_balance = current_balance - amount
            else:
                # Cost adjustment
                cost = abs(amount)
                remaining_balance = current_balance + cost

    remaining_balance = max(0.0, remaining_balance)
    return remaining_balance, excess_amount

class CustomerObj:
    def __init__(self, **kwargs):
        self.id = kwargs.get("id", "")
        self.customer_name = kwargs.get("customer_name", "")
        self.customer_address = kwargs.get("customer_address", "")
        self.customer_number = kwargs.get("customer_number", "")
        self.jar = kwargs.get("jar", 0)
        self.paid = kwargs.get("paid", 0.0)
        self.pending = kwargs.get("pending", 0.0)
        self.success = kwargs.get("success", False)
        self.date = kwargs.get("date")
        self.is_monthly = kwargs.get("is_monthly", False)
        self.created_at = kwargs.get("created_at")
        self.updated_at = kwargs.get("updated_at")
        self.rate = float(kwargs.get("rate", 30.0))
        self.transactions = []

    def dict(self):
        formatted_txns = []
        for t in self.transactions:
            formatted_txns.append({
                "id": str(t.id),
                "customer_id": t.customer_id,
                "date": t.date.isoformat() if isinstance(t.date, datetime) else t.date,
                "jar": t.jar_count,
                "paid": t.amount,
                "pending": t.pending,
                "description": t.description or "",
                "created_at": t.created_at.isoformat() if isinstance(t.created_at, datetime) else (t.created_at or ""),
                "updated_at": t.updated_at.isoformat() if isinstance(t.updated_at, datetime) else (t.updated_at or "")
            })
        return {
            "id": self.id,
            "customer_name": self.customer_name,
            "customer_address": self.customer_address,
            "customer_number": self.customer_number,
            "jar": self.jar,
            "paid": self.paid,
            "pending": self.pending,
            "success": self.success,
            "date": self.date.isoformat() if isinstance(self.date, datetime) else self.date,
            "is_monthly": self.is_monthly,
            "created_at": self.created_at.isoformat() if isinstance(self.created_at, datetime) else self.created_at,
            "updated_at": self.updated_at.isoformat() if isinstance(self.updated_at, datetime) else self.updated_at,
            "rate": self.rate,
            "transactions": formatted_txns
        }



class TransactionObj:
    def __init__(self, **kwargs):
        self.id = kwargs.get("id", 0)
        self.customer_id = kwargs.get("customer_id", "")
        self.date = kwargs.get("date")
        self.jar_count = kwargs.get("jar_count", 0)
        self.amount = kwargs.get("amount", 0.0)
        self.pending = kwargs.get("pending", 0.0)
        self.description = kwargs.get("description", "")
        self.created_at = kwargs.get("created_at")
        self.updated_at = kwargs.get("updated_at")

    def dict(self):
        return {
            "id": self.id,
            "customer_id": self.customer_id,
            "date": self.date.isoformat() if isinstance(self.date, datetime) else self.date,
            "jar_count": self.jar_count,
            "amount": self.amount,
            "pending": self.pending,
            "description": self.description,
            "created_at": self.created_at.isoformat() if isinstance(self.created_at, datetime) else self.created_at,
            "updated_at": self.updated_at.isoformat() if isinstance(self.updated_at, datetime) else self.updated_at
        }


class GoogleSheetsClient:
    def __init__(self):
        self.client = None
        self.spreadsheet = None
        self.initialized = False
        
        # Lock for initialization and trackers
        self._init_lock = threading.Lock()
        self._last_init_attempt_time = 0.0
        self._last_init_error = None
        
        # Worksheet headers definition
        self.customer_headers = [
            "id", "customer_name", "customer_address", "customer_number", 
            "jar", "paid", "pending", "success", "date", "is_monthly", 
            "created_at", "updated_at", "rate"
        ]
        self.transaction_headers = [
            "id", "customer_id", "date", "jar_count", "amount", 
            "description", "created_at", "updated_at", "pending"
        ]

        # Cache for worksheets and data to prevent hitting Google Sheet API 429 Rate Limits
        self._worksheet_cache = {}        # maps customer_id -> Worksheet object
        self._customers_cache = None      # (timestamp, list of CustomerObj)
        self._transactions_cache = None   # (timestamp, list of TransactionObj)
        # Cache TTL = 30 seconds:
        # - Prevents 429 rate limit errors (much fewer API calls per minute)
        # - Both mobiles hit same server cache → same data
        # - Every write (add/update/delete) fully invalidates cache → next read is always fresh
        self._cache_duration = 30.0

    def _retry(self, func, *args, **kwargs):
        import requests
        import urllib3
        max_retries = 5
        delay = 1.0
        backoff_factor = 2.0
        
        for attempt in range(max_retries):
            try:
                return func(*args, **kwargs)
            except gspread.exceptions.APIError as e:
                status_code = getattr(e.response, "status_code", None)
                if status_code in [429, 500, 502, 503, 504]:
                    if attempt < max_retries - 1:
                        print(f"Google Sheets API error {status_code} on attempt {attempt+1}/{max_retries}. Retrying in {delay}s...")
                        time.sleep(delay)
                        delay *= backoff_factor
                        continue
                raise e
            except (requests.exceptions.RequestException, urllib3.exceptions.HTTPError) as e:
                if attempt < max_retries - 1:
                    print(f"Google Sheets connection/network error '{e}' on attempt {attempt+1}/{max_retries}. Retrying in {delay}s...")
                    time.sleep(delay)
                    delay *= backoff_factor
                    continue
                raise e

    def initialize(self):
        if self.initialized:
            return True

        with self._init_lock:
            if self.initialized:
                return True
                
            now = time.time()
            if now - self._last_init_attempt_time < 10.0:
                print(f"Skipping initialization retry: cooldown active (last attempt failed {(now - self._last_init_attempt_time):.1f}s ago with: {self._last_init_error})")
                return False

            self._last_init_attempt_time = now
            sheet_id = GOOGLE_SHEET_ID
            creds_json = GOOGLE_SERVICE_ACCOUNT_DICT
            creds_path = GOOGLE_SERVICE_ACCOUNT_JSON

            if not sheet_id:
                print("Google Sheets backend warning: GOOGLE_SHEET_ID is not configured in .env")
                return False

            scopes = [
                "https://www.googleapis.com/auth/spreadsheets",
                "https://www.googleapis.com/auth/drive"
            ]

            try:
                if creds_json:
                    info = json.loads(creds_json)
                    creds = Credentials.from_service_account_info(info, scopes=scopes)
                elif creds_path and os.path.exists(creds_path):
                    creds = Credentials.from_service_account_file(creds_path, scopes=scopes)
                else:
                    print(f"Google Sheets backend warning: Service account credentials not found. Checked GOOGLE_SERVICE_ACCOUNT_JSON={creds_path}")
                    return False

                self.client = self._retry(gspread.authorize, creds)
                self.spreadsheet = self._retry(self.client.open_by_key, sheet_id)
                self._ensure_worksheets()
                
                self.initialized = True
                self._last_init_error = None
                print("Google Sheets backend initialized successfully!")
                return True
            except Exception as e:
                self._last_init_error = f"{type(e).__name__}: {e}"
                print(f"Failed to initialize Google Sheets backend: {e}")
                return False

    def _invalidate_customers_cache(self):
        self._customers_cache = None

    def _invalidate_transactions_cache(self):
        self._transactions_cache = None

    def _ensure_worksheets(self):
        # 1. Ensure 'customers' worksheet
        try:
            self.customers_sheet = self._retry(self.spreadsheet.worksheet, "customers")
        except gspread.exceptions.WorksheetNotFound:
            self.customers_sheet = self._retry(self.spreadsheet.add_worksheet, "customers", rows="1000", cols="15")
            self._retry(self.customers_sheet.append_row, self.customer_headers)

        # 2. Ensure 'transactions' worksheet
        try:
            self.transactions_sheet = self._retry(self.spreadsheet.worksheet, "transactions")
            # Migration check: ensure all headers are present (specifically "pending" at the end)
            existing_headers = self._retry(self.transactions_sheet.row_values, 1)
            if existing_headers and len(existing_headers) < len(self.transaction_headers):
                self._retry(self.transactions_sheet.update, "A1:I1", [self.transaction_headers])
        except gspread.exceptions.WorksheetNotFound:
            self.transactions_sheet = self._retry(self.spreadsheet.add_worksheet, "transactions", rows="5000", cols="10")
            self._retry(self.transactions_sheet.append_row, self.transaction_headers)

    def _parse_datetime(self, val):
        if not val:
            return None
        if isinstance(val, datetime):
            return val
        try:
            return datetime.fromisoformat(val.replace("Z", "+00:00"))
        except ValueError:
            try:
                # Try common formats
                return datetime.strptime(val, "%Y-%m-%d %H:%M:%S")
            except ValueError:
                try:
                    return datetime.strptime(val, "%Y-%m-%d")
                except ValueError:
                    return None

    def _row_to_customer(self, row_dict):
        try:
            jar = int(row_dict.get("jar", 0))
        except:
            jar = 0
        try:
            paid = float(row_dict.get("paid", 0.0))
        except:
            paid = 0.0
        try:
            pending = float(row_dict.get("pending", 0.0))
        except:
            pending = 0.0

        try:
            rate = float(row_dict.get("rate", 30.0))
        except:
            rate = 30.0

        success = str(row_dict.get("success", "")).lower() == "true"
        is_monthly = str(row_dict.get("is_monthly", "")).lower() == "true"

        return CustomerObj(
            id=str(row_dict.get("id", "")),
            customer_name=str(row_dict.get("customer_name", "")),
            customer_address=str(row_dict.get("customer_address", "")),
            customer_number=str(row_dict.get("customer_number", "")),
            jar=jar,
            paid=paid,
            pending=pending,
            success=success,
            date=self._parse_datetime(row_dict.get("date")),
            is_monthly=is_monthly,
            created_at=self._parse_datetime(row_dict.get("created_at")),
            updated_at=self._parse_datetime(row_dict.get("updated_at")),
            rate=rate
        )

    def _row_to_transaction(self, row_dict):
        try:
            txn_id = int(row_dict.get("id", 0))
        except:
            txn_id = row_dict.get("id", 0)
        try:
            jar_count = int(row_dict.get("jar_count", 0))
        except:
            jar_count = 0
        try:
            amount = float(row_dict.get("amount", 0.0))
        except:
            amount = 0.0
        try:
            pending = float(row_dict.get("pending", 0.0))
        except:
            pending = 0.0

        return TransactionObj(
            id=txn_id,
            customer_id=str(row_dict.get("customer_id", "")),
            date=self._parse_datetime(row_dict.get("date")),
            jar_count=jar_count,
            amount=amount,
            pending=pending,
            description=str(row_dict.get("description", "")),
            created_at=self._parse_datetime(row_dict.get("created_at")),
            updated_at=self._parse_datetime(row_dict.get("updated_at"))
        )

    def _object_to_customer_row(self, obj: CustomerObj):
        row = []
        for h in self.customer_headers:
            val = getattr(obj, h, "")
            if isinstance(val, datetime):
                row.append(val.isoformat())
            elif isinstance(val, bool):
                row.append(str(val).upper())
            else:
                row.append(val)
        return row

    def _object_to_transaction_row(self, obj: TransactionObj):
        row = []
        for h in self.transaction_headers:
            val = getattr(obj, h, "")
            if isinstance(val, datetime):
                row.append(val.isoformat())
            elif isinstance(val, bool):
                row.append(str(val).upper())
            else:
                row.append(val)
        return row

    # --- CUSTOMER CRUD METHODS ---

    def get_all_customers(self, exclude_monthly=False, force_refresh=False):
        self.initialize()
        if not self.initialized:
            return []
            
        now_ts = time.time()
        with sheets_lock:
            if not force_refresh and self._customers_cache and (now_ts - self._customers_cache[0] < self._cache_duration):
                customers = self._customers_cache[1]
            else:
                # Use get_all_values() instead of get_all_records() because
                # get_all_records() silently STOPS at the first empty row,
                # causing customers added after an empty row to be invisible.
                all_values = self._retry(self.customers_sheet.get_all_values)
                customers = []
                if all_values:
                    headers = all_values[0]  # First row = headers
                    for row in all_values[1:]:  # Skip header row
                        if not any(row):  # Skip fully empty rows
                            continue
                        # Pad row to header length if needed
                        padded = row + [''] * (len(headers) - len(row))
                        row_dict = dict(zip(headers, padded))
                        if row_dict.get('id'):  # Only include rows with a valid ID
                            customers.append(self._row_to_customer(row_dict))
                self._customers_cache = (now_ts, customers)
            
        # Fetch and associate transactions for each customer
        txns = self.get_all_transactions(force_refresh=force_refresh)
        for c in customers:
            c.transactions = [t for t in txns if t.customer_id == c.id]

        if exclude_monthly:
            return [c for c in customers if not c.is_monthly]
        return list(customers)

    def get_customer(self, customer_id: str):
        customers = self.get_all_customers()
        for c in customers:
            if c.id == customer_id:
                return c
        return None

    def store_customer(self, data: dict):
        self.initialize()
        if not self.initialized:
            raise Exception("Google Sheets backend not initialized.")

        # Check duplicate phone number — raise error instead of silently updating
        cust_num = data.get("customer_number")
        if cust_num:
            existing = self.get_all_customers()
            for c in existing:
                if c.customer_number == cust_num:
                    raise Exception(f"Customer with phone number '{cust_num}' already exists (Name: {c.customer_name}). Use update instead.")

        doc_id = str(uuid.uuid4())
        now = datetime.now()

        # Handle Date Parsing
        date_val = data.get("date")
        if not date_val:
            parsed_date = now
        elif isinstance(date_val, datetime):
            parsed_date = date_val
        else:
            parsed_date = self._parse_datetime(date_val) or now

        customer = CustomerObj(
            id=doc_id,
            customer_name=data.get("customer_name", ""),
            customer_address=data.get("customer_address", ""),
            customer_number=data.get("customer_number", ""),
            jar=data.get("jar", 0),
            paid=data.get("paid", 0.0),
            pending=data.get("pending", 0.0),
            success=data.get("success", False),
            date=parsed_date,
            is_monthly=data.get("is_monthly", False),
            created_at=now,
            updated_at=now,
            rate=float(data.get("rate", 30.0))
        )

        row_values = self._object_to_customer_row(customer)
        with sheets_lock:
            # Use explicit row targeting instead of append_row to avoid
            # Google Sheets API "table detection" race condition where
            # multiple rapid writes overwrite each other on the same row.
            id_col = self._retry(self.customers_sheet.col_values, 1)
            # Find last row with actual data in column A
            last_data_row = 1
            for i, val in enumerate(id_col):
                if val and str(val).strip():
                    last_data_row = i + 1
            next_row = last_data_row + 1
            self._retry(
                self.customers_sheet.update,
                f"A{next_row}:M{next_row}",
                [row_values],
                value_input_option="USER_ENTERED"
            )
            # Full cache invalidation: ensures both mobiles get fresh data on next read
            self._customers_cache = None
            self._transactions_cache = None

        # Create initial transaction log if jar count or paid is recorded
        if customer.jar or customer.paid:
            threading.Thread(target=self.add_transaction, args=({
                "customer_id": doc_id,
                "date": parsed_date,
                "jar_count": customer.jar,
                "amount": customer.paid,
                "pending": customer.pending,
                "description": "Initial Record"
            },), daemon=True).start()
        else:
            threading.Thread(target=self.recalculate_customer_balances, args=(doc_id,), daemon=True).start()

        return doc_id

    def update_customer(self, customer_id: str, data: dict):
        self.initialize()
        if not self.initialized:
            return False

        customer = self.get_customer(customer_id)
        if not customer:
            return False

        with sheets_lock:
            id_list = self._retry(self.customers_sheet.col_values, 1)
            
        row_idx = -1
        for idx, cid in enumerate(id_list):
            if str(cid) == customer_id:
                row_idx = idx + 1
                break

        if row_idx == -1:
            return False
        
        # Track differences for Transactions
        old_jar = customer.jar
        old_paid = customer.paid
        now = datetime.now()

        # Update values
        if "customer_name" in data: customer.customer_name = data["customer_name"]
        if "customer_address" in data: customer.customer_address = data["customer_address"]
        if "customer_number" in data: customer.customer_number = data["customer_number"]
        if "jar" in data: customer.jar = int(data["jar"])
        if "paid" in data: customer.paid = float(data["paid"])
        if "pending" in data: customer.pending = float(data["pending"])
        if "success" in data: customer.success = str(data["success"]).lower() == "true"
        if "is_monthly" in data: customer.is_monthly = str(data["is_monthly"]).lower() == "true"
        if "rate" in data: customer.rate = float(data["rate"])
        
        if "date" in data and data["date"]:
            if isinstance(data["date"], datetime):
                customer.date = data["date"]
            else:
                customer.date = self._parse_datetime(data["date"]) or customer.date

        customer.updated_at = now

        row_values = self._object_to_customer_row(customer)
        
        with sheets_lock:
            self._retry(self.customers_sheet.update, f"A{row_idx}:M{row_idx}", [row_values])
            # Full cache invalidation on update: ensures fresh data on next read for both mobiles
            self._customers_cache = None
            self._transactions_cache = None

        # If transactions list is provided, synchronize it directly.
        # Otherwise, generate transaction log if differences exist.
        if "transactions" in data and data["transactions"] is not None:
            # Safely check if incoming transactions are actually different to prevent accidental wipe
            existing_txns = self.get_transactions_by_customer(customer_id)
            existing_simplified = []
            for t in existing_txns:
                existing_simplified.append({
                    "id": str(t.id),
                    "date": t.date.strftime("%Y-%m-%d") if t.date else "",
                    "jar": int(t.jar_count),
                    "paid": float(t.amount),
                    "pending": float(t.pending)
                })
            
            incoming_simplified = []
            for t in data["transactions"]:
                date_val = t.get("date")
                parsed_date = self._parse_datetime(date_val)
                date_str = parsed_date.strftime("%Y-%m-%d") if parsed_date else ""
                incoming_simplified.append({
                    "id": str(t.get("id", "")),
                    "date": date_str,
                    "jar": int(t.get("jar", 0)),
                    "paid": float(t.get("paid", 0.0)),
                    "pending": float(t.get("pending", 0.0))
                })
            
            # Sort both to ensure order doesn't fail comparison
            existing_simplified.sort(key=lambda x: (x["id"], x["date"]))
            incoming_simplified.sort(key=lambda x: (x["id"], x["date"]))
            
            if existing_simplified != incoming_simplified:
                threading.Thread(
                    target=self.sync_customer_transactions,
                    args=(customer_id, data["transactions"]),
                    daemon=True
                ).start()
            else:
                threading.Thread(target=self.sync_customer_sheet, args=(customer_id,), daemon=True).start()
        else:
            jar_diff = customer.jar - old_jar
            paid_diff = customer.paid - old_paid

            if jar_diff != 0 or abs(paid_diff) > 0.001:
                description = "Update"
                if jar_diff > 0: description = f"Added {jar_diff} jars"
                if paid_diff > 0: description = f"Paid {paid_diff}"
                if jar_diff > 0 and paid_diff > 0: description = f"Added {jar_diff} jars & Paid {paid_diff}"

                threading.Thread(target=self.add_transaction, args=({
                    "customer_id": customer_id,
                    "date": customer.date or now,
                    "jar_count": jar_diff,
                    "amount": paid_diff,
                    "pending": customer.pending,
                    "description": description
                },), daemon=True).start()
            else:
                threading.Thread(target=self.sync_customer_sheet, args=(customer_id,), daemon=True).start()

        return True

    def delete_customer(self, customer_id: str):
        self.initialize()
        if not self.initialized:
            return False

        customer = self.get_customer(customer_id)
        if customer:
            # Delete customer's individual sheet if it exists
            cust_sheet = self._find_customer_worksheet_by_id(customer_id, customer.customer_name)
            if cust_sheet:
                try:
                    with sheets_lock:
                        self._retry(self.spreadsheet.del_worksheet, cust_sheet)
                    if customer_id in self._worksheet_cache:
                        del self._worksheet_cache[customer_id]
                except Exception as e:
                    print(f"Error deleting customer worksheet: {e}")

        with sheets_lock:
            id_list = self._retry(self.customers_sheet.col_values, 1)
            
        row_idx = -1
        for idx, cid in enumerate(id_list):
            if str(cid) == customer_id:
                row_idx = idx + 1
                break

        if row_idx == -1:
            return False

        with sheets_lock:
            self._retry(self.customers_sheet.delete_rows, row_idx)
            # Full cache invalidation on delete: ensures fresh data on next read for both mobiles
            self._customers_cache = None
            self._transactions_cache = None

        # Cascading delete transactions (Run in background thread to avoid blocking request thread)
        threading.Thread(target=self.delete_transactions_by_customer, args=(customer_id,), daemon=True).start()
        return True

    def search_customers(self, query_str: str):
        if not query_str:
            return []
        
        customers = self.get_all_customers()
        query_lower = query_str.lower()
        results = []

        for c in customers:
            if (query_lower in c.customer_name.lower() or 
                query_lower in c.customer_address.lower() or 
                query_lower in c.customer_number.lower()):
                results.append(c)

        return results

    # --- TRANSACTION CRUD METHODS ---

    def get_all_transactions(self, force_refresh=False):
        self.initialize()
        if not self.initialized:
            return []

        now_ts = time.time()
        with sheets_lock:
            if not force_refresh and self._transactions_cache and (now_ts - self._transactions_cache[0] < self._cache_duration):
                transactions = self._transactions_cache[1]
            else:
                # Use get_all_values() instead of get_all_records() — same fix
                # as customers: avoids silent stop at empty rows.
                all_values = self._retry(self.transactions_sheet.get_all_values)
                transactions = []
                if all_values:
                    headers = all_values[0]
                    for row in all_values[1:]:
                        if not any(row):
                            continue
                        padded = row + [''] * (len(headers) - len(row))
                        row_dict = dict(zip(headers, padded))
                        if row_dict.get('id'):  # Only include rows with a valid ID
                            transactions.append(self._row_to_transaction(row_dict))
                self._transactions_cache = (now_ts, transactions)

        return list(transactions)

    def get_transactions_by_customer(self, customer_id: str, force_refresh=False):
        txns = self.get_all_transactions(force_refresh=force_refresh)
        filtered = [t for t in txns if t.customer_id == customer_id]
        # Sort descending by date
        filtered.sort(key=lambda x: x.date or datetime.min, reverse=True)
        return filtered

    def add_transaction(self, data: dict):
        self.initialize()
        if not self.initialized:
            raise Exception("Google Sheets backend not initialized.")

        txns = self.get_all_transactions()
        # Find next sequential ID
        max_id = 0
        for t in txns:
            if isinstance(t.id, int) and t.id > max_id:
                max_id = t.id
        next_id = max_id + 1

        now = datetime.now()
        date_val = data.get("date")
        if not date_val:
            parsed_date = now
        elif isinstance(date_val, datetime):
            parsed_date = date_val
        else:
            parsed_date = self._parse_datetime(date_val) or now

        customer_id = data.get("customer_id", "")
        
        # Calculate/retrieve pending value
        pending_val = data.get("pending")
        if pending_val is None:
            cust = self.get_customer(customer_id)
            if cust:
                if cust.is_monthly:
                    cust_txns = [t for t in txns if t.customer_id == customer_id]
                    total_cost = 0.0
                    total_paid = 0.0
                    for t in cust_txns:
                        val = t.amount
                        if t.jar_count > 0:
                            total_cost += abs(val)
                        else:
                            if val > 0:
                                total_paid += val
                            else:
                                total_cost += abs(val)
                    
                    new_jar = int(data.get("jar_count", 0))
                    new_amt = float(data.get("amount", 0.0))
                    if new_jar > 0:
                        total_cost += abs(new_amt)
                    else:
                        if new_amt > 0:
                            total_paid += new_amt
                        else:
                            total_cost += abs(new_amt)
                    pending_val = total_cost - total_paid
                else:
                    pending_val = cust.pending
            else:
                pending_val = 0.0
        else:
            try:
                pending_val = float(pending_val)
            except:
                pending_val = 0.0

        txn = TransactionObj(
            id=next_id,
            customer_id=customer_id,
            date=parsed_date,
            jar_count=int(data.get("jar_count", 0)),
            amount=float(data.get("amount", 0.0)),
            pending=pending_val,
            description=data.get("description", ""),
            created_at=now,
            updated_at=now
        )

        row_values = self._object_to_transaction_row(txn)
        with sheets_lock:
            # Use explicit row targeting (same fix as store_customer)
            id_col = self._retry(self.transactions_sheet.col_values, 1)
            last_data_row = 1
            for i, val in enumerate(id_col):
                if val and str(val).strip():
                    last_data_row = i + 1
            next_row = last_data_row + 1
            self._retry(
                self.transactions_sheet.update,
                f"A{next_row}:I{next_row}",
                [row_values],
                value_input_option="USER_ENTERED"
            )
            # Full cache invalidation on new transaction: ensures fresh data for both mobiles
            self._customers_cache = None
            self._transactions_cache = None

        # Recalculate customer balances and sync customer's sheet in background thread
        threading.Thread(target=self.recalculate_customer_balances, args=(customer_id,), daemon=True).start()

        return txn

    def update_transaction(self, txn_id: int, data: dict):
        self.initialize()
        if not self.initialized:
            return None

        # Find transaction in cached/fetched list
        txns = self.get_all_transactions()
        txn = None
        for t in txns:
            try:
                curr_id = int(t.id)
            except:
                curr_id = t.id
            if curr_id == txn_id:
                txn = t
                break

        if not txn:
            return None

        # Find row index using col_values(1)
        with sheets_lock:
            id_list = self._retry(self.transactions_sheet.col_values, 1)

        row_idx = -1
        for idx, tid in enumerate(id_list):
            try:
                curr_id = int(tid)
            except:
                curr_id = tid
            if curr_id == txn_id:
                row_idx = idx + 1
                break

        if row_idx == -1:
            return None

        now = datetime.now()
        if "date" in data and data["date"]:
            if isinstance(data["date"], datetime):
                txn.date = data["date"]
            else:
                txn.date = self._parse_datetime(data["date"]) or txn.date
        if "jar_count" in data: txn.jar_count = int(data["jar_count"])
        if "amount" in data: txn.amount = float(data["amount"])
        if "pending" in data: txn.pending = float(data["pending"])
        if "description" in data: txn.description = data["description"]
        txn.updated_at = now

        row_values = self._object_to_transaction_row(txn)
        with sheets_lock:
            self._retry(self.transactions_sheet.update, f"A{row_idx}:I{row_idx}", [row_values])
            # Full cache invalidation on transaction update: ensures fresh data for both mobiles
            self._customers_cache = None
            self._transactions_cache = None

        # Recalculate customer balances and sync customer's sheet
        self.recalculate_customer_balances(txn.customer_id)

        return txn

    def delete_transaction(self, txn_id: int):
        self.initialize()
        if not self.initialized:
            return False

        # Find transaction to get customer_id
        txns = self.get_all_transactions()
        txn = None
        for t in txns:
            try:
                curr_id = int(t.id)
            except:
                curr_id = t.id
            if curr_id == txn_id:
                txn = t
                break

        if not txn:
            return False

        customer_id = txn.customer_id

        # Find row index using col_values(1)
        with sheets_lock:
            id_list = self._retry(self.transactions_sheet.col_values, 1)

        row_idx = -1
        for idx, tid in enumerate(id_list):
            try:
                curr_id = int(tid)
            except:
                curr_id = tid
            if curr_id == txn_id:
                row_idx = idx + 1
                break

        if row_idx == -1:
            return False

        with sheets_lock:
            self._retry(self.transactions_sheet.delete_rows, row_idx)
            # Full cache invalidation on transaction delete: ensures fresh data for both mobiles
            self._customers_cache = None
            self._transactions_cache = None

        # Recalculate customer balances and sync customer's sheet
        if customer_id:
            self.recalculate_customer_balances(customer_id)

        return True

    def delete_transactions_by_customer(self, customer_id: str):
        self.initialize()
        if not self.initialized:
            return False

        # Find matching transactions rows using col_values(2) (customer_id column)
        with sheets_lock:
            cust_ids = self._retry(self.transactions_sheet.col_values, 2)

        rows_to_delete = []
        for idx, cid in enumerate(cust_ids):
            if str(cid) == customer_id:
                rows_to_delete.append(idx + 1)

        if not rows_to_delete:
            return True

        # Sort descending to delete from bottom up without shifting remaining targets
        rows_to_delete.sort(reverse=True)
        with sheets_lock:
            for r_idx in rows_to_delete:
                self._retry(self.transactions_sheet.delete_rows, r_idx)
            # Full cache invalidation: ensures fresh data for both mobiles
            self._customers_cache = None
            self._transactions_cache = None

        return True

    def sync_customer_transactions(self, customer_id: str, client_txns: list):
        self.initialize()
        if not self.initialized:
            return False

        now = datetime.now()
        
        # 1. Map client transactions to row values
        rows_to_add = []
        for t in client_txns:
            date_val = t.get("date")
            parsed_date = self._parse_datetime(date_val) or now
            
            txn = TransactionObj(
                id=t.get("id", ""),
                customer_id=customer_id,
                date=parsed_date,
                jar_count=int(t.get("jar", 0)),
                amount=float(t.get("paid", 0.0)),
                pending=float(t.get("pending", 0.0)),
                description="Delivery Log",
                created_at=now,
                updated_at=now
            )
            rows_to_add.append(self._object_to_transaction_row(txn))

        with sheets_lock:
            # 2. Find and delete all existing transactions for this customer
            cust_ids = self._retry(self.transactions_sheet.col_values, 2)
            rows_to_delete = []
            for idx, cid in enumerate(cust_ids):
                if str(cid) == customer_id:
                    rows_to_delete.append(idx + 1)
            
            if rows_to_delete:
                rows_to_delete.sort(reverse=True)
                for r in rows_to_delete:
                    self._retry(self.transactions_sheet.delete_rows, r)

            # 3. Append new transactions
            if rows_to_add:
                # Find last row with actual data in column A
                id_col = self._retry(self.transactions_sheet.col_values, 1)
                last_data_row = 1
                for i, val in enumerate(id_col):
                    if val and str(val).strip():
                        last_data_row = i + 1
                next_row = last_data_row + 1
                end_row = next_row + len(rows_to_add) - 1
                
                self._retry(
                    self.transactions_sheet.update,
                    f"A{next_row}:I{end_row}",
                    rows_to_add,
                    value_input_option="USER_ENTERED"
                )

        # 4. Invalidate cache so they are reloaded
        self._invalidate_transactions_cache()
        
        # 5. Recalculate customer balances and sync customer worksheet (ledger sheet)
        self.recalculate_customer_balances(customer_id)
        return True

    def _find_customer_worksheet_by_id(self, customer_id: str, name: str):
        self.initialize()
        if not self.initialized:
            return None
        
        # Check in-memory cache first
        if customer_id in self._worksheet_cache:
            sheet = self._worksheet_cache[customer_id]
            try:
                # Access title property (does not call API in gspread, it is just a field on Worksheet)
                _ = sheet.title
                return sheet
            except:
                del self._worksheet_cache[customer_id]
                
        # If cache miss, populate cache for all customer worksheets in bulk (only 2 API calls total)
        try:
            with sheets_lock:
                all_sheets = self._retry(self.spreadsheet.worksheets)
            
            sheet_names = [s.title for s in all_sheets if s.title not in ["customers", "transactions"]]
            
            if sheet_names:
                ranges = [f"'{s_name}'!A1" for s_name in sheet_names]
                with sheets_lock:
                    results = self._retry(self.spreadsheet.values_batch_get, ranges)
                value_ranges = results.get('valueRanges', [])
                
                # Map titles to worksheet objects
                title_to_sheet = {s.title: s for s in all_sheets}
                
                for s_name, item in zip(sheet_names, value_ranges):
                    values = item.get('values', [])
                    if values and values[0] and values[0][0]:
                        val = values[0][0]
                        if val.startswith("ID:"):
                            cid = val.split(":")[1].strip()
                            sheet_obj = title_to_sheet.get(s_name)
                            if sheet_obj:
                                self._worksheet_cache[cid] = sheet_obj
        except Exception as e:
            print(f"Error rebuilding worksheet cache: {e}")
            
        # Re-check cache after refresh
        if customer_id in self._worksheet_cache:
            return self._worksheet_cache[customer_id]
            
        return None

    def _get_unique_sheet_title(self, desired_name: str, customer_id: str):
        sanitized_name = desired_name
        for c in [':', '\\', '/', '?', '*', '[', ']']:
            sanitized_name = sanitized_name.replace(c, '')
        sanitized_name = sanitized_name[:90]
        
        existing = self._find_customer_worksheet_by_id(customer_id, desired_name)
        if existing:
            return existing.title
            
        title = sanitized_name
        counter = 1
        
        try:
            with sheets_lock:
                all_sheets = self._retry(self.spreadsheet.worksheets)
            existing_titles = {s.title for s in all_sheets}
        except Exception as e:
            print(f"Error fetching sheets for unique title: {e}")
            existing_titles = set()
            
        while title in existing_titles:
            counter += 1
            title = f"{sanitized_name} ({counter})"
        return title

    def recalculate_customer_balances(self, customer_id: str):
        """
        Recalculates the running outstanding balances for all transactions of a customer
        chronologically, updates the transaction records in 'transactions' sheet, and
        stores the final outstanding balance in the 'customers' sheet.
        """
        self.initialize()
        if not self.initialized:
            return

        customer = self.get_customer(customer_id)
        if not customer:
            return

        txns = self.get_transactions_by_customer(customer_id, force_refresh=True)
        txns.sort(key=lambda x: x.date or datetime.min)

        running_balance = 0.0
        now = datetime.now()

        with sheets_lock:
            id_list = self._retry(self.transactions_sheet.col_values, 1)

        id_to_row = {}
        for idx, tid in enumerate(id_list):
            if tid:
                id_to_row[str(tid)] = idx + 1

        total_jars = 0
        total_paid = 0.0

        for t in txns:
            jar_count = int(t.jar_count)
            amount = float(t.amount)

            running_balance, excess = calculate_new_balance(
                running_balance, jar_count, amount, customer.is_monthly, customer.rate
            )

            if excess > 0.0:
                # Append excess info safely
                base_desc = t.description or "Payment received"
                if "Excess:" not in base_desc:
                    t.description = f"{base_desc} (Excess: ₹{excess:.2f})"

            t.pending = running_balance
            t.updated_at = now

            # Accumulate totals
            total_jars += jar_count
            if customer.is_monthly:
                if jar_count == 0 and amount > 0:
                    total_paid += amount
            else:
                if amount > 0:
                    total_paid += amount

            row_idx = id_to_row.get(str(t.id))
            if row_idx:
                row_values = self._object_to_transaction_row(t)
                with sheets_lock:
                    self._retry(self.transactions_sheet.update, f"A{row_idx}:I{row_idx}", [row_values])

        customer.jar = total_jars
        customer.paid = total_paid
        customer.pending = running_balance
        customer.updated_at = now

        with sheets_lock:
            cust_id_list = self._retry(self.customers_sheet.col_values, 1)
        
        cust_row_idx = -1
        for idx, cid in enumerate(cust_id_list):
            if str(cid) == customer_id:
                cust_row_idx = idx + 1
                break

        if cust_row_idx != -1:
            cust_row_values = self._object_to_customer_row(customer)
            with sheets_lock:
                self._retry(self.customers_sheet.update, f"A{cust_row_idx}:M{cust_row_idx}", [cust_row_values])

        self._customers_cache = None
        self._transactions_cache = None

        self.sync_customer_sheet(customer_id)

    def sync_customer_sheet(self, customer_id: str):
        self.initialize()
        if not self.initialized:
            return False
            
        customer = self.get_customer(customer_id)
        if not customer:
            return False
            
        sheet = self._find_customer_worksheet_by_id(customer_id, customer.customer_name)
        new_title = self._get_unique_sheet_title(customer.customer_name, customer_id)
        
        with sheets_lock:
            if not sheet:
                try:
                    sheet = self._retry(self.spreadsheet.add_worksheet, new_title, rows="1000", cols="5")
                    self._worksheet_cache[customer_id] = sheet
                except Exception as e:
                    print(f"Error creating customer worksheet {new_title}: {e}")
                    return False
            elif sheet.title != new_title:
                try:
                    self._retry(sheet.update_title, new_title)
                    self._worksheet_cache[customer_id] = sheet
                except Exception as e:
                    print(f"Error updating customer worksheet title: {e}")
                
        txns = self.get_transactions_by_customer(customer_id)
        txns.sort(key=lambda x: x.date or datetime.min)
        
        rows = [
            [f"ID: {customer_id}", "", "", "", ""],
            [f"Customer Name: {customer.customer_name}", f"Mobile: {customer.customer_number or ''}", "", "", ""],
            ["", "", "", "", ""],
            ["Date", "Jar", "Pending Amount", "Paid Amount", "Description"]
        ]
        
        is_monthly = customer.is_monthly
        running_pending = 0.0
        
        for t in txns:
            date_str = t.date.strftime("%Y-%m-%d") if t.date else ""
            jar = t.jar_count
            desc = t.description or ""
            
            if is_monthly:
                cost = 0.0
                payment = 0.0
                if jar > 0:
                    cost = abs(t.amount)
                    payment = 0.0
                else:
                    if t.amount > 0:
                        payment = t.amount
                        cost = 0.0
                    else:
                        cost = abs(t.amount)
                        payment = 0.0
                
                running_pending = running_pending + cost - payment
                paid_amount = payment
                pending_amount = running_pending
            else:
                paid_amount = t.amount
                pending_amount = t.pending
            
            rows.append([
                date_str,
                str(jar),
                f"{pending_amount:.2f}" if isinstance(pending_amount, (int, float)) else str(pending_amount),
                f"{paid_amount:.2f}" if isinstance(paid_amount, (int, float)) else str(paid_amount),
                desc
            ])
            
        with sheets_lock:
            try:
                self._retry(sheet.clear)
                self._retry(sheet.update, "A1", rows)
                
                # Batch all format calls into a single API request to reduce rate limit hits
                try:
                    self._retry(sheet.spreadsheet.batch_update, {
                        "requests": [
                            {
                                "repeatCell": {
                                    "range": {
                                        "sheetId": sheet.id,
                                        "startRowIndex": 3, "endRowIndex": 4,
                                        "startColumnIndex": 0, "endColumnIndex": 5
                                    },
                                    "cell": {
                                        "userEnteredFormat": {
                                            "backgroundColor": {"red": 0.082, "green": 0.133, "blue": 0.22},
                                            "textFormat": {
                                                "foregroundColor": {"red": 1.0, "green": 1.0, "blue": 1.0},
                                                "fontSize": 11,
                                                "bold": True
                                            },
                                            "horizontalAlignment": "CENTER"
                                        }
                                    },
                                    "fields": "userEnteredFormat(backgroundColor,textFormat,horizontalAlignment)"
                                }
                            },
                            {
                                "repeatCell": {
                                    "range": {
                                        "sheetId": sheet.id,
                                        "startRowIndex": 1, "endRowIndex": 2,
                                        "startColumnIndex": 0, "endColumnIndex": 2
                                    },
                                    "cell": {
                                        "userEnteredFormat": {
                                            "textFormat": {"fontSize": 12, "bold": True}
                                        }
                                    },
                                    "fields": "userEnteredFormat.textFormat"
                                }
                            },
                            {
                                "repeatCell": {
                                    "range": {
                                        "sheetId": sheet.id,
                                        "startRowIndex": 0, "endRowIndex": 1,
                                        "startColumnIndex": 0, "endColumnIndex": 1
                                    },
                                    "cell": {
                                        "userEnteredFormat": {
                                            "textFormat": {
                                                "foregroundColor": {"red": 0.6, "green": 0.6, "blue": 0.6},
                                                "fontSize": 8
                                            }
                                        }
                                    },
                                    "fields": "userEnteredFormat.textFormat"
                                }
                            },
                            {
                                "autoResizeDimensions": {
                                    "dimensions": {
                                        "sheetId": sheet.id,
                                        "dimension": "COLUMNS",
                                        "startIndex": 0,
                                        "endIndex": 5
                                    }
                                }
                            }
                        ]
                    })
                except Exception as ex:
                    print(f"Error applying batch formatting: {ex}")
            except Exception as e:
                print(f"Error updating or formatting customer worksheet: {e}")
                return False
                
        return True


# Instantiate singleton Sheets client
sheets_client = GoogleSheetsClient()
