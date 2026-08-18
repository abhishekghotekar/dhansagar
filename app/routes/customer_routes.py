from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from app.schemas.customer_schema import Customer, DailyEntryCreate, DailyEntryUpdate
from app.db.postgres import get_db
from app.db.sheets import sheets_client
from app.services.vector_service import (
    store_customer,
    update_customer,
    search_customer,
    get_all_customers,
    delete_customer
)
from datetime import datetime
import io
from reportlab.lib import colors
from reportlab.lib.pagesizes import landscape, A4
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph
from reportlab.lib.styles import getSampleStyleSheet

router = APIRouter()

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


# 🟢 Store customer
@router.post("/add-customer")
def add_customer(customer: Customer, db = Depends(get_db)):
    try:
        doc_id = store_customer(customer.dict(), db)
        return {
            "message": "Customer stored successfully",
            "id": doc_id
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ✏️ Update customer
@router.put("/update/{doc_id}")
def update(doc_id: str, customer: Customer, db = Depends(get_db)):
    try:
        success = update_customer(doc_id, customer.dict(exclude_unset=True), db)
        if not success:
            raise HTTPException(status_code=404, detail="Customer not found")
        
        return {"message": "Customer updated successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# 🔍 Search customer (AI search)
@router.get("/search")
def search(q: str, db = Depends(get_db)):
    if not q:
        return []
    results = search_customer(q, db)
    return results


# 📄 Get all customers — Monthly customers included!
@router.get("/all-customers")
def get_all(force_refresh: bool = False, db = Depends(get_db)):
    data = get_all_customers(db, exclude_monthly=False, force_refresh=force_refresh)
    return [c.dict() for c in data]


# 👤 Get single customer detail — all customers included
@router.get("/customer/{customer_id}")
def get_customer_detail(customer_id: str, force_refresh: bool = False, db = Depends(get_db)):
    try:
        if force_refresh:
            sheets_client._invalidate_customers_cache()
            sheets_client._invalidate_transactions_cache()

        customer = sheets_client.get_customer(customer_id)
        if not customer:
            raise HTTPException(status_code=404, detail="Customer not found")

        # Sort stably by date and ID (latest first)
        def safe_sort_key(t):
            try:
                tid = int(t.id)
            except:
                tid = 0
            t_dt = t.date if isinstance(t.date, datetime) else datetime.min
            return (t_dt, tid)
        txns = sorted(
            customer.transactions,
            key=safe_sort_key,
            reverse=True
        )

        rate = getattr(customer, 'rate', 25.0) or 25.0
        total_jars = sum(t.jar_count for t in txns)
        total_paid = sum(t.amount for t in txns if t.amount > 0)
        total_pending = customer.pending
        total_cost = total_jars * rate

        delivery_records = []
        for t in txns:
            delivery_records.append({
                "id": str(t.id),
                "date": safe_iso(t.date),
                "jar": t.jar_count,
                "jama": t.amount,
                "baki": t.manual_bill,
                "description": t.description or "",
                "created_at": safe_iso(t.created_at),
                "updated_at": safe_iso(t.updated_at)
            })

        return {
            "id": customer.id,
            "customer_name": customer.customer_name,
            "customer_address": customer.customer_address,
            "customer_number": customer.customer_number,
            "success": customer.success,
            "date": safe_iso(customer.date),
            "created_at": safe_iso(customer.created_at),
            "updated_at": safe_iso(customer.updated_at),
            "delivery_records": delivery_records,
            "summary": {
                "total_jars": total_jars,
                "total_paid": total_paid,
                "total_cost": total_cost,
                "total_pending": customer.pending
            }
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching customer: {str(e)}")


# 🔄 Refresh: Cache invalidate karo aani Google Sheets madhun ALL data with full history fetch karo
@router.get("/refresh")
def refresh_data(db = Depends(get_db)):
    """
    Memory cache clear karto aani Google Sheets madhun saglya customers chi
    full history (transactions sahit) fetch karto.
    Reload kelyanavar fresh latest data milato.
    """
    try:
        # 1. Cache completely invalidate karo
        sheets_client._invalidate_customers_cache()
        sheets_client._invalidate_transactions_cache()

        # 2. Force refresh - ALL customers + their transactions fresh fetch
        all_customers = get_all_customers(db, exclude_monthly=False, force_refresh=True)

        # 3. Recalculate balances to ensure Jama is properly subtracted from Baki
        for c in all_customers:
            try:
                sheets_client.recalculate_customer_balances(c.id)
            except Exception as e:
                print(f"Error recalculating customer balance for {c.id}: {e}")

        # Fetch updated customers list after recalculating
        all_customers = get_all_customers(db, exclude_monthly=False, force_refresh=True)

        # 4. Pratek customer chi complete history (transactions sahit) return karo
        result = []
        for c in all_customers:
            customer_dict = c.dict()  # transactions already included via CustomerObj.dict()
            result.append(customer_dict)

        return {
            "message": "All customer data with full history refreshed from Google Sheets",
            "total_customers": len(result),
            "customers": result
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Refresh failed: {str(e)}")


def _process_add_daily_entry(target_cust_id: str, entry: DailyEntryCreate):
    if not target_cust_id:
        raise HTTPException(status_code=400, detail="Customer ID is required")

    customer = sheets_client.get_customer(target_cust_id)
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")

    txn = sheets_client.add_transaction({
        "customer_id": target_cust_id,
        "date": entry.date,
        "jar_count": entry.jar_count,
        "amount": entry.amount,
        "manual_bill": entry.pending,
        "description": entry.description or "Daily Delivery"
    })

    # Fetch updated customer summary
    updated_customer = sheets_client.get_customer(target_cust_id, force_refresh=True)

    return {
        "message": "Daily entry added successfully",
        "record": {
            "id": str(txn.id),
            "date": safe_iso(txn.date),
            "jar": txn.jar_count,
            "jama": txn.amount,
            "baki": txn.manual_bill,
            "description": txn.description or "",
            "created_at": safe_iso(txn.created_at)
        },
        "customer_summary": {
            "total_jars": updated_customer.jar if updated_customer else 0,
            "total_paid": updated_customer.paid if updated_customer else 0.0,
            "total_pending": updated_customer.pending if updated_customer else 0.0
        }
    }


# ➕ Add daily date-wise entry (by path)
@router.post("/customer/{customer_id}/add-entry")
@router.post("/customer/{customer_id}/add-record")
def add_daily_entry_by_customer_id(customer_id: str, entry: DailyEntryCreate, db = Depends(get_db)):
    """
    Tarikh-wise jar delivery aani jama (paid amount) chi entry add karto (via path customer_id).
    """
    try:
        return _process_add_daily_entry(customer_id, entry)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error adding entry: {str(e)}")


# ➕ Add daily date-wise entry (by body)
@router.post("/add-entry")
@router.post("/add-record")
def add_daily_entry_generic(entry: DailyEntryCreate, db = Depends(get_db)):
    """
    Body madhil customer_id varun entry add karto.
    """
    try:
        return _process_add_daily_entry(entry.customer_id, entry)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error adding entry: {str(e)}")


def _process_update_daily_entry(record_id: int, entry: DailyEntryUpdate, customer_id: str = None):
    update_dict = entry.dict(exclude_unset=True)
    if "pending" in update_dict:
        update_dict["manual_bill"] = update_dict.pop("pending")
    
    txn = sheets_client.update_transaction(record_id, update_dict)
    if not txn:
        raise HTTPException(status_code=404, detail="Record not found")

    target_cust_id = txn.customer_id or customer_id
    updated_customer = sheets_client.get_customer(target_cust_id, force_refresh=True) if target_cust_id else None

    return {
        "message": "Record updated successfully",
        "record": {
            "id": str(txn.id),
            "date": safe_iso(txn.date),
            "jar": txn.jar_count,
            "jama": txn.amount,
            "baki": txn.manual_bill,
            "description": txn.description or "",
            "updated_at": safe_iso(txn.updated_at)
        },
        "customer_summary": {
            "total_jars": updated_customer.jar if updated_customer else 0,
            "total_paid": updated_customer.paid if updated_customer else 0.0,
            "total_pending": updated_customer.pending if updated_customer else 0.0
        } if updated_customer else None
    }


# ✏️ Update a specific date-wise record (by path)
@router.put("/customer/{customer_id}/record/{record_id}")
def update_daily_entry_by_customer_id(customer_id: str, record_id: int, entry: DailyEntryUpdate, db = Depends(get_db)):
    """
    Ek specific tarikhchi delivery record update karto aani balance recalculate karto.
    """
    try:
        return _process_update_daily_entry(record_id, entry, customer_id=customer_id)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error updating record: {str(e)}")


# ✏️ Update a specific date-wise record (by id)
@router.put("/record/{record_id}")
def update_daily_entry_by_id(record_id: int, entry: DailyEntryUpdate, db = Depends(get_db)):
    """
    Record ID varun delivery record update karto.
    """
    try:
        return _process_update_daily_entry(record_id, entry)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error updating record: {str(e)}")


def _process_delete_daily_entry(record_id: int, customer_id: str = None):
    target_cust_id = customer_id
    if not target_cust_id:
        txns = sheets_client.get_all_transactions()
        for t in txns:
            try:
                curr_id = int(t.id)
            except:
                curr_id = t.id
            if curr_id == record_id:
                target_cust_id = t.customer_id
                break

    success = sheets_client.delete_transaction(record_id)
    if not success:
        raise HTTPException(status_code=404, detail="Record not found")

    updated_customer = sheets_client.get_customer(target_cust_id, force_refresh=True) if target_cust_id else None

    return {
        "message": "Record deleted successfully",
        "customer_summary": {
            "total_jars": updated_customer.jar if updated_customer else 0,
            "total_paid": updated_customer.paid if updated_customer else 0.0,
            "total_pending": updated_customer.pending if updated_customer else 0.0
        } if updated_customer else None
    }


# ❌ Delete a specific date-wise record (by path)
@router.delete("/customer/{customer_id}/record/{record_id}")
def delete_daily_entry_by_customer_id(customer_id: str, record_id: int, db = Depends(get_db)):
    """
    Ek specific tarikhchi delivery record delete karto.
    """
    try:
        return _process_delete_daily_entry(record_id, customer_id=customer_id)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error deleting record: {str(e)}")


# ❌ Delete a specific date-wise record (by id)
@router.delete("/record/{record_id}")
def delete_daily_entry_by_id(record_id: int, db = Depends(get_db)):
    """
    Record ID varun delivery record delete karto.
    """
    try:
        return _process_delete_daily_entry(record_id)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error deleting record: {str(e)}")


# 📅 Get customer records DATE BY DATE (chronological order)
@router.get("/customer/{customer_id}/records")
def get_customer_records_by_date(
    customer_id: str,
    from_date: str = None,   # Optional: filter from date (YYYY-MM-DD)
    to_date: str   = None,   # Optional: filter to date (YYYY-MM-DD)
    order: str     = "desc", # "asc" = oldest first, "desc" = latest first
    db = Depends(get_db)
):
    """
    Customer ID varun tyachi date-by-date sagli records returns karto.
    - Chronological order madhe sorted (date wise)
    - Optional: from_date aani to_date filter
    - Optional: order=asc (oldest first) or order=desc (latest first)

    Example:
      GET /customer/{id}/records
      GET /customer/{id}/records?order=asc
      GET /customer/{id}/records?from_date=2026-06-01&to_date=2026-06-30
    """
    try:
        customer = sheets_client.get_customer(customer_id)
        if not customer:
            raise HTTPException(status_code=404, detail="Customer not found")

        txns = list(customer.transactions)

        # --- Flexible date parser: accepts YYYY-MM-DD, YYYY/MM/DD, DD-MM-YYYY, DD/MM/YYYY ---
        def _parse_date(date_str: str) -> datetime:
            for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%d-%m-%Y", "%d/%m/%Y"):
                try:
                    return datetime.strptime(date_str.strip(), fmt)
                except ValueError:
                    continue
            raise HTTPException(
                status_code=400,
                detail=f"Invalid date format '{date_str}'. Use YYYY-MM-DD or YYYY/MM/DD"
            )

        # --- Date range filter ---
        if from_date:
            fd = _parse_date(from_date)
            txns = [t for t in txns if t.date and (t.date.date() if isinstance(t.date, datetime) else datetime.min.date()) >= fd.date()]

        if to_date:
            td = _parse_date(to_date)
            txns = [t for t in txns if t.date and (t.date.date() if isinstance(t.date, datetime) else datetime.max.date()) <= td.date()]


        # --- Date-wise sort ---
        reverse_order = (order.lower() == "desc")
        def safe_sort_key(t):
            try:
                tid = int(t.id)
            except:
                tid = 0
            t_dt = t.date if isinstance(t.date, datetime) else datetime.min
            return (t_dt, tid)
        txns.sort(key=safe_sort_key, reverse=reverse_order)

        # --- Group by date ---
        from collections import OrderedDict
        grouped: dict = OrderedDict()
        for t in txns:
            date_key = safe_date_str(t.date) if t.date else "Unknown"
            if date_key not in grouped:
                grouped[date_key] = []
            grouped[date_key].append({
                "id": str(t.id),
                "jar": t.jar_count,
                "jama": t.amount,       # Jama = Paid
                "baki": t.manual_bill,  # Baki = Manual Bill added in this entry
                "description": t.description or "",
                "created_at": safe_iso(t.created_at)
            })

        # --- Running totals ---
        rate = getattr(customer, 'rate', 25.0) or 25.0
        total_jars   = sum(t.jar_count for t in txns)
        total_paid   = sum(t.amount for t in txns if t.amount > 0)
        total_pending = customer.pending
        total_cost   = total_jars * rate

        return {
            "customer_id": customer_id,
            "customer_name": customer.customer_name,
            "customer_number": customer.customer_number,
            "customer_address": customer.customer_address,
            "total_records": len(txns),
            "date_range": {
                "from": from_date or (safe_date_str(txns[-1].date) if txns else None),
                "to":   to_date   or (safe_date_str(txns[0].date) if txns else None)
            },
            "records_by_date": [
                {
                    "date": date_key,
                    "entries": entries
                }
                for date_key, entries in grouped.items()
            ],
            "summary": {
                "total_jars":    total_jars,
                "total_paid":    total_paid,
                "total_cost":    total_cost,
                "total_pending": customer.pending
            }
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching records: {str(e)}")




# 📥 Download PDF — all customers included!
@router.get("/download-pdf")
def download_pdf(db = Depends(get_db)):
    try:
        # 1. Fetch data
        customers = get_all_customers(db, exclude_monthly=False)
        
        # 2. Create Buffer
        buffer = io.BytesIO()
        
        # 3. Create PDF Doc
        doc = SimpleDocTemplate(buffer, pagesize=landscape(A4))
        elements = []
        
        styles = getSampleStyleSheet()
        elements.append(Paragraph("Customer Data Report", styles['Title']))
        
        # 4. Table Data
        data = [['Date', 'Name', 'Address', 'Phone', 'Status', 'Jars', 'Paid', 'Pending']]
        
        for c in customers:
            date_str = safe_date_str(c.date)
            status = "Active" if c.success else "Pending"
            row = [
                date_str,
                c.customer_name,
                (c.customer_address[:30] + '...') if c.customer_address and len(c.customer_address) > 30 else (c.customer_address or ""),
                c.customer_number,
                status,
                str(c.jar),
                f"Rs. {c.paid}",
                f"Rs. {c.pending}"
            ]
            data.append(row)
            
        # 5. Table Style
        table = Table(data)
        style = TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.grey),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
            ('BACKGROUND', (0, 1), (-1, -1), colors.beige),
            ('GRID', (0, 0), (-1, -1), 1, colors.black),
        ])
        table.setStyle(style)
        elements.append(table)
        
        # 6. Build
        doc.build(elements)
        
        buffer.seek(0)
        return StreamingResponse(
            buffer,
            media_type="application/pdf",
            headers={"Content-Disposition": "attachment; filename=customers_report.pdf"}
        )
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error generating PDF: {str(e)}")


# ❌ Delete customer
@router.delete("/delete/{doc_id}")
def delete(doc_id: str, db = Depends(get_db)):
    delete_customer(doc_id, db)
    return {"message": "Customer deleted"}

