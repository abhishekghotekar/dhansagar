from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from app.schemas.customer_schema import Customer, CustomerCreate, DailyEntryCreate, DailyEntryUpdate, CustomerUpdate
from app.db.sheets import sheets_client
from app.services.vector_service import (
    store_customer,
    update_customer,
    search_customer,
    get_all_customers,
    delete_customer
)
from datetime import datetime, timezone, timedelta
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
async def add_customer(customer: CustomerCreate):
    try:
        data = customer.model_dump() if hasattr(customer, "model_dump") else customer.dict()
        doc_id = await store_customer(data)
        return {
            "message": "Customer stored successfully",
            "id": doc_id
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ✏️ Update customer
@router.put("/update/{doc_id}")
async def update(doc_id: str, customer: CustomerUpdate):
    try:
        data = customer.model_dump(exclude_unset=True) if hasattr(customer, "model_dump") else customer.dict(exclude_unset=True)
        success = await update_customer(doc_id, data)
        if not success:
            raise HTTPException(status_code=404, detail="Customer not found")
        
        return {"message": "Customer updated successfully"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# 🔍 Search customer
@router.get("/search")
async def search(q: str):
    if not q:
        return []
    results = await search_customer(q)
    return results


# 📄 Get all customers
@router.get("/all-customers")
async def get_all(force_refresh: bool = False):
    data = await get_all_customers(exclude_monthly=False, force_refresh=force_refresh)
    return [c.dict() for c in data]


# 👤 Get single customer detail
@router.get("/customer/{customer_id}")
async def get_customer_detail(customer_id: str, force_refresh: bool = False):
    try:
        if force_refresh:
            sheets_client._invalidate_customers_cache()
            sheets_client._invalidate_transactions_cache()

        customer = await sheets_client.get_customer(customer_id, force_refresh=force_refresh)
        if not customer:
            raise HTTPException(status_code=404, detail="Customer not found")

        def safe_sort_key(t):
            return t.date if isinstance(t.date, datetime) else datetime.min

        txns = sorted(
            customer.transactions,
            key=safe_sort_key,
            reverse=True
        )

        rate = getattr(customer, 'rate', 25.0) or 25.0
        total_jars = sum(t.jar_count for t in txns)
        total_paid = sum(t.amount for t in txns if t.amount > 0)
        total_cost = total_jars * rate

        delivery_records = []
        for t in txns:
            delivery_records.append({
                "id": str(t.id),
                "date": safe_date_str(t.date),
                "jar": t.jar_count,
                "jama": t.amount,
                "paid": t.amount,
                "baki": t.manual_bill,
                "pending": t.manual_bill,
                "running_balance": t.pending,
                "description": t.description or "",
                "created_at": safe_iso(t.created_at),
                "updated_at": safe_iso(t.updated_at)
            })

        return {
            "id": customer.id,
            "customer_name": customer.customer_name,
            "customer_address": customer.customer_address,
            "customer_number": customer.customer_number,
            "rate": rate,
            "success": customer.success,
            "date": safe_date_str(customer.date),
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


# 🔄 Refresh data from Google Sheets
@router.get("/refresh")
async def refresh_data():
    try:
        sheets_client._invalidate_customers_cache()
        sheets_client._invalidate_transactions_cache()

        all_customers = await get_all_customers(exclude_monthly=False, force_refresh=True)

        result = [c.dict() for c in all_customers]
        return {
            "message": "All customer data with full history refreshed from Google Sheets",
            "total_customers": len(result),
            "customers": result
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Refresh failed: {str(e)}")


async def _process_add_daily_entry(target_cust_id: str, entry: DailyEntryCreate):
    if not target_cust_id:
        target_cust_id = entry.customer_id
    if not target_cust_id:
        raise HTTPException(status_code=400, detail="Customer ID is required")

    customer = await sheets_client.get_customer(target_cust_id)
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")

    now_iso = datetime.now(timezone(timedelta(hours=5, minutes=30))).strftime("%Y-%m-%d")
    target_date_str = entry.date or now_iso
    target_date_clean = target_date_str.split("T")[0].split(" ")[0].strip() if target_date_str else ""

    existing_txn = None
    all_cust_txns = await sheets_client.get_transactions_by_customer(target_cust_id)

    # 1. Match by explicit record_id if provided
    if getattr(entry, "record_id", None):
        for t in all_cust_txns:
            if str(t.id).strip() == str(entry.record_id).strip():
                existing_txn = t
                break

    # 2. Match by date — find any existing record for this date
    has_initial_on_date = False  # track if Initial Record exists on target date
    if not existing_txn and target_date_clean:
        for t in all_cust_txns:
            desc = (t.description or "").strip()
            if t.date:
                t_date_str = t.date.strftime("%Y-%m-%d") if isinstance(t.date, datetime) else str(t.date).split("T")[0].split(" ")[0].strip()
                if t_date_str == target_date_clean:
                    if desc in ["Initial Record", "Opening Balance"]:
                        # Remember there is an initial record on this date
                        has_initial_on_date = True
                        # If the client explicitly wants to edit this record by date, let them
                        # (only if entry description is also an initial-type)
                        req_desc = (entry.description or "").strip()
                        if req_desc in ["Initial Record", "Opening Balance"]:
                            existing_txn = t
                        # else: do NOT overwrite initial record with a delivery log
                    else:
                        existing_txn = t
                    if existing_txn:
                        break

    # 3. If the only record on target_date is an Initial Record and we're adding a Delivery Log,
    #    treat it as a NEW entry (don't overwrite), BUT check for idempotency in add_transaction
    if existing_txn:
        update_data = {
            "date": entry.date or safe_iso(existing_txn.date),
            "jar_count": entry.jar,
            "amount": entry.paid,
            "manual_bill": entry.pending,
            "description": entry.description or existing_txn.description or "Daily Delivery"
        }
        txn = await sheets_client.update_transaction(str(existing_txn.id), update_data)
        message = "Daily entry updated successfully"
    else:
        txn = await sheets_client.add_transaction({
            "customer_id": target_cust_id,
            "date": entry.date,
            "jar_count": entry.jar,
            "amount": entry.paid,
            "manual_bill": entry.pending,
            "description": entry.description or "Daily Delivery"
        })
        message = "Daily entry added successfully"

    updated_customer = await sheets_client.get_customer(target_cust_id, force_refresh=True)

    return {
        "message": message,
        "record": {
            "id": str(txn.id),
            "date": safe_iso(txn.date),
            "jar": txn.jar_count,
            "jama": txn.amount,
            "paid": txn.amount,
            "baki": txn.manual_bill,
            "pending": txn.manual_bill,
            "running_balance": txn.pending,
            "description": txn.description or "",
            "created_at": safe_iso(getattr(txn, "created_at", None)),
            "updated_at": safe_iso(getattr(txn, "updated_at", None))
        },
        "customer_summary": {
            "total_jars": updated_customer.jar if updated_customer else 0,
            "total_paid": updated_customer.paid if updated_customer else 0.0,
            "total_pending": updated_customer.pending if updated_customer else 0.0
        }
    }


# ➕ Add / Update daily entry (by path)
@router.post("/customer/{customer_id}/add-entry")
@router.post("/customer/{customer_id}/add-record")
@router.post("/customer/{customer_id}/update-entry")
@router.put("/customer/{customer_id}/update-entry")
async def add_daily_entry_by_customer_id(customer_id: str, entry: DailyEntryCreate):
    try:
        return await _process_add_daily_entry(customer_id, entry)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error processing entry: {str(e)}")


# ➕ Add / Update daily entry (by body)
@router.post("/add-entry")
@router.post("/add-record")
@router.post("/update-entry")
@router.put("/update-entry")
async def add_daily_entry_generic(entry: DailyEntryCreate):
    try:
        return await _process_add_daily_entry(entry.customer_id, entry)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error processing entry: {str(e)}")


async def _process_update_daily_entry(record_id: str, entry: DailyEntryUpdate, customer_id: str = None):
    update_dict = entry.model_dump(exclude_unset=True) if hasattr(entry, "model_dump") else entry.dict(exclude_unset=True)
    if "jar" in update_dict and update_dict["jar"] is not None:
        update_dict["jar_count"] = update_dict.pop("jar")
    if "paid" in update_dict and update_dict["paid"] is not None:
        update_dict["amount"] = update_dict.pop("paid")
    if "pending" in update_dict and update_dict["pending"] is not None:
        update_dict["manual_bill"] = update_dict.pop("pending")
    
    target_cust_id = customer_id or entry.customer_id
    target_rec_id = record_id or entry.record_id

    txn = None
    if target_rec_id and target_rec_id != "auto":
        txn = await sheets_client.update_transaction(target_rec_id, update_dict)
    
    if not txn and target_cust_id and entry.date:
        target_date_clean = entry.date.split("T")[0].split(" ")[0].strip()
        all_cust_txns = await sheets_client.get_transactions_by_customer(target_cust_id)
        for t in all_cust_txns:
            if t.date:
                t_date_str = t.date.strftime("%Y-%m-%d") if isinstance(t.date, datetime) else str(t.date).split("T")[0].split(" ")[0].strip()
                if t_date_str == target_date_clean:
                    txn = await sheets_client.update_transaction(str(t.id), update_dict)
                    break

    if not txn:
        raise HTTPException(status_code=404, detail="Record not found")

    final_cust_id = txn.customer_id or target_cust_id
    updated_customer = await sheets_client.get_customer(final_cust_id, force_refresh=True) if final_cust_id else None

    return {
        "message": "Record updated successfully",
        "record": {
            "id": str(txn.id),
            "date": safe_iso(txn.date),
            "jar": txn.jar_count,
            "jama": txn.amount,
            "paid": txn.amount,
            "baki": txn.manual_bill,
            "pending": txn.manual_bill,
            "running_balance": txn.pending,
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
async def update_daily_entry_by_customer_id(customer_id: str, record_id: str, entry: DailyEntryUpdate):
    try:
        return await _process_update_daily_entry(record_id, entry, customer_id=customer_id)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error updating record: {str(e)}")


# ✏️ Update a specific date-wise record (by id)
@router.put("/record/{record_id}")
async def update_daily_entry_by_id(record_id: str, entry: DailyEntryUpdate):
    try:
        return await _process_update_daily_entry(record_id, entry)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error updating record: {str(e)}")


async def _process_delete_daily_entry(record_id: str, customer_id: str = None):
    target_cust_id = customer_id
    if not target_cust_id:
        txns = await sheets_client.get_all_transactions()
        for t in txns:
            if str(t.id).strip() == str(record_id).strip():
                target_cust_id = t.customer_id
                break

    success = await sheets_client.delete_transaction(record_id)
    if not success:
        raise HTTPException(status_code=404, detail="Record not found")

    updated_customer = await sheets_client.get_customer(target_cust_id, force_refresh=True) if target_cust_id else None

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
async def delete_daily_entry_by_customer_id(customer_id: str, record_id: str):
    try:
        return await _process_delete_daily_entry(record_id, customer_id=customer_id)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error deleting record: {str(e)}")


# ❌ Delete a specific date-wise record (by id)
@router.delete("/record/{record_id}")
async def delete_daily_entry_by_id(record_id: str):
    try:
        return await _process_delete_daily_entry(record_id)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error deleting record: {str(e)}")


# 📅 Get customer records DATE BY DATE
@router.get("/customer/{customer_id}/records")
async def get_customer_records_by_date(
    customer_id: str,
    from_date: str = None,
    to_date: str   = None,
    order: str     = "desc"
):
    try:
        customer = await sheets_client.get_customer(customer_id)
        if not customer:
            raise HTTPException(status_code=404, detail="Customer not found")

        txns = list(customer.transactions)

        def _parse_filter_date(date_str: str) -> datetime:
            for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%d-%m-%Y", "%d/%m/%Y"):
                try:
                    return datetime.strptime(date_str.strip(), fmt)
                except ValueError:
                    continue
            raise HTTPException(
                status_code=400,
                detail=f"Invalid date format '{date_str}'. Use YYYY-MM-DD"
            )

        if from_date:
            fd = _parse_filter_date(from_date)
            txns = [t for t in txns if t.date and (t.date.date() if isinstance(t.date, datetime) else datetime.min.date()) >= fd.date()]

        if to_date:
            td = _parse_filter_date(to_date)
            txns = [t for t in txns if t.date and (t.date.date() if isinstance(t.date, datetime) else datetime.max.date()) <= td.date()]

        reverse_order = (order.lower() == "desc")
        def safe_sort_key(t):
            return t.date if isinstance(t.date, datetime) else datetime.min

        txns.sort(key=safe_sort_key, reverse=reverse_order)

        from collections import OrderedDict
        grouped: dict = OrderedDict()
        for t in txns:
            date_key = safe_date_str(t.date) if t.date else "Unknown"
            if date_key not in grouped:
                grouped[date_key] = []
            grouped[date_key].append({
                "id": str(t.id),
                "jar": t.jar_count,
                "jama": t.amount,
                "paid": t.amount,
                "baki": t.manual_bill,
                "pending": t.manual_bill,
                "running_balance": t.pending,
                "description": t.description or "",
                "created_at": safe_iso(t.created_at)
            })

        rate = getattr(customer, 'rate', 25.0) or 25.0
        total_jars = sum(t.jar_count for t in txns)
        total_paid = sum(t.amount for t in txns if t.amount > 0)
        total_cost = total_jars * rate

        # Accurate date range calculation
        if txns:
            valid_dates = [t.date for t in txns if isinstance(t.date, datetime)]
            min_date_str = safe_date_str(min(valid_dates)) if valid_dates else None
            max_date_str = safe_date_str(max(valid_dates)) if valid_dates else None
        else:
            min_date_str = None
            max_date_str = None

        return {
            "customer_id": customer_id,
            "customer_name": customer.customer_name,
            "customer_number": customer.customer_number,
            "customer_address": customer.customer_address,
            "total_records": len(txns),
            "date_range": {
                "from": from_date or min_date_str,
                "to":   to_date   or max_date_str
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


# 📥 Download PDF report
@router.get("/download-pdf")
async def download_pdf():
    try:
        customers = await get_all_customers(exclude_monthly=False)
        buffer = io.BytesIO()
        doc = SimpleDocTemplate(
            buffer,
            pagesize=landscape(A4),
            leftMargin=30,
            rightMargin=30,
            topMargin=30,
            bottomMargin=30
        )
        elements = []
        
        styles = getSampleStyleSheet()
        elements.append(Paragraph("Dhansagar - Customer Ledger Report", styles['Title']))

        normal_style = styles['Normal']
        normal_style.fontSize = 8
        normal_style.leading = 10

        header_style = styles['Normal']
        header_style.fontSize = 9
        header_style.fontName = 'Helvetica-Bold'
        header_style.textColor = colors.whitesmoke

        data = [[
            Paragraph("Date", header_style),
            Paragraph("Name", header_style),
            Paragraph("Address", header_style),
            Paragraph("Phone", header_style),
            Paragraph("Status", header_style),
            Paragraph("Jars", header_style),
            Paragraph("Paid", header_style),
            Paragraph("Pending", header_style)
        ]]
        
        for c in customers:
            date_str = safe_date_str(c.date)
            status = "Active" if c.success else "Pending"
            row = [
                Paragraph(date_str or "", normal_style),
                Paragraph(str(c.customer_name or ""), normal_style),
                Paragraph(str(c.customer_address or ""), normal_style),
                Paragraph(str(c.customer_number or ""), normal_style),
                Paragraph(status, normal_style),
                Paragraph(str(c.jar or 0), normal_style),
                Paragraph(f"Rs. {c.paid or 0.0}", normal_style),
                Paragraph(f"Rs. {c.pending or 0.0}", normal_style)
            ]
            data.append(row)
            
        # Total printable width is approx 780 for landscape A4
        col_widths = [75, 110, 210, 95, 60, 50, 90, 90]
        table = Table(data, colWidths=col_widths)
        table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor("#1A2B4C")),
            ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 6),
            ('TOPPADDING', (0, 0), (-1, 0), 6),
            ('BACKGROUND', (0, 1), (-1, -1), colors.HexColor("#F8F9FA")),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor("#D0D5DD")),
        ]))
        elements.append(table)
        
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
async def delete(doc_id: str):
    try:
        success = await delete_customer(doc_id)
        if not success:
            raise HTTPException(status_code=404, detail="Customer not found")
        return {"message": "Customer deleted successfully"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
