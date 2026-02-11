from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from app.schemas.customer_schema import Customer
from app.db.postgres import get_db
from app.services.vector_service import (
    store_customer,
    update_customer,
    search_customer,
    get_all_customers,
    delete_customer
)
import io
from reportlab.lib import colors
from reportlab.lib.pagesizes import landscape, A4
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph
from reportlab.lib.styles import getSampleStyleSheet

router = APIRouter()


# 🟢 Store customer
@router.post("/add-customer")
def add_customer(customer: Customer, db: Session = Depends(get_db)):
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
def update(doc_id: str, customer: Customer, db: Session = Depends(get_db)):
    try:
        success = update_customer(doc_id, customer.dict(), db)
        if not success:
            raise HTTPException(status_code=404, detail="Customer not found")
        
        return {"message": "Customer updated successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# 🔍 Search customer (AI search)
@router.get("/search")
def search(q: str):
    if not q:
        return []
    results = search_customer(q)
    return results


# 📄 Get all customers
@router.get("/all-customers")
def get_all(db: Session = Depends(get_db)):
    data = get_all_customers(db, exclude_monthly=True)
    return data


# 📥 Download PDF
@router.get("/download-pdf")
def download_pdf(db: Session = Depends(get_db)):
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
def delete(doc_id: str, db: Session = Depends(get_db)):
    delete_customer(doc_id, db)
    return {"message": "Customer deleted"}
