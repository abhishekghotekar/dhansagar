from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from app.schemas.customer_schema import Customer
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


# 📄 Get all customers — Monthly customers excluded (use /monthly/customers for those)
@router.get("/all-customers")
def get_all(force_refresh: bool = False, db = Depends(get_db)):
    # Always exclude_monthly=True — monthly customers are in /monthly/customers
    data = get_all_customers(db, exclude_monthly=True, force_refresh=force_refresh)
    return [c.dict() for c in data]


# 👤 Get single customer detail — Monthly customers excluded
@router.get("/customer/{customer_id}")
def get_customer_detail(customer_id: str, force_refresh: bool = False, db = Depends(get_db)):
    try:
        if force_refresh:
            sheets_client._invalidate_customers_cache()
            sheets_client._invalidate_transactions_cache()

        customer = sheets_client.get_customer(customer_id)
        if not customer or customer.is_monthly:
            raise HTTPException(status_code=404, detail="Customer not found")

        # Transactions sort karo by date (latest first)
        txns = sorted(
            customer.transactions,
            key=lambda t: t.date if t.date else datetime.min,
            reverse=True
        )

        total_jars = sum(t.jar_count for t in txns)
        total_paid = sum(t.amount for t in txns if t.amount > 0 and t.jar_count == 0)
        total_cost = sum(abs(t.amount) for t in txns if t.jar_count > 0 or (t.jar_count == 0 and t.amount < 0))

        delivery_records = []
        for t in txns:
            delivery_records.append({
                "id": str(t.id),
                "date": t.date.isoformat() if t.date else None,
                "jar": t.jar_count,
                "jama": t.amount,
                "baki": t.pending,
                "description": t.description or "",
                "created_at": t.created_at.isoformat() if t.created_at else None,
                "updated_at": t.updated_at.isoformat() if t.updated_at else None
            })

        return {
            "id": customer.id,
            "customer_name": customer.customer_name,
            "customer_address": customer.customer_address,
            "customer_number": customer.customer_number,
            "success": customer.success,
            "date": customer.date.isoformat() if customer.date else None,
            "created_at": customer.created_at.isoformat() if customer.created_at else None,
            "updated_at": customer.updated_at.isoformat() if customer.updated_at else None,
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

        # 3. Pratek customer chi complete history (transactions sahit) return karo
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
        if not customer or customer.is_monthly:
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
            txns = [t for t in txns if t.date and t.date.date() >= fd.date()]

        if to_date:
            td = _parse_date(to_date)
            txns = [t for t in txns if t.date and t.date.date() <= td.date()]


        # --- Date-wise sort ---
        reverse_order = (order.lower() == "desc")
        txns.sort(key=lambda t: t.date if t.date else datetime.min, reverse=reverse_order)

        # --- Group by date ---
        from collections import OrderedDict
        grouped: dict = OrderedDict()
        for t in txns:
            date_key = t.date.strftime("%Y-%m-%d") if t.date else "Unknown"
            if date_key not in grouped:
                grouped[date_key] = []
            grouped[date_key].append({
                "id": str(t.id),
                "jar": t.jar_count,
                "jama": t.amount,       # Jama = Paid
                "baki": t.pending,      # Baki = Pending
                "description": t.description or "",
                "created_at": t.created_at.isoformat() if t.created_at else None
            })

        # --- Running totals ---
        total_jars   = sum(t.jar_count for t in txns)
        total_paid   = sum(t.amount for t in txns if t.amount > 0 and t.jar_count == 0)
        total_cost   = sum(abs(t.amount) for t in txns if t.jar_count > 0 or (t.jar_count == 0 and t.amount < 0))

        return {
            "customer_id": customer_id,
            "customer_name": customer.customer_name,
            "customer_number": customer.customer_number,
            "customer_address": customer.customer_address,
            "total_records": len(txns),
            "date_range": {
                "from": from_date or (txns[-1].date.strftime("%Y-%m-%d") if txns else None),
                "to":   to_date   or (txns[0].date.strftime("%Y-%m-%d")  if txns else None)
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




# 📥 Download PDF
@router.get("/download-pdf")
def download_pdf(db = Depends(get_db)):
    try:
        # 1. Fetch data
        customers = get_all_customers(db, exclude_monthly=True)
        
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
            date_str = c.date.strftime("%Y-%m-%d") if c.date else ""
            status = "Active" if c.success else "Pending"
            row = [
                date_str,
                c.customer_name,
                (c.customer_address[:30] + '...') if c.customer_address and len(c.customer_address) > 30 else (c.customer_address or ""),
                c.customer_number,
                status,
                str(c.jar),
                f"${c.paid}",
                f"${c.pending}"
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

