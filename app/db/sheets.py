import os
import json
import uuid
import threading
import time
from datetime import datetime, timezone, timedelta
import gspread
import asyncio
from google.oauth2.service_account import Credentials
from app.core.config import (
    GOOGLE_SHEET_ID,
    GOOGLE_SERVICE_ACCOUNT_JSON,
    GOOGLE_SERVICE_ACCOUNT_DICT
)

# Lock for sheet operations to prevent race conditions during updates
sheets_lock = asyncio.Lock()

def calculate_new_balance(current_balance: float, jar_count: int, amount: float, is_monthly: bool = False, rate: float = 25.0) -> tuple[float, float]:
    """
    Calculates the new outstanding balance and the excess amount.
    Returns: (remaining_balance, excess_amount)
    """
    current_balance = float(current_balance)
    jar_count = int(jar_count)
    amount = float(amount)
    excess_amount = 0.0

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
        self.rate = float(kwargs.get("rate", 25.0))
        self.row_idx = kwargs.get("row_idx", None)
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
                "pending": t.manual_bill,
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
        self.id = str(kwargs.get("id", ""))
        self.customer_id = kwargs.get("customer_id", "")
        self.date = kwargs.get("date")
        self.jar_count = kwargs.get("jar_count", 0)
        self.amount = kwargs.get("amount", 0.0)
        self.pending = kwargs.get("pending", 0.0)
        self.manual_bill = kwargs.get("manual_bill", 0.0)
        self.description = kwargs.get("description", "")
        self.created_at = kwargs.get("created_at")
        self.updated_at = kwargs.get("updated_at")
        self.row_idx = kwargs.get("row_idx", None)

    def dict(self):
        return {
            "id": self.id,
            "customer_id": self.customer_id,
            "date": self.date.isoformat() if isinstance(self.date, datetime) else self.date,
            "jar_count": self.jar_count,
            "amount": self.amount,
            "pending": self.pending,
            "manual_bill": self.manual_bill,
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
            "description", "created_at", "updated_at", "pending", "manual_bill"
        ]

        # Cache for worksheets and data to prevent hitting Google Sheet API 429 Rate Limits
        self._worksheet_cache = {}        # maps customer_id -> Worksheet object
        self._customers_cache = None      # (timestamp, list of CustomerObj)
        self._transactions_cache = None   # (timestamp, list of TransactionObj)
        self._worksheets_list_cache = None # (timestamp, list of Worksheet objects)
        # Cache TTL = 30 seconds:
        # - Prevents 429 rate limit errors (much fewer API calls per minute)
        # - Both mobiles hit same server cache → same data
        # - Every write (add/update/delete) fully invalidates cache → next read is always fresh
        self._cache_duration = 30.0

    async def _retry(self, func, *args, **kwargs):
        import requests
        import urllib3
        max_retries = 5
        delay = 1.0
        backoff_factor = 2.0
        
        for attempt in range(max_retries):
            try:
                # Execute the synchronous gspread call in a worker thread
                return await asyncio.to_thread(func, *args, **kwargs)
            except Exception as e:
                status_code = getattr(getattr(e, "response", None), "status_code", None)
                if status_code in [429, 500, 502, 503, 504] or "APIError" in str(type(e)):
                    if attempt < max_retries - 1:
                        print(f"Google Sheets API error {status_code} on attempt {attempt+1}/{max_retries}. Retrying in {delay}s...")
                        await asyncio.sleep(delay)
                        delay *= backoff_factor
                        continue
                elif "RequestException" in str(type(e)) or "HTTPError" in str(type(e)) or "Timeout" in str(type(e)):
                    if attempt < max_retries - 1:
                        print(f"Google Sheets connection/network error '{e}' on attempt {attempt+1}/{max_retries}. Retrying in {delay}s...")
                        await asyncio.sleep(delay)
                        delay *= backoff_factor
                        continue
                raise e

    async def initialize(self):
        if self.initialized:
            return True

        # Async lock for init
        if not hasattr(self, '_async_init_lock'):
            self._async_init_lock = asyncio.Lock()

        async with self._async_init_lock:
            if self.initialized:
                return True
                
            now = time.time()
            if now - self._last_init_attempt_time < 10.0:
                return False

            self._last_init_attempt_time = now
            sheet_id = GOOGLE_SHEET_ID
            creds_json = GOOGLE_SERVICE_ACCOUNT_DICT
            creds_path = GOOGLE_SERVICE_ACCOUNT_JSON

            if not sheet_id:
                return False

            scopes = [
                "https://www.googleapis.com/auth/spreadsheets",
                "https://www.googleapis.com/auth/drive"
            ]

            try:
                def get_creds():
                    if creds_json:
                        info = json.loads(creds_json)
                        return Credentials.from_service_account_info(info, scopes=scopes)
                    elif creds_path and os.path.exists(creds_path):
                        return Credentials.from_service_account_file(creds_path, scopes=scopes)
                    else:
                        raise Exception("Credentials not found")

                creds = get_creds()
                self.client = await asyncio.to_thread(gspread.authorize, creds)
                self.spreadsheet = await self._retry(self.client.open_by_key, sheet_id)
                await self._ensure_worksheets()
                
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

    async def _get_worksheets_cached(self, force_refresh=False):
        now = time.time()
        if not force_refresh and self._worksheets_list_cache and (now - self._worksheets_list_cache[0] < 300.0):
            return self._worksheets_list_cache[1]
        async with sheets_lock:
            all_sheets = await self._retry(self.spreadsheet.worksheets)
        self._worksheets_list_cache = (now, all_sheets)
        return all_sheets

    def _invalidate_worksheets_cache(self):
        self._worksheets_list_cache = None

    async def _ensure_worksheets(self):
        # 1. Ensure 'customers' worksheet
        try:
            self.customers_sheet = await self._retry(self.spreadsheet.worksheet, "customers")
        except gspread.exceptions.WorksheetNotFound:
            self.customers_sheet = await self._retry(self.spreadsheet.add_worksheet, "customers", rows="1000", cols="15")
            await self._retry(self.customers_sheet.append_row, self.customer_headers)
            self._invalidate_worksheets_cache()

        # 2. Ensure 'transactions' worksheet
        try:
            self.transactions_sheet = await self._retry(self.spreadsheet.worksheet, "transactions")
            # Migration check: ensure all headers are present (specifically "pending" at the end)
            existing_headers = await self._retry(self.transactions_sheet.row_values, 1)
            if existing_headers and len(existing_headers) < len(self.transaction_headers):
                await self._retry(self.transactions_sheet.update, "A1:J1", [self.transaction_headers])
        except gspread.exceptions.WorksheetNotFound:
            self.transactions_sheet = await self._retry(self.spreadsheet.add_worksheet, "transactions", rows="5000", cols="10")
            await self._retry(self.transactions_sheet.append_row, self.transaction_headers)
            self._invalidate_worksheets_cache()

    def _parse_datetime(self, val):
        if not val:
            return None
        if isinstance(val, datetime):
            return val
        
        val_str = str(val).strip()
        try:
            return datetime.fromisoformat(val_str.replace("Z", "+00:00"))
        except ValueError:
            pass
            
        for fmt in (
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d",
            "%Y/%m/%d",
            "%d-%m-%Y",
            "%d/%m/%Y",
            "%d-%m-%Y %H:%M:%S",
            "%d/%m/%Y %H:%M:%S"
        ):
            try:
                return datetime.strptime(val_str, fmt)
            except ValueError:
                continue
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
            rate = float(row_dict.get("rate", 25.0))
        except:
            rate = 25.0

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
        txn_id = str(row_dict.get("id", ""))
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
        try:
            manual_bill = float(row_dict.get("manual_bill", 0.0))
        except:
            manual_bill = 0.0

        desc = str(row_dict.get("description", ""))
        if desc in ["Initial Record", "Opening Balance"] and manual_bill == 0.0 and pending > 0.0:
            manual_bill = pending

        return TransactionObj(
            id=txn_id,
            customer_id=str(row_dict.get("customer_id", "")),
            date=self._parse_datetime(row_dict.get("date")),
            jar_count=jar_count,
            amount=amount,
            pending=pending,
            manual_bill=manual_bill,
            description=desc,
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

    async def get_all_customers(self, exclude_monthly=False, force_refresh=False):
        await self.initialize()
        if not self.initialized:
            return []
            
        now_ts = time.time()
        # Granular locking: only lock the cache check and Google Sheets fetch
        async with sheets_lock:
            if not force_refresh and self._customers_cache and (now_ts - self._customers_cache[0] < self._cache_duration):
                return list(self._customers_cache[1])
            else:
                all_values = await self._retry(self.customers_sheet.get_all_values)
        
        # Release lock before parsing data
        customers = []
        if all_values:
            headers = self.customer_headers # Always enforce our canonical headers
                
            for idx, row in enumerate(all_values[1:]):  # Skip header row
                if not any(row):  # Skip fully empty rows
                    continue
                # Pad row to header length if needed
                padded = row + [''] * (len(headers) - len(row))
                row_dict = dict(zip(headers, padded))
                if row_dict.get('id'):  # Only include rows with a valid ID
                    cust = self._row_to_customer(row_dict)
                    cust.row_idx = idx + 2
                    customers.append(cust)
                    
        async with sheets_lock:
            self._customers_cache = (now_ts, customers)
            
        # Fetch and associate transactions for each customer
        txns = await self.get_all_transactions(force_refresh=force_refresh)
        for c in customers:
            c.transactions = [t for t in txns if t.customer_id == c.id]

        if exclude_monthly:
            return [c for c in customers if not c.is_monthly]
        return list(customers)

    async def get_customer(self, customer_id: str, force_refresh: bool = False):
        customers = await self.get_all_customers(force_refresh=force_refresh)
        for c in customers:
            if c.id == customer_id:
                return c
        return None

    async def store_customer(self, data: dict):
        await self.initialize()
        if not self.initialized:
            raise Exception("Google Sheets backend not initialized.")

        # Check duplicate phone number — raise error instead of silently updating
        cust_num = data.get("customer_number")
        if cust_num and str(cust_num).strip():
            existing = await self.get_all_customers()
            for c in existing:
                if c.customer_number and c.customer_number.strip() == str(cust_num).strip():
                    raise Exception(f"Customer with phone number '{cust_num}' already exists (Name: {c.customer_name}). Use update instead.")

        doc_id = str(uuid.uuid4())
        now = datetime.now(timezone(timedelta(hours=5, minutes=30))).replace(tzinfo=None)

        # Handle Date Parsing - normalize business date to midnight
        date_val = data.get("date")
        if not date_val:
            parsed_date = now.replace(hour=0, minute=0, second=0, microsecond=0)
        elif isinstance(date_val, datetime):
            parsed_date = date_val.replace(hour=0, minute=0, second=0, microsecond=0)
        else:
            parsed_date = self._parse_datetime(date_val) or now
            parsed_date = parsed_date.replace(hour=0, minute=0, second=0, microsecond=0)

        # Extract jar, paid, pending, is_monthly from root or transactions
        jar_val = data.get("jar") or 0
        paid_val = data.get("paid") or 0.0
        pending_val = data.get("pending") or 0.0
        is_monthly_val = data.get("is_monthly") or data.get("isMonthly") or False

        transactions = data.get("transactions") or []
        if jar_val == 0 and paid_val == 0.0 and pending_val == 0.0 and transactions:
            for tx in transactions:
                if isinstance(tx, dict):
                    jar_val += int(tx.get("jar") or 0)
                    paid_val += float(tx.get("paid") or tx.get("jama") or 0.0)
                    pending_val += float(tx.get("pending") or tx.get("baki") or 0.0)

        customer = CustomerObj(
            id=doc_id,
            customer_name=data.get("customer_name", ""),
            customer_address=data.get("customer_address", ""),
            customer_number=data.get("customer_number", ""),
            jar=int(jar_val),
            paid=float(paid_val),
            pending=float(pending_val),
            success=data.get("success", False),
            date=parsed_date,
            is_monthly=bool(is_monthly_val),
            created_at=now,
            updated_at=now,
            rate=float(data.get("rate", 25.0))
        )

        row_values = self._object_to_customer_row(customer)
        async with sheets_lock:
            # Use explicit row targeting instead of append_row to avoid
            # Google Sheets API "table detection" race condition where
            # multiple rapid writes overwrite each other on the same row.
            id_col = await self._retry(self.customers_sheet.col_values, 1)
            # Find last row with actual data in column A
            last_data_row = 1
            for i, val in enumerate(id_col):
                if val and str(val).strip():
                    last_data_row = i + 1
            next_row = last_data_row + 1
            await self._retry(
                self.customers_sheet.update,
                f"A{next_row}:M{next_row}",
                [row_values],
                value_input_option="USER_ENTERED"
            )
            customer.row_idx = next_row
            # Targeted Cache Update
            if self._customers_cache:
                self._customers_cache[1].append(customer)

        # Create initial transaction log if jar count, paid, or pending is recorded
        if customer.jar or customer.paid or customer.pending:
            await self.add_transaction({
                "customer_id": doc_id,
                "date": parsed_date,
                "jar_count": customer.jar,
                "amount": customer.paid,
                "pending": customer.pending,
                "description": "Initial Record"
            })
        else:
            await self.recalculate_customer_balances(doc_id)

        return doc_id

    async def update_customer(self, customer_id: str, data: dict):
        await self.initialize()
        if not self.initialized:
            return False

        customer = await self.get_customer(customer_id)
        if not customer:
            return False

        if hasattr(customer, 'row_idx') and customer.row_idx:
            row_idx = customer.row_idx
        else:
            async with sheets_lock:
                id_list = await self._retry(self.customers_sheet.col_values, 1)
                
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
        now = datetime.now(timezone(timedelta(hours=5, minutes=30))).replace(tzinfo=None)

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
                customer.date = data["date"].replace(hour=0, minute=0, second=0, microsecond=0)
            else:
                parsed_d = self._parse_datetime(data["date"])
                if parsed_d:
                    customer.date = parsed_d.replace(hour=0, minute=0, second=0, microsecond=0)

        customer.updated_at = now

        row_values = self._object_to_customer_row(customer)
        
        async with sheets_lock:
            await self._retry(self.customers_sheet.update, f"A{row_idx}:M{row_idx}", [row_values])
            # Targeted Cache Update
            if self._customers_cache:
                for i, c in enumerate(self._customers_cache[1]):
                    if c.id == customer_id:
                        self._customers_cache[1][i] = customer
                        break

        # If transactions list is provided, synchronize it directly.
        # Otherwise, generate transaction log if differences exist.
        if "transactions" in data and data["transactions"] is not None:
            # Safely check if incoming transactions are actually different to prevent accidental wipe
            existing_txns = await self.get_transactions_by_customer(customer_id)
            existing_simplified = []
            for t in existing_txns:
                existing_simplified.append({
                    "id": str(t.id),
                    "date": t.date.strftime("%Y-%m-%d") if t.date else "",
                    "jar": int(t.jar_count),
                    "paid": float(t.amount),
                    "pending": float(t.manual_bill)
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
                await self.sync_customer_transactions(customer_id, data["transactions"])
            else:
                await self.sync_customer_sheet(customer_id)
        else:
            # If jar, paid, or pending is being updated, we must update the "Initial Record" transaction
            # so that recalculate_customer_balances doesn't overwrite our changes.
            if "jar" in data or "paid" in data or "pending" in data:
                txns = await self.get_transactions_by_customer(customer_id)
                initial_txn = None
                for t in txns:
                    if t.description in ["Initial Record", "Opening Balance"]:
                        initial_txn = t
                        break
                
                if initial_txn:
                    if "jar" in data:
                        initial_txn.jar_count = int(data["jar"])
                    if "paid" in data:
                        initial_txn.amount = float(data["paid"])
                    if "pending" in data:
                        initial_txn.manual_bill = float(data["pending"])
                        initial_txn.pending = float(data["pending"])
                    
                    initial_txn.updated_at = now
                    row_vals = self._object_to_transaction_row(initial_txn)
                    
                    if hasattr(initial_txn, 'row_idx') and initial_txn.row_idx:
                        async with sheets_lock:
                            await self._retry(self.transactions_sheet.update, f"A{initial_txn.row_idx}:J{initial_txn.row_idx}", [row_vals])
                else:
                    # If no initial record was found, create one
                    await self.add_transaction({
                        "customer_id": customer_id,
                        "date": customer.date or now,
                        "jar_count": customer.jar,
                        "amount": customer.paid,
                        "pending": customer.pending,
                        "description": "Initial Record"
                    })

            # DO NOT create automatic transactions based on jar/paid differences.
            # Transactions should only be added explicitly via add_transaction endpoints.
            # Simply recalculate the correct balances from the existing true transaction records.
            await self.recalculate_customer_balances(customer_id)

        return True

    async def delete_customer(self, customer_id: str):
        await self.initialize()
        if not self.initialized:
            return False

        customer = await self.get_customer(customer_id)
        if customer:
            # Delete customer's individual sheet if it exists
            cust_sheet = await self._find_customer_worksheet_by_id(customer_id, customer.customer_name)
            if cust_sheet:
                try:
                    async with sheets_lock:
                        await self._retry(self.spreadsheet.del_worksheet, cust_sheet)
                    self._invalidate_worksheets_cache()
                    if customer_id in self._worksheet_cache:
                        del self._worksheet_cache[customer_id]
                except Exception as e:
                    print(f"Error deleting customer worksheet: {e}")

        if customer and hasattr(customer, 'row_idx') and customer.row_idx:
            row_idx = customer.row_idx
        else:
            async with sheets_lock:
                id_list = await self._retry(self.customers_sheet.col_values, 1)
                
            row_idx = -1
            for idx, cid in enumerate(id_list):
                if str(cid) == customer_id:
                    row_idx = idx + 1
                    break

        if row_idx == -1:
            return False

        async with sheets_lock:
            await self._retry(self.customers_sheet.delete_rows, row_idx)
            # Targeted Cache Update
            if self._customers_cache:
                new_list = []
                for c in self._customers_cache[1]:
                    if c.id != customer_id:
                        if hasattr(c, 'row_idx') and c.row_idx and c.row_idx > row_idx:
                            c.row_idx -= 1
                        new_list.append(c)
                self._customers_cache = (self._customers_cache[0], new_list)

        # Cascading delete transactions synchronously
        await self.delete_transactions_by_customer(customer_id)
        return True

    async def search_customers(self, query_str: str):
        if not query_str:
            return []
        
        customers = await self.get_all_customers()
        query_lower = query_str.lower()
        results = []

        for c in customers:
            if (query_lower in c.customer_name.lower() or 
                query_lower in c.customer_address.lower() or 
                query_lower in c.customer_number.lower()):
                results.append(c)

        return results

    # --- TRANSACTION CRUD METHODS ---

    async def get_all_transactions(self, force_refresh=False):
        await self.initialize()
        if not self.initialized:
            return []

        now_ts = time.time()
        # Granular locking: only lock the cache check and Google Sheets fetch
        async with sheets_lock:
            if not force_refresh and self._transactions_cache and (now_ts - self._transactions_cache[0] < self._cache_duration):
                return list(self._transactions_cache[1])
            else:
                all_values = await self._retry(self.transactions_sheet.get_all_values)
                
        # Release lock before parsing data
        transactions = []
        if all_values:
            headers = self.transaction_headers # Always enforce our canonical headers
                
            for idx, row in enumerate(all_values[1:]):
                if not any(row):
                    continue
                padded = row + [''] * (len(headers) - len(row))
                row_dict = dict(zip(headers, padded))
                if row_dict.get('id'):  # Only include rows with a valid ID
                    txn = self._row_to_transaction(row_dict)
                    txn.row_idx = idx + 2
                    transactions.append(txn)
                    
        async with sheets_lock:
            self._transactions_cache = (now_ts, transactions)

        return list(transactions)

    async def get_transactions_by_customer(self, customer_id: str, force_refresh=False):
        txns = await self.get_all_transactions(force_refresh=force_refresh)
        filtered = [t for t in txns if t.customer_id == customer_id]
        # Sort descending by date and ID
        def safe_sort_key(t):
            try:
                tid = int(t.id)
            except:
                tid = 0
            return (t.date or datetime.min, tid)
        filtered.sort(key=safe_sort_key, reverse=True)
        return filtered

    async def add_transaction(self, data: dict):
        await self.initialize()
        if not self.initialized:
            raise Exception("Google Sheets backend not initialized.")

        next_id = str(uuid.uuid4())

        now = datetime.now(timezone(timedelta(hours=5, minutes=30))).replace(tzinfo=None)
        date_val = data.get("date")
        if not date_val:
            parsed_date = now.replace(hour=0, minute=0, second=0, microsecond=0)
        elif isinstance(date_val, datetime):
            parsed_date = date_val.replace(hour=0, minute=0, second=0, microsecond=0)
        else:
            parsed_date = self._parse_datetime(date_val) or now
            parsed_date = parsed_date.replace(hour=0, minute=0, second=0, microsecond=0)

        customer_id = data.get("customer_id", "")
        
        # Calculate/retrieve pending value (Direct Baki minus Jama calculation, no jar rate)
        pending_val = data.get("pending")
        desc = data.get("description", "")
        if desc in ["Initial Record", "Opening Balance"]:
            try:
                pending_val = float(pending_val or 0.0)
            except:
                pending_val = 0.0
        elif pending_val is None:
            pending_val = - float(data.get("amount", 0.0))
        else:
            try:
                pending_val = float(pending_val) - float(data.get("amount", 0.0))
            except:
                pending_val = - float(data.get("amount", 0.0))

        man_bill = float(data.get("manual_bill", 0.0))
        if desc in ["Initial Record", "Opening Balance"]:
            man_bill = pending_val

        txn = TransactionObj(
            id=next_id,
            customer_id=customer_id,
            date=parsed_date,
            jar_count=int(data.get("jar_count", 0)),
            amount=float(data.get("amount", 0.0)),
            pending=pending_val,
            manual_bill=man_bill,
            description=desc,
            created_at=now,
            updated_at=now
        )

        row_values = self._object_to_transaction_row(txn)
        async with sheets_lock:
            # Use explicit row targeting (same fix as store_customer)
            id_col = await self._retry(self.transactions_sheet.col_values, 1)
            last_data_row = 1
            for i, val in enumerate(id_col):
                if val and str(val).strip():
                    last_data_row = i + 1
            next_row = last_data_row + 1
            await self._retry(
                self.transactions_sheet.update,
                f"A{next_row}:J{next_row}",
                [row_values],
                value_input_option="USER_ENTERED"
            )
            txn.row_idx = next_row
            # Targeted Cache Update
            if self._transactions_cache:
                self._transactions_cache[1].append(txn)

        # Recalculate customer balances and sync customer's sheet synchronously
        await self.recalculate_customer_balances(customer_id)
        return txn

    async def update_transaction(self, txn_id: str, data: dict):
        await self.initialize()
        if not self.initialized:
            return None

        # Find transaction in cached/fetched list
        txns = await self.get_all_transactions()
        txn = None
        for t in txns:
            if str(t.id) == str(txn_id):
                txn = t
                break

        if not txn:
            return None

        if hasattr(txn, 'row_idx') and txn.row_idx:
            row_idx = txn.row_idx
        else:
            # Find row index using col_values(1)
            async with sheets_lock:
                id_list = await self._retry(self.transactions_sheet.col_values, 1)

            row_idx = -1
            for idx, tid in enumerate(id_list):
                if str(tid) == str(txn_id):
                    row_idx = idx + 1
                    break

        if row_idx == -1:
            return None

        now = datetime.now(timezone(timedelta(hours=5, minutes=30))).replace(tzinfo=None)
        if "date" in data and data["date"]:
            if isinstance(data["date"], datetime):
                txn.date = data["date"].replace(hour=0, minute=0, second=0, microsecond=0)
            else:
                parsed_d = self._parse_datetime(data["date"])
                if parsed_d:
                    txn.date = parsed_d.replace(hour=0, minute=0, second=0, microsecond=0)
        if "jar_count" in data: txn.jar_count = int(data["jar_count"])
        if "amount" in data: txn.amount = float(data["amount"])
        if "pending" in data: txn.pending = float(data["pending"])
        if "manual_bill" in data: txn.manual_bill = float(data["manual_bill"])
        if "description" in data: txn.description = data["description"]
        txn.updated_at = now

        row_values = self._object_to_transaction_row(txn)
        async with sheets_lock:
            await self._retry(self.transactions_sheet.update, f"A{row_idx}:J{row_idx}", [row_values])
            # Targeted Cache Update
            if self._transactions_cache:
                for i, t in enumerate(self._transactions_cache[1]):
                    t_id = int(t.id) if str(t.id).isdigit() else t.id
                    if t_id == txn_id:
                        self._transactions_cache[1][i] = txn
                        break

        # Recalculate customer balances and sync customer's sheet
        await self.recalculate_customer_balances(txn.customer_id)

        return txn

    async def delete_transaction(self, txn_id: str):
        await self.initialize()
        if not self.initialized:
            return False

        # Find transaction to get customer_id
        txns = await self.get_all_transactions()
        txn = None
        for t in txns:
            if str(t.id) == str(txn_id):
                txn = t
                break

        if not txn:
            return False

        customer_id = txn.customer_id

        if hasattr(txn, 'row_idx') and txn.row_idx:
            row_idx = txn.row_idx
        else:
            # Find row index using col_values(1)
            async with sheets_lock:
                id_list = await self._retry(self.transactions_sheet.col_values, 1)

            row_idx = -1
            for idx, tid in enumerate(id_list):
                if str(tid) == str(txn_id):
                    row_idx = idx + 1
                    break

        if row_idx == -1:
            return False

        async with sheets_lock:
            await self._retry(self.transactions_sheet.delete_rows, row_idx)
            # Targeted Cache Update
            if self._transactions_cache:
                new_list = []
                for t in self._transactions_cache[1]:
                    if str(t.id) != str(txn_id):
                        if hasattr(t, 'row_idx') and t.row_idx and t.row_idx > row_idx:
                            t.row_idx -= 1
                        new_list.append(t)
                self._transactions_cache = (self._transactions_cache[0], new_list)

        # Recalculate customer balances and sync customer's sheet
        if customer_id:
            await self.recalculate_customer_balances(customer_id)

        return True

    async def delete_transactions_by_customer(self, customer_id: str):
        await self.initialize()
        if not self.initialized:
            return False

        # Find matching transactions rows using cached row_idx
        txns = await self.get_all_transactions()
        rows_to_delete = [t.row_idx for t in txns if t.customer_id == customer_id and hasattr(t, 'row_idx') and t.row_idx]

        if not rows_to_delete:
            async with sheets_lock:
                cust_ids = await self._retry(self.transactions_sheet.col_values, 2)
            for idx, cid in enumerate(cust_ids):
                if str(cid) == customer_id:
                    rows_to_delete.append(idx + 1)

        if not rows_to_delete:
            return True

        # Sort descending to delete from bottom up without shifting remaining targets
        rows_to_delete.sort(reverse=True)
        async with sheets_lock:
            for r_idx in rows_to_delete:
                await self._retry(self.transactions_sheet.delete_rows, r_idx)
            # Targeted Cache Update
            if self._transactions_cache:
                new_list = []
                for t in self._transactions_cache[1]:
                    if t.customer_id != customer_id:
                        if hasattr(t, 'row_idx') and t.row_idx:
                            shift = sum(1 for r in rows_to_delete if r < t.row_idx)
                            t.row_idx -= shift
                        new_list.append(t)
                self._transactions_cache = (self._transactions_cache[0], new_list)

        return True

    async def sync_customer_transactions(self, customer_id: str, client_txns: list):
        await self.initialize()
        if not self.initialized:
            return False

        now = datetime.now(timezone(timedelta(hours=5, minutes=30))).replace(tzinfo=None)
        
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
                manual_bill=float(t.get("manual_bill") or 0.0),
                description=t.get("description") or "Delivery Log",
                created_at=now,
                updated_at=now
            )
            rows_to_add.append(self._object_to_transaction_row(txn))

        # 2. Find and delete all existing transactions for this customer
        txns = await self.get_all_transactions()
        rows_to_delete = [t.row_idx for t in txns if t.customer_id == customer_id and hasattr(t, 'row_idx') and t.row_idx]

        if not rows_to_delete:
            async with sheets_lock:
                cust_ids = await self._retry(self.transactions_sheet.col_values, 2)
            for idx, cid in enumerate(cust_ids):
                if str(cid) == customer_id:
                    rows_to_delete.append(idx + 1)

        async with sheets_lock:
            if rows_to_delete:
                rows_to_delete.sort(reverse=True)
                for r in rows_to_delete:
                    await self._retry(self.transactions_sheet.delete_rows, r)

            # 3. Append new transactions
            if rows_to_add:
                # Find last row with actual data in column A
                id_col = await self._retry(self.transactions_sheet.col_values, 1)
                last_data_row = 1
                for i, val in enumerate(id_col):
                    if val and str(val).strip():
                        last_data_row = i + 1
                next_row = last_data_row + 1
                end_row = next_row + len(rows_to_add) - 1
                
                await self._retry(
                    self.transactions_sheet.update,
                    f"A{next_row}:J{end_row}",
                    rows_to_add,
                    value_input_option="USER_ENTERED"
                )

        # 4. Invalidate cache so they are reloaded
        self._invalidate_transactions_cache()
        
        # 5. Recalculate customer balances and sync customer worksheet (ledger sheet)
        await self.recalculate_customer_balances(customer_id)
        return True

    async def _find_customer_worksheet_by_id(self, customer_id: str, name: str):
        await self.initialize()
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
            all_sheets = await self._get_worksheets_cached()
            
            sheet_names = [s.title for s in all_sheets if s.title not in ["customers", "transactions"]]
            
            if sheet_names:
                ranges = [f"'{s_name}'!A1" for s_name in sheet_names]
                async with sheets_lock:
                    results = await self._retry(self.spreadsheet.values_batch_get, ranges)
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

    async def _get_unique_sheet_title(self, desired_name: str, customer_id: str):
        sanitized_name = desired_name
        for c in [':', '\\', '/', '?', '*', '[', ']']:
            sanitized_name = sanitized_name.replace(c, '')
        sanitized_name = sanitized_name[:90]
        
        existing = await self._find_customer_worksheet_by_id(customer_id, desired_name)
        if existing:
            return existing.title
            
        title = sanitized_name
        counter = 1
        
        try:
            all_sheets = await self._get_worksheets_cached()
            existing_titles = {s.title for s in all_sheets}
        except Exception as e:
            print(f"Error fetching sheets for unique title: {e}")
            existing_titles = set()
            
        while title in existing_titles:
            counter += 1
            title = f"{sanitized_name} ({counter})"
        return title

    async def recalculate_customer_balances(self, customer_id: str):
        """
        Recalculates the totals and stores the final outstanding balance in the 'customers' sheet.
        Subtracts Jama (paid amount) from Baki (pending amount) correctly.
        """
        await self.initialize()
        if not self.initialized:
            return

        customer = await self.get_customer(customer_id)
        if not customer:
            return

        txns = await self.get_transactions_by_customer(customer_id, force_refresh=True)

        def safe_sort_key(t):
            try:
                tid = int(t.id)
            except:
                tid = 0
            return (t.date or datetime.min, tid)

        txns_chrono = sorted(txns, key=safe_sort_key)

        rate = getattr(customer, 'rate', 25.0) or 25.0

        total_jars = 0
        total_paid = 0.0
        running_baki = 0.0

        txn_updates = []
        for t in txns_chrono:
            j_cnt = int(t.jar_count or 0)
            p_amt = float(t.amount or 0.0)
            total_jars += j_cnt
            if p_amt > 0:
                total_paid += p_amt

            # bill_amount is strictly the manual bill entered by the user
            bill_amount = float(getattr(t, 'manual_bill', 0.0) or 0.0)

            # If it's an initial balance record, treat manual_bill or pending as the opening balance
            if getattr(t, 'description', '') in ["Initial Record", "Opening Balance"]:
                opening_pending = float(getattr(t, 'manual_bill', 0.0) or getattr(t, 'pending', 0.0) or 0.0)
                net_change = opening_pending - p_amt
            else:
                net_change = bill_amount - p_amt

            running_baki += net_change
            running_baki = max(0.0, running_baki)  # Never let outstanding balance go negative
            t.pending = running_baki

            if hasattr(t, 'row_idx') and t.row_idx:
                txn_updates.append((t.row_idx, self._object_to_transaction_row(t)))

        total_pending = running_baki

        customer.jar = total_jars
        customer.paid = total_paid
        customer.pending = total_pending
        customer.updated_at = datetime.now()

        # Update customer row in 'customers' sheet using row_idx directly
        if hasattr(customer, 'row_idx') and customer.row_idx:
            cust_row_values = self._object_to_customer_row(customer)
            async with sheets_lock:
                await self._retry(self.customers_sheet.update, f"A{customer.row_idx}:M{customer.row_idx}", [cust_row_values])
        else:
            # Fallback
            async with sheets_lock:
                cust_id_list = await self._retry(self.customers_sheet.col_values, 1)
            
            cust_row_idx = -1
            for idx, cid in enumerate(cust_id_list):
                if str(cid) == customer_id:
                    cust_row_idx = idx + 1
                    break

            if cust_row_idx != -1:
                cust_row_values = self._object_to_customer_row(customer)
                async with sheets_lock:
                    await self._retry(self.customers_sheet.update, f"A{cust_row_idx}:M{cust_row_idx}", [cust_row_values])

        # Update transaction rows in 'transactions' sheet
        if txn_updates:
            async with sheets_lock:
                for r_idx, row_vals in txn_updates:
                    try:
                        await self._retry(self.transactions_sheet.update, f"A{r_idx}:J{r_idx}", [row_vals])
                    except Exception as e:
                        print(f"Error updating transaction row {r_idx}: {e}")

        # Targeted Cache Update for customers
        if self._customers_cache:
            for i, c in enumerate(self._customers_cache[1]):
                if c.id == customer_id:
                    self._customers_cache[1][i] = customer
                    break

        await self.sync_customer_sheet(customer_id)

    async def sync_customer_sheet(self, customer_id: str):
        await self.initialize()
        if not self.initialized:
            return False
            
        customer = await self.get_customer(customer_id)
        if not customer:
            return False
            
        sheet = await self._find_customer_worksheet_by_id(customer_id, customer.customer_name)
        new_title = await self._get_unique_sheet_title(customer.customer_name, customer_id)
        
        async with sheets_lock:
            if not sheet:
                try:
                    sheet = await self._retry(self.spreadsheet.add_worksheet, new_title, rows="1000", cols="5")
                    self._invalidate_worksheets_cache()
                    self._worksheet_cache[customer_id] = sheet
                except Exception as e:
                    print(f"Error creating customer worksheet {new_title}: {e}")
                    return False
            elif sheet.title != new_title:
                try:
                    await self._retry(sheet.update_title, new_title)
                    self._invalidate_worksheets_cache()
                    self._worksheet_cache[customer_id] = sheet
                except Exception as e:
                    print(f"Error updating customer worksheet title: {e}")
                
        txns = await self.get_transactions_by_customer(customer_id)
        def safe_sort_key(t):
            try:
                tid = int(t.id)
            except:
                tid = 0
            return (t.date or datetime.min, tid)
        txns.sort(key=safe_sort_key)
        
        rows = [
            [f"ID: {customer_id}", "", "", "", ""],
            [f"Customer Name: {customer.customer_name}", f"Mobile: {customer.customer_number or ''}", "", "", ""],
            ["", "", "", "", ""],
            ["Date", "Jar", "Pending Amount", "Paid Amount", "Description"]
        ]
        
        for t in txns:
            date_str = t.date.strftime("%Y-%m-%d") if t.date else ""
            jar = t.jar_count
            desc = t.description or ""
            
            paid_amount = t.amount
            pending_amount = t.manual_bill
            
            rows.append([
                date_str,
                str(jar),
                f"{pending_amount:.2f}" if isinstance(pending_amount, (int, float)) else str(pending_amount),
                f"{paid_amount:.2f}" if isinstance(paid_amount, (int, float)) else str(paid_amount),
                desc
            ])
            
        async with sheets_lock:
            try:
                await self._retry(sheet.clear)
                await self._retry(sheet.update, "A1", rows)
                
                # Batch all format calls into a single API request to reduce rate limit hits
                try:
                    await self._retry(sheet.spreadsheet.batch_update, {
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
