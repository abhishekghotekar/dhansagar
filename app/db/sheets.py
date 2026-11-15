import os
import json
import uuid
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
from app.utils.formatter import normalize_phone

# Lock for sheet operations to prevent race conditions during updates
sheets_lock = asyncio.Lock()


class CustomerObj:
    def __init__(self, **kwargs):
        self.id = str(kwargs.get("id", ""))
        self.customer_name = str(kwargs.get("customer_name", ""))
        self.customer_address = str(kwargs.get("customer_address", ""))
        self.customer_number = str(kwargs.get("customer_number", ""))
        self.jar = int(kwargs.get("jar", 0))
        self.paid = float(kwargs.get("paid", 0.0))
        self.pending = float(kwargs.get("pending", 0.0))
        self.success = bool(kwargs.get("success", False))
        self.date = kwargs.get("date")
        self.is_monthly = bool(kwargs.get("is_monthly", False))
        self.created_at = kwargs.get("created_at")
        self.updated_at = kwargs.get("updated_at")
        self.rate = float(kwargs.get("rate", 25.0))
        self.row_idx = kwargs.get("row_idx", None)
        self.transactions = []

    def dict(self):
        formatted_txns = []
        for t in self.transactions:
            date_str = t.date.isoformat() if isinstance(t.date, datetime) else str(t.date or "")
            created_str = t.created_at.isoformat() if isinstance(t.created_at, datetime) else str(t.created_at or "")
            updated_str = t.updated_at.isoformat() if isinstance(t.updated_at, datetime) else str(t.updated_at or "")
            formatted_txns.append({
                "id": str(t.id),
                "customer_id": t.customer_id,
                "date": date_str,
                "jar": t.jar_count,
                "jar_count": t.jar_count,
                "paid": t.amount,
                "jama": t.amount,
                "pending": t.manual_bill,
                "baki": t.manual_bill,
                "manual_bill": t.manual_bill,
                "running_balance": t.pending,
                "description": t.description or "",
                "created_at": created_str,
                "updated_at": updated_str
            })

        date_str = self.date.isoformat() if isinstance(self.date, datetime) else str(self.date or "")
        created_str = self.created_at.isoformat() if isinstance(self.created_at, datetime) else str(self.created_at or "")
        updated_str = self.updated_at.isoformat() if isinstance(self.updated_at, datetime) else str(self.updated_at or "")

        return {
            "id": self.id,
            "customer_name": self.customer_name,
            "customer_address": self.customer_address,
            "customer_number": self.customer_number,
            "jar": self.jar,
            "paid": self.paid,
            "pending": self.pending,
            "success": self.success,
            "date": date_str,
            "is_monthly": self.is_monthly,
            "created_at": created_str,
            "updated_at": updated_str,
            "rate": self.rate,
            "transactions": formatted_txns
        }


class TransactionObj:
    def __init__(self, **kwargs):
        self.id = str(kwargs.get("id", ""))
        self.customer_id = str(kwargs.get("customer_id", ""))
        self.date = kwargs.get("date")
        self.jar_count = int(kwargs.get("jar_count", 0))
        self.amount = float(kwargs.get("amount", 0.0))
        self.pending = float(kwargs.get("pending", 0.0))
        self.manual_bill = float(kwargs.get("manual_bill", 0.0))
        self.description = str(kwargs.get("description", ""))
        self.created_at = kwargs.get("created_at")
        self.updated_at = kwargs.get("updated_at")
        self.row_idx = kwargs.get("row_idx", None)

    def dict(self):
        date_str = self.date.isoformat() if isinstance(self.date, datetime) else str(self.date or "")
        created_str = self.created_at.isoformat() if isinstance(self.created_at, datetime) else str(self.created_at or "")
        updated_str = self.updated_at.isoformat() if isinstance(self.updated_at, datetime) else str(self.updated_at or "")
        return {
            "id": self.id,
            "customer_id": self.customer_id,
            "date": date_str,
            "jar": self.jar_count,
            "jar_count": self.jar_count,
            "paid": self.amount,
            "amount": self.amount,
            "jama": self.amount,
            "pending": self.manual_bill,
            "baki": self.manual_bill,
            "manual_bill": self.manual_bill,
            "running_balance": self.pending,
            "description": self.description,
            "created_at": created_str,
            "updated_at": updated_str
        }


class GoogleSheetsClient:
    def __init__(self):
        self.client = None
        self.spreadsheet = None
        self.customers_sheet = None
        self.transactions_sheet = None
        self.initialized = False
        
        # Initialization trackers
        self._async_init_lock = asyncio.Lock()
        self._last_init_attempt_time = 0.0
        self._last_init_error = None
        
        # Worksheet canonical headers
        self.customer_headers = [
            "id", "customer_name", "customer_address", "customer_number", 
            "jar", "paid", "pending", "success", "date", "is_monthly", 
            "created_at", "updated_at", "rate"
        ]
        self.transaction_headers = [
            "id", "customer_id", "date", "jar_count", "amount", 
            "description", "created_at", "updated_at", "pending", "manual_bill"
        ]

        # In-memory caches to prevent hitting Google Sheets rate limits
        self._worksheet_cache = {}          # customer_id -> Worksheet
        self._customers_cache = None        # (timestamp, list of CustomerObj)
        self._transactions_cache = None     # (timestamp, list of TransactionObj)
        self._worksheets_list_cache = None  # (timestamp, list of Worksheet objects)
        self._cache_duration = 30.0

    async def _retry(self, func, *args, **kwargs):
        max_retries = 5
        delay = 1.0
        backoff_factor = 2.0
        
        for attempt in range(max_retries):
            try:
                return await asyncio.to_thread(func, *args, **kwargs)
            except Exception as e:
                status_code = getattr(getattr(e, "response", None), "status_code", None)
                err_str = str(e)

                # Never retry client errors (400 Bad Request, 403 Forbidden, 404 Not Found)
                if status_code in [400, 403, 404] or any(code in err_str for code in ["[400]", "[403]", "[404]"]):
                    raise e

                # Retry transient server/rate errors
                if status_code in [429, 500, 502, 503, 504] or any(code in err_str for code in ["[429]", "[500]", "[502]", "[503]", "[504]"]):
                    if attempt < max_retries - 1:
                        print(f"Google Sheets API rate/server error ({e}) on attempt {attempt+1}/{max_retries}. Retrying in {delay}s...")
                        await asyncio.sleep(delay)
                        delay *= backoff_factor
                        continue
                elif "RequestException" in str(type(e)) or "HTTPError" in str(type(e)) or "Timeout" in str(type(e)):
                    if attempt < max_retries - 1:
                        print(f"Google Sheets network error ({e}) on attempt {attempt+1}/{max_retries}. Retrying in {delay}s...")
                        await asyncio.sleep(delay)
                        delay *= backoff_factor
                        continue
                raise e

    async def initialize(self):
        if self.initialized:
            return True

        async with self._async_init_lock:
            if self.initialized:
                return True
                
            now = time.time()
            if now - self._last_init_attempt_time < 5.0:
                return False

            self._last_init_attempt_time = now
            sheet_id = (os.getenv("GOOGLE_SHEET_ID") or GOOGLE_SHEET_ID or "").strip()
            
            # Check multiple potential env vars for JSON dict content
            creds_json = (
                os.getenv("GOOGLE_SERVICE_ACCOUNT_DICT") or 
                os.getenv("GOOGLE_CREDENTIALS_JSON") or 
                os.getenv("GOOGLE_CREDENTIALS") or 
                GOOGLE_SERVICE_ACCOUNT_DICT or 
                ""
            ).strip()

            creds_path = (
                os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON") or 
                os.getenv("GOOGLE_APPLICATION_CREDENTIALS") or 
                GOOGLE_SERVICE_ACCOUNT_JSON or 
                "google-credentials.json"
            ).strip()

            if not sheet_id:
                print("GOOGLE_SHEET_ID is not configured. Please set the GOOGLE_SHEET_ID environment variable in your deployment dashboard.")
                return False

            scopes = [
                "https://www.googleapis.com/auth/spreadsheets",
                "https://www.googleapis.com/auth/drive"
            ]

            try:
                def get_creds():
                    # 1. If raw JSON string was passed directly in creds_path
                    candidate_json = None
                    if creds_path.startswith("{") and creds_path.endswith("}"):
                        candidate_json = creds_path
                    elif creds_json:
                        candidate_json = creds_json

                    if candidate_json:
                        raw = candidate_json.strip()
                        if (raw.startswith("'") and raw.endswith("'")) or (raw.startswith('"') and raw.endswith('"') and not raw.endswith('"}')):
                            raw = raw[1:-1]
                        info = json.loads(raw)
                        # Fix escaped newlines in private_key if present
                        if "private_key" in info and isinstance(info["private_key"], str) and "\\n" in info["private_key"]:
                            info["private_key"] = info["private_key"].replace("\\n", "\n")
                        return Credentials.from_service_account_info(info, scopes=scopes)

                    # 2. Check explicit paths
                    possible_paths = [
                        creds_path,
                        os.path.join(os.getcwd(), creds_path),
                        os.path.join(os.getcwd(), "google-credentials.json"),
                        "/etc/secrets/google-credentials.json",
                        os.path.join(os.path.dirname(__file__), "..", "..", "google-credentials.json")
                    ]
                    for p in possible_paths:
                        if p and os.path.isfile(p):
                            return Credentials.from_service_account_file(p, scopes=scopes)

                    # 3. Check any .json file in Render's /etc/secrets directory
                    if os.path.isdir("/etc/secrets"):
                        try:
                            for fname in os.listdir("/etc/secrets"):
                                if fname.endswith(".json"):
                                    fpath = os.path.join("/etc/secrets", fname)
                                    try:
                                        return Credentials.from_service_account_file(fpath, scopes=scopes)
                                    except Exception:
                                        pass
                        except Exception:
                            pass

                    # Diagnostic message
                    secrets_dir_exists = os.path.isdir("/etc/secrets")
                    secrets_contents = os.listdir("/etc/secrets") if secrets_dir_exists else "Directory not found"
                    raise Exception(
                        f"Google credentials not found.\n"
                        f"- GOOGLE_SERVICE_ACCOUNT_DICT is empty: {not bool(creds_json)}\n"
                        f"- /etc/secrets contents: {secrets_contents}\n"
                        f"- Current working dir files: {[f for f in os.listdir('.') if f.endswith('.json') or f.endswith('.txt')]}"
                    )

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

    def _invalidate_worksheets_cache(self):
        self._worksheets_list_cache = None

    async def _get_worksheets_cached(self, force_refresh=False):
        now = time.time()
        if not force_refresh and self._worksheets_list_cache and (now - self._worksheets_list_cache[0] < 300.0):
            return self._worksheets_list_cache[1]
        async with sheets_lock:
            all_sheets = await self._retry(self.spreadsheet.worksheets)
        self._worksheets_list_cache = (now, all_sheets)
        return all_sheets

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
            existing_headers = await self._retry(self.transactions_sheet.row_values, 1)
            if existing_headers and len(existing_headers) < len(self.transaction_headers):
                await self._retry(self.transactions_sheet.update, "A1:J1", [self.transaction_headers])
        except gspread.exceptions.WorksheetNotFound:
            self.transactions_sheet = await self._retry(self.spreadsheet.add_worksheet, "transactions", rows="5000", cols="10")
            await self._retry(self.transactions_sheet.append_row, self.transaction_headers)
            self._invalidate_worksheets_cache()

    def _parse_datetime(self, val):
        """Always returns a timezone-naive datetime to avoid comparison TypeErrors."""
        if not val:
            return None
        if isinstance(val, datetime):
            return val.replace(tzinfo=None)
        
        val_str = str(val).strip()
        try:
            dt = datetime.fromisoformat(val_str.replace("Z", "+00:00"))
            return dt.replace(tzinfo=None)
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
                dt = datetime.strptime(val_str, fmt)
                return dt.replace(tzinfo=None)
            except ValueError:
                continue
        return None

    def _row_to_customer(self, row_dict):
        try:
            jar = int(float(row_dict.get("jar") or 0))
        except:
            jar = 0
        try:
            paid = float(row_dict.get("paid") or 0.0)
        except:
            paid = 0.0
        try:
            pending = float(row_dict.get("pending") or 0.0)
        except:
            pending = 0.0
        try:
            rate = float(row_dict.get("rate") or 25.0)
        except:
            rate = 25.0

        success = str(row_dict.get("success", "")).strip().lower() == "true"
        is_monthly = str(row_dict.get("is_monthly", "")).strip().lower() == "true"

        return CustomerObj(
            id=str(row_dict.get("id", "")).strip(),
            customer_name=str(row_dict.get("customer_name", "")).strip(),
            customer_address=str(row_dict.get("customer_address", "")).strip(),
            customer_number=str(row_dict.get("customer_number", "")).strip(),
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
        txn_id = str(row_dict.get("id", "")).strip()
        try:
            jar_count = int(float(row_dict.get("jar_count") or 0))
        except:
            jar_count = 0
        try:
            amount = float(row_dict.get("amount") or 0.0)
        except:
            amount = 0.0
        try:
            pending = float(row_dict.get("pending") or 0.0)
        except:
            pending = 0.0
        try:
            manual_bill = float(row_dict.get("manual_bill") or 0.0)
        except:
            manual_bill = 0.0

        desc = str(row_dict.get("description", "")).strip()
        if desc in ["Initial Record", "Opening Balance"] and manual_bill == 0.0 and pending > 0.0:
            manual_bill = pending

        return TransactionObj(
            id=txn_id,
            customer_id=str(row_dict.get("customer_id", "")).strip(),
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
        need_fetch = force_refresh or not self._customers_cache or (now_ts - self._customers_cache[0] >= self._cache_duration)
        
        if need_fetch:
            async with sheets_lock:
                all_values = await self._retry(self.customers_sheet.get_all_values)
            
            customers = []
            if all_values:
                headers = self.customer_headers
                for idx, row in enumerate(all_values[1:]):
                    if not any(row):
                        continue
                    padded = row + [''] * (len(headers) - len(row))
                    row_dict = dict(zip(headers, padded))
                    if row_dict.get('id'):
                        cust = self._row_to_customer(row_dict)
                        cust.row_idx = idx + 2
                        customers.append(cust)
                        
            self._customers_cache = (now_ts, customers)
        else:
            customers = list(self._customers_cache[1])
            
        # Associate transactions
        txns = await self.get_all_transactions(force_refresh=force_refresh)
        txns_by_cust = {}
        for t in txns:
            txns_by_cust.setdefault(t.customer_id, []).append(t)
            
        for c in customers:
            c.transactions = txns_by_cust.get(c.id, [])

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

        # Check duplicate phone number with normalized digits
        cust_num = normalize_phone(data.get("customer_number"))
        if cust_num:
            existing = await self.get_all_customers()
            for c in existing:
                if normalize_phone(c.customer_number) == cust_num:
                    raise Exception(f"Customer with phone number '{data.get('customer_number')}' already exists (Name: {c.customer_name}).")

        doc_id = str(uuid.uuid4())
        now = datetime.now(timezone(timedelta(hours=5, minutes=30))).replace(tzinfo=None)

        date_val = data.get("date")
        if not date_val:
            parsed_date = now.replace(hour=0, minute=0, second=0, microsecond=0)
        elif isinstance(date_val, datetime):
            parsed_date = date_val.replace(tzinfo=None, hour=0, minute=0, second=0, microsecond=0)
        else:
            parsed_date = self._parse_datetime(date_val) or now
            parsed_date = parsed_date.replace(hour=0, minute=0, second=0, microsecond=0)

        jar_val = int(data.get("jar") or 0)
        paid_val = float(data.get("paid") or 0.0)
        pending_val = float(data.get("pending") or 0.0)
        is_monthly_val = bool(data.get("is_monthly") or data.get("isMonthly") or False)
        rate_val = float(data.get("rate") or 25.0)

        customer = CustomerObj(
            id=doc_id,
            customer_name=str(data.get("customer_name", "")).strip(),
            customer_address=str(data.get("customer_address", "")).strip(),
            customer_number=str(data.get("customer_number", "")).strip(),
            jar=jar_val,
            paid=paid_val,
            pending=pending_val,
            success=bool(data.get("success", False)),
            date=parsed_date,
            is_monthly=is_monthly_val,
            created_at=now,
            updated_at=now,
            rate=rate_val
        )

        row_values = self._object_to_customer_row(customer)
        async with sheets_lock:
            id_col = await self._retry(self.customers_sheet.col_values, 1)
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

        # Invalidate cache so fresh fetch attaches transactions
        self._invalidate_customers_cache()

        # Create opening transaction log if initial balance/jar count is recorded
        # add_transaction() has idempotency guard so double-calls are safe
        if jar_val > 0 or paid_val > 0 or pending_val > 0:
            await self.add_transaction({
                "customer_id": doc_id,
                "date": parsed_date,
                "jar_count": jar_val,
                "amount": paid_val,
                "manual_bill": pending_val,
                "pending": pending_val,
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

        row_idx = customer.row_idx
        if not row_idx:
            async with sheets_lock:
                id_list = await self._retry(self.customers_sheet.col_values, 1)
            for idx, cid in enumerate(id_list):
                if str(cid).strip() == customer_id:
                    row_idx = idx + 1
                    break

        if not row_idx or row_idx == -1:
            return False

        now = datetime.now(timezone(timedelta(hours=5, minutes=30))).replace(tzinfo=None)

        if "customer_name" in data and data["customer_name"] is not None:
            customer.customer_name = str(data["customer_name"]).strip()
        if "customer_address" in data and data["customer_address"] is not None:
            customer.customer_address = str(data["customer_address"]).strip()
        if "customer_number" in data and data["customer_number"] is not None:
            customer.customer_number = str(data["customer_number"]).strip()
        if "success" in data and data["success"] is not None:
            customer.success = str(data["success"]).lower() == "true"
        if "is_monthly" in data and data["is_monthly"] is not None:
            customer.is_monthly = str(data["is_monthly"]).lower() == "true"
        if "rate" in data and data["rate"] is not None:
            customer.rate = float(data["rate"])
        
        if "date" in data and data["date"]:
            if isinstance(data["date"], datetime):
                customer.date = data["date"].replace(tzinfo=None, hour=0, minute=0, second=0, microsecond=0)
            else:
                parsed_d = self._parse_datetime(data["date"])
                if parsed_d:
                    customer.date = parsed_d.replace(hour=0, minute=0, second=0, microsecond=0)

        customer.updated_at = now
        row_values = self._object_to_customer_row(customer)
        
        async with sheets_lock:
            await self._retry(
                self.customers_sheet.update,
                f"A{row_idx}:M{row_idx}",
                [row_values],
                value_input_option="USER_ENTERED"
            )

        self._invalidate_customers_cache()

        # If full transactions list provided explicitly, synchronize it
        if "transactions" in data and data["transactions"] is not None:
            await self.sync_customer_transactions(customer_id, data["transactions"])
        else:
            # Recalculate true cumulative balances from the transaction ledger
            await self.recalculate_customer_balances(customer_id)

        return True

    async def delete_customer(self, customer_id: str):
        await self.initialize()
        if not self.initialized:
            return False

        customer = await self.get_customer(customer_id)
        if not customer:
            return False

        # Delete customer's individual ledger sheet if exists
        try:
            cust_sheet = await self._find_customer_worksheet_by_id(customer_id, customer.customer_name)
            if cust_sheet:
                # Verify the sheet still exists in the workbook before attempting deletion
                all_current_sheets = await self._retry(self.spreadsheet.worksheets)
                current_sheet_ids = {s.id for s in all_current_sheets}
                if cust_sheet.id in current_sheet_ids:
                    async with sheets_lock:
                        await self.spreadsheet.del_worksheet(cust_sheet)
                self._invalidate_worksheets_cache()
                if customer_id in self._worksheet_cache:
                    del self._worksheet_cache[customer_id]
        except Exception as e:
            print(f"Notice: Individual sheet not deleted for customer (may already be removed): {e}")
            self._invalidate_worksheets_cache()
            if customer_id in self._worksheet_cache:
                del self._worksheet_cache[customer_id]

        # Delete from customers worksheet
        row_idx = customer.row_idx
        if not row_idx:
            async with sheets_lock:
                id_list = await self._retry(self.customers_sheet.col_values, 1)
            for idx, cid in enumerate(id_list):
                if str(cid).strip() == customer_id:
                    row_idx = idx + 1
                    break

        if row_idx and row_idx > 1:
            async with sheets_lock:
                await self._retry(self.customers_sheet.delete_rows, row_idx)

        self._invalidate_customers_cache()

        # Delete all customer transactions in one batch
        await self.delete_transactions_by_customer(customer_id)
        return True

    async def search_customers(self, query_str: str):
        if not query_str:
            return []
        
        customers = await self.get_all_customers()
        query_lower = query_str.lower().strip()
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
        need_fetch = force_refresh or not self._transactions_cache or (now_ts - self._transactions_cache[0] >= self._cache_duration)
        
        if need_fetch:
            async with sheets_lock:
                all_values = await self._retry(self.transactions_sheet.get_all_values)
                
            transactions = []
            if all_values:
                headers = self.transaction_headers
                for idx, row in enumerate(all_values[1:]):
                    if not any(row):
                        continue
                    padded = row + [''] * (len(headers) - len(row))
                    row_dict = dict(zip(headers, padded))
                    if row_dict.get('id'):
                        txn = self._row_to_transaction(row_dict)
                        txn.row_idx = idx + 2
                        transactions.append(txn)
                        
            self._transactions_cache = (now_ts, transactions)
            return list(transactions)
        else:
            return list(self._transactions_cache[1])

    async def get_transactions_by_customer(self, customer_id: str, force_refresh=False):
        txns = await self.get_all_transactions(force_refresh=force_refresh)
        filtered = [t for t in txns if t.customer_id == customer_id]
        
        def safe_sort_key(t):
            t_dt = t.date if isinstance(t.date, datetime) else datetime.min
            return t_dt
            
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
            parsed_date = date_val.replace(tzinfo=None, hour=0, minute=0, second=0, microsecond=0)
        else:
            parsed_date = self._parse_datetime(date_val) or now
            parsed_date = parsed_date.replace(hour=0, minute=0, second=0, microsecond=0)

        customer_id = str(data.get("customer_id", "")).strip()
        jar_count = int(data.get("jar_count") or data.get("jar") or 0)
        amount = float(data.get("amount") or data.get("paid") or 0.0)
        manual_bill = float(data.get("manual_bill") or data.get("pending") or 0.0)
        desc = str(data.get("description", "Daily Delivery")).strip()

        # ---- IDEMPOTENCY GUARD: prevent duplicate transactions ----
        # For "Initial Record"/"Opening Balance" check by customer_id + description only (one per customer)
        # For other entries: check by customer_id + date + description
        if customer_id:
            existing_txns = await self.get_all_transactions()
            parsed_date_str = parsed_date.strftime("%Y-%m-%d")
            for t in existing_txns:
                if t.customer_id != customer_id:
                    continue
                t_date_str = t.date.strftime("%Y-%m-%d") if isinstance(t.date, datetime) else ""
                t_desc = (t.description or "").strip()
                if desc in ["Initial Record", "Opening Balance"]:
                    # Only one opening record allowed per customer
                    if t_desc in ["Initial Record", "Opening Balance"]:
                        print(f"[IDEMPOTENCY] Skipping duplicate {desc} for customer {customer_id}")
                        return t
                else:
                    # Same date + same description = duplicate
                    if t_date_str == parsed_date_str and t_desc == desc:
                        print(f"[IDEMPOTENCY] Skipping duplicate transaction on {parsed_date_str} ({desc}) for customer {customer_id}")
                        return t
        # ----------------------------------------------------------

        txn = TransactionObj(
            id=next_id,
            customer_id=customer_id,
            date=parsed_date,
            jar_count=jar_count,
            amount=amount,
            pending=0.0,
            manual_bill=manual_bill,
            description=desc,
            created_at=now,
            updated_at=now
        )

        row_values = self._object_to_transaction_row(txn)
        async with sheets_lock:
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

        self._invalidate_transactions_cache()

        # Recalculate customer running balance and update customer summary
        await self.recalculate_customer_balances(customer_id)
        return txn

    async def update_transaction(self, txn_id: str, data: dict):
        await self.initialize()
        if not self.initialized:
            return None

        txns = await self.get_all_transactions()
        txn = None
        for t in txns:
            if str(t.id).strip() == str(txn_id).strip():
                txn = t
                break

        if not txn:
            return None

        row_idx = txn.row_idx
        if not row_idx:
            async with sheets_lock:
                id_list = await self._retry(self.transactions_sheet.col_values, 1)
            for idx, tid in enumerate(id_list):
                if str(tid).strip() == str(txn_id).strip():
                    row_idx = idx + 1
                    break

        if not row_idx or row_idx == -1:
            return None

        now = datetime.now(timezone(timedelta(hours=5, minutes=30))).replace(tzinfo=None)
        if "date" in data and data["date"]:
            if isinstance(data["date"], datetime):
                txn.date = data["date"].replace(tzinfo=None, hour=0, minute=0, second=0, microsecond=0)
            else:
                parsed_d = self._parse_datetime(data["date"])
                if parsed_d:
                    txn.date = parsed_d.replace(hour=0, minute=0, second=0, microsecond=0)
                    
        if "jar_count" in data and data["jar_count"] is not None:
            txn.jar_count = int(data["jar_count"])
        elif "jar" in data and data["jar"] is not None:
            txn.jar_count = int(data["jar"])

        if "amount" in data and data["amount"] is not None:
            txn.amount = float(data["amount"])
        elif "paid" in data and data["paid"] is not None:
            txn.amount = float(data["paid"])

        if "manual_bill" in data and data["manual_bill"] is not None:
            txn.manual_bill = float(data["manual_bill"])
        elif "pending" in data and data["pending"] is not None:
            txn.manual_bill = float(data["pending"])

        if "description" in data and data["description"] is not None:
            txn.description = str(data["description"]).strip()

        txn.updated_at = now
        row_values = self._object_to_transaction_row(txn)
        
        async with sheets_lock:
            await self._retry(
                self.transactions_sheet.update,
                f"A{row_idx}:J{row_idx}",
                [row_values],
                value_input_option="USER_ENTERED"
            )

        self._invalidate_transactions_cache()
        await self.recalculate_customer_balances(txn.customer_id)
        return txn

    async def delete_transaction(self, txn_id: str):
        await self.initialize()
        if not self.initialized:
            return False

        txns = await self.get_all_transactions()
        txn = None
        for t in txns:
            if str(t.id).strip() == str(txn_id).strip():
                txn = t
                break

        if not txn:
            return False

        customer_id = txn.customer_id
        row_idx = txn.row_idx
        if not row_idx:
            async with sheets_lock:
                id_list = await self._retry(self.transactions_sheet.col_values, 1)
            for idx, tid in enumerate(id_list):
                if str(tid).strip() == str(txn_id).strip():
                    row_idx = idx + 1
                    break

        if not row_idx or row_idx <= 1:
            return False

        async with sheets_lock:
            await self._retry(self.transactions_sheet.delete_rows, row_idx)

        self._invalidate_transactions_cache()

        if customer_id:
            await self.recalculate_customer_balances(customer_id)

        return True

    async def delete_transactions_by_customer(self, customer_id: str):
        """Batch deletes all transactions of a customer using a single API request."""
        await self.initialize()
        if not self.initialized:
            return False

        txns = await self.get_all_transactions(force_refresh=True)
        rows_to_delete = [t.row_idx for t in txns if t.customer_id == customer_id and t.row_idx and t.row_idx > 1]

        if not rows_to_delete:
            return True

        # Group rows and execute via single batch_update request to avoid 429 errors
        rows_to_delete.sort(reverse=True)
        requests = [
            {
                "deleteDimension": {
                    "range": {
                        "sheetId": self.transactions_sheet.id,
                        "dimension": "ROWS",
                        "startIndex": r_idx - 1,
                        "endIndex": r_idx
                    }
                }
            }
            for r_idx in rows_to_delete
        ]

        async with sheets_lock:
            try:
                await self._retry(self.spreadsheet.batch_update, {"requests": requests})
            except Exception as e:
                print(f"Error during batch row deletion: {e}")
                # Fallback to sequential deletion if needed
                for r in rows_to_delete:
                    try:
                        await self._retry(self.transactions_sheet.delete_rows, r)
                    except:
                        pass

        self._invalidate_transactions_cache()
        return True

    async def sync_customer_transactions(self, customer_id: str, client_txns: list):
        await self.initialize()
        if not self.initialized:
            return False

        now = datetime.now(timezone(timedelta(hours=5, minutes=30))).replace(tzinfo=None)
        
        rows_to_add = []
        for t in client_txns:
            date_val = t.get("date")
            parsed_date = self._parse_datetime(date_val) or now
            
            txn = TransactionObj(
                id=str(t.get("id") or uuid.uuid4()),
                customer_id=customer_id,
                date=parsed_date,
                jar_count=int(t.get("jar") or t.get("jar_count") or 0),
                amount=float(t.get("paid") or t.get("amount") or 0.0),
                pending=0.0,
                manual_bill=float(t.get("pending") or t.get("manual_bill") or 0.0),
                description=str(t.get("description") or "Delivery Log").strip(),
                created_at=now,
                updated_at=now
            )
            rows_to_add.append(self._object_to_transaction_row(txn))

        # 1. Delete all existing transactions for this customer
        await self.delete_transactions_by_customer(customer_id)

        # 2. Append new transactions in bulk
        if rows_to_add:
            async with sheets_lock:
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

        self._invalidate_transactions_cache()
        await self.recalculate_customer_balances(customer_id)
        return True

    async def recalculate_customer_balances(self, customer_id: str):
        """
        Recalculates the cumulative running balance from the transaction ledger.
        Uses Worksheet.batch_update to update all transaction rows in 1 SINGLE API call.
        """
        await self.initialize()
        if not self.initialized:
            return

        customer = await self.get_customer(customer_id, force_refresh=True)
        if not customer:
            return

        txns = await self.get_transactions_by_customer(customer_id, force_refresh=True)

        def safe_sort_key(t):
            t_dt = t.date if isinstance(t.date, datetime) else datetime.min
            return t_dt

        # Sort chronologically (oldest first) to calculate running balance
        txns_chrono = sorted(txns, key=safe_sort_key)

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

            bill_amount = float(getattr(t, 'manual_bill', 0.0) or 0.0)
            desc = getattr(t, 'description', '') or ''

            if desc in ["Initial Record", "Opening Balance"]:
                net_change = bill_amount - p_amt
            else:
                net_change = bill_amount - p_amt

            running_baki += net_change
            running_baki = max(0.0, running_baki)
            t.pending = running_baki

            if hasattr(t, 'row_idx') and t.row_idx and t.row_idx > 1:
                txn_updates.append((t.row_idx, self._object_to_transaction_row(t)))

        customer.jar = total_jars
        customer.paid = total_paid
        customer.pending = running_baki
        customer.updated_at = datetime.now()

        # 1. Update customer summary row in 'customers' sheet
        row_idx = customer.row_idx
        if not row_idx:
            async with sheets_lock:
                id_col = await self._retry(self.customers_sheet.col_values, 1)
            for idx, cid in enumerate(id_col):
                if str(cid).strip() == customer_id:
                    row_idx = idx + 1
                    break

        if row_idx and row_idx > 1:
            cust_row_values = self._object_to_customer_row(customer)
            async with sheets_lock:
                await self._retry(
                    self.customers_sheet.update,
                    f"A{row_idx}:M{row_idx}",
                    [cust_row_values],
                    value_input_option="USER_ENTERED"
                )

        # 2. Batch update all transaction rows in 1 SINGLE API call
        if txn_updates:
            batch_data = [{"range": f"A{r}:J{r}", "values": [vals]} for r, vals in txn_updates]
            async with sheets_lock:
                try:
                    await self._retry(self.transactions_sheet.batch_update, batch_data, value_input_option="USER_ENTERED")
                except Exception as e:
                    print(f"Error during transaction batch update: {e}")

        # Invalidate cache so fresh data is returned
        self._invalidate_customers_cache()
        self._invalidate_transactions_cache()

        # 3. Synchronize customer ledger worksheet
        await self.sync_customer_sheet(customer_id)

    async def _find_customer_worksheet_by_id(self, customer_id: str, name: str):
        await self.initialize()
        if not self.initialized:
            return None
        
        if customer_id in self._worksheet_cache:
            sheet = self._worksheet_cache[customer_id]
            try:
                _ = sheet.title
                return sheet
            except:
                del self._worksheet_cache[customer_id]
                
        try:
            all_sheets = await self._get_worksheets_cached()
            sheet_names = [s.title for s in all_sheets if s.title not in ["customers", "transactions"]]
            
            if sheet_names:
                ranges = [f"'{s_name}'!A1" for s_name in sheet_names]
                async with sheets_lock:
                    results = await self._retry(self.spreadsheet.values_batch_get, ranges)
                value_ranges = results.get('valueRanges', [])
                title_to_sheet = {s.title: s for s in all_sheets}
                
                for s_name, item in zip(sheet_names, value_ranges):
                    values = item.get('values', [])
                    if values and values[0] and values[0][0]:
                        val = str(values[0][0]).strip()
                        if val.startswith("ID:"):
                            cid = val.split(":")[1].strip()
                            sheet_obj = title_to_sheet.get(s_name)
                            if sheet_obj:
                                self._worksheet_cache[cid] = sheet_obj
        except Exception as e:
            print(f"Error rebuilding worksheet cache: {e}")
            
        return self._worksheet_cache.get(customer_id)

    async def _get_unique_sheet_title(self, desired_name: str, customer_id: str):
        sanitized_name = desired_name
        for c in [':', '\\', '/', '?', '*', '[', ']']:
            sanitized_name = sanitized_name.replace(c, '')
        sanitized_name = sanitized_name[:90].strip() or "Customer"
        
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

    async def sync_customer_sheet(self, customer_id: str):
        try:
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
                        # Gracefully handle 200 sheets limit
                        print(f"Notice: Individual sheet not created for {new_title} (may have reached max sheets): {e}")
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
                t_dt = t.date if isinstance(t.date, datetime) else datetime.min
                return t_dt
                
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
                    f"{pending_amount:.2f}",
                    f"{paid_amount:.2f}",
                    desc
                ])
                
            async with sheets_lock:
                try:
                    await self._retry(sheet.clear)
                    await self._retry(sheet.update, "A1", rows)
                except Exception as e:
                    print(f"Error updating customer worksheet: {e}")
                    return False
                    
            return True
        except Exception as ex:
            print(f"Non-critical notice in sync_customer_sheet for {customer_id}: {ex}")
            return False


# Global singleton Sheets client
sheets_client = GoogleSheetsClient()
