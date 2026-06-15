import os
import json
import uuid
import threading
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
        self.transactions = []

    def dict(self):
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
            "updated_at": self.updated_at.isoformat() if isinstance(self.updated_at, datetime) else self.updated_at
        }


class TransactionObj:
    def __init__(self, **kwargs):
        self.id = kwargs.get("id", 0)
        self.customer_id = kwargs.get("customer_id", "")
        self.date = kwargs.get("date")
        self.jar_count = kwargs.get("jar_count", 0)
        self.amount = kwargs.get("amount", 0.0)
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
            "description": self.description,
            "created_at": self.created_at.isoformat() if isinstance(self.created_at, datetime) else self.created_at,
            "updated_at": self.updated_at.isoformat() if isinstance(self.updated_at, datetime) else self.updated_at
        }


class GoogleSheetsClient:
    def __init__(self):
        self.client = None
        self.spreadsheet = None
        self.initialized = False
        
        # Worksheet headers definition
        self.customer_headers = [
            "id", "customer_name", "customer_address", "customer_number", 
            "jar", "paid", "pending", "success", "date", "is_monthly", 
            "created_at", "updated_at"
        ]
        self.transaction_headers = [
            "id", "customer_id", "date", "jar_count", "amount", 
            "description", "created_at", "updated_at"
        ]

    def initialize(self):
        if self.initialized:
            return True

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

            self.client = gspread.authorize(creds)
            self.spreadsheet = self.client.open_by_key(sheet_id)
            self._ensure_worksheets()
            self.initialized = True
            print("Google Sheets backend initialized successfully!")
            return True
        except Exception as e:
            print(f"Failed to initialize Google Sheets backend: {e}")
            return False

    def _ensure_worksheets(self):
        # 1. Ensure 'customers' worksheet
        try:
            self.customers_sheet = self.spreadsheet.worksheet("customers")
        except gspread.exceptions.WorksheetNotFound:
            self.customers_sheet = self.spreadsheet.add_worksheet("customers", rows="1000", cols="15")
            self.customers_sheet.append_row(self.customer_headers)

        # 2. Ensure 'transactions' worksheet
        try:
            self.transactions_sheet = self.spreadsheet.worksheet("transactions")
        except gspread.exceptions.WorksheetNotFound:
            self.transactions_sheet = self.spreadsheet.add_worksheet("transactions", rows="5000", cols="10")
            self.transactions_sheet.append_row(self.transaction_headers)

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
            updated_at=self._parse_datetime(row_dict.get("updated_at"))
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

        return TransactionObj(
            id=txn_id,
            customer_id=str(row_dict.get("customer_id", "")),
            date=self._parse_datetime(row_dict.get("date")),
            jar_count=jar_count,
            amount=amount,
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

    def get_all_customers(self, exclude_monthly=False):
        self.initialize()
        if not self.initialized:
            return []
            
        with sheets_lock:
            records = self.customers_sheet.get_all_records()
            
        customers = []
        for r in records:
            cust = self._row_to_customer(r)
            if exclude_monthly and cust.is_monthly:
                continue
            customers.append(cust)
            
        return customers

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

        # Check duplicate
        cust_num = data.get("customer_number")
        if cust_num:
            existing = self.get_all_customers()
            for c in existing:
                if c.customer_number == cust_num:
                    self.update_customer(c.id, data)
                    return c.id

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
            updated_at=now
        )

        row_values = self._object_to_customer_row(customer)
        with sheets_lock:
            self.customers_sheet.append_row(row_values)

        # Create initial transaction log if jar count or paid is recorded
        if customer.jar or customer.paid:
            self.add_transaction({
                "customer_id": doc_id,
                "date": parsed_date,
                "jar_count": customer.jar,
                "amount": customer.paid,
                "description": "Initial Record"
            })

        return doc_id

    def update_customer(self, customer_id: str, data: dict):
        self.initialize()
        if not self.initialized:
            return False

        with sheets_lock:
            records = self.customers_sheet.get_all_records()
            
        row_idx = -1
        target_record = None
        for idx, r in enumerate(records):
            if str(r.get("id")) == customer_id:
                row_idx = idx + 2  # 1-based index + header offset
                target_record = r
                break

        if row_idx == -1:
            return False

        customer = self._row_to_customer(target_record)
        
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
        
        if "date" in data and data["date"]:
            if isinstance(data["date"], datetime):
                customer.date = data["date"]
            else:
                customer.date = self._parse_datetime(data["date"]) or customer.date

        customer.updated_at = now

        row_values = self._object_to_customer_row(customer)
        
        with sheets_lock:
            self.customers_sheet.update(f"A{row_idx}:L{row_idx}", [row_values])

        # Generate transaction log if differences exist
        jar_diff = customer.jar - old_jar
        paid_diff = customer.paid - old_paid

        if jar_diff != 0 or abs(paid_diff) > 0.001:
            description = "Update"
            if jar_diff > 0: description = f"Added {jar_diff} jars"
            if paid_diff > 0: description = f"Paid {paid_diff}"
            if jar_diff > 0 and paid_diff > 0: description = f"Added {jar_diff} jars & Paid {paid_diff}"

            self.add_transaction({
                "customer_id": customer_id,
                "date": customer.date or now,
                "jar_count": jar_diff,
                "amount": paid_diff,
                "description": description
            })

        return True

    def delete_customer(self, customer_id: str):
        self.initialize()
        if not self.initialized:
            return False

        with sheets_lock:
            records = self.customers_sheet.get_all_records()
            
        row_idx = -1
        for idx, r in enumerate(records):
            if str(r.get("id")) == customer_id:
                row_idx = idx + 2
                break

        if row_idx == -1:
            return False

        with sheets_lock:
            self.customers_sheet.delete_rows(row_idx)

        # Cascading delete transactions
        self.delete_transactions_by_customer(customer_id)
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

    def get_all_transactions(self):
        self.initialize()
        if not self.initialized:
            return []

        with sheets_lock:
            records = self.transactions_sheet.get_all_records()

        transactions = []
        for r in records:
            transactions.append(self._row_to_transaction(r))

        return transactions

    def get_transactions_by_customer(self, customer_id: str):
        txns = self.get_all_transactions()
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

        txn = TransactionObj(
            id=next_id,
            customer_id=data.get("customer_id", ""),
            date=parsed_date,
            jar_count=int(data.get("jar_count", 0)),
            amount=float(data.get("amount", 0.0)),
            description=data.get("description", ""),
            created_at=now,
            updated_at=now
        )

        row_values = self._object_to_transaction_row(txn)
        with sheets_lock:
            self.transactions_sheet.append_row(row_values)

        return txn

    def update_transaction(self, txn_id: int, data: dict):
        self.initialize()
        if not self.initialized:
            return None

        with sheets_lock:
            records = self.transactions_sheet.get_all_records()

        row_idx = -1
        target_record = None
        for idx, r in enumerate(records):
            try:
                curr_id = int(r.get("id"))
            except:
                curr_id = r.get("id")
                
            if curr_id == txn_id:
                row_idx = idx + 2
                target_record = r
                break

        if row_idx == -1:
            return None

        txn = self._row_to_transaction(target_record)
        now = datetime.now()

        if "date" in data and data["date"]:
            if isinstance(data["date"], datetime):
                txn.date = data["date"]
            else:
                txn.date = self._parse_datetime(data["date"]) or txn.date
        if "jar_count" in data: txn.jar_count = int(data["jar_count"])
        if "amount" in data: txn.amount = float(data["amount"])
        if "description" in data: txn.description = data["description"]
        txn.updated_at = now

        row_values = self._object_to_transaction_row(txn)
        with sheets_lock:
            self.transactions_sheet.update(f"A{row_idx}:H{row_idx}", [row_values])

        return txn

    def delete_transaction(self, txn_id: int):
        self.initialize()
        if not self.initialized:
            return False

        with sheets_lock:
            records = self.transactions_sheet.get_all_records()

        row_idx = -1
        for idx, r in enumerate(records):
            try:
                curr_id = int(r.get("id"))
            except:
                curr_id = r.get("id")

            if curr_id == txn_id:
                row_idx = idx + 2
                break

        if row_idx == -1:
            return False

        with sheets_lock:
            self.transactions_sheet.delete_rows(row_idx)

        return True

    def delete_transactions_by_customer(self, customer_id: str):
        self.initialize()
        if not self.initialized:
            return False

        # Read transactions and delete matched rows
        # To avoid index shifting problems, we delete rows from bottom to top
        with sheets_lock:
            records = self.transactions_sheet.get_all_records()
            
        rows_to_delete = []
        for idx, r in enumerate(records):
            if str(r.get("customer_id")) == customer_id:
                rows_to_delete.append(idx + 2)

        if not rows_to_delete:
            return True

        # Sort descending to delete from bottom up without shifting remaining targets
        rows_to_delete.sort(reverse=True)
        with sheets_lock:
            for r_idx in rows_to_delete:
                self.transactions_sheet.delete_rows(r_idx)

        return True


# Instantiate singleton Sheets client
sheets_client = GoogleSheetsClient()
