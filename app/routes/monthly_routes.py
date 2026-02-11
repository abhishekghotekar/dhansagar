from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from app.db.postgres import get_db
from app.db.models import CustomerModel, TransactionModel
from app.schemas.transaction_schema import TransactionCreate, TransactionUpdate
from datetime import datetime
import io
from fastapi.responses import StreamingResponse
from reportlab.lib import colors
from reportlab.lib.pagesizes import landscape, A4
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph
from reportlab.lib.styles import getSampleStyleSheet

router = APIRouter(prefix="/monthly", tags=["Monthly Credit"])

# 1. Get Monthly Credit Customers
@router.get("/customers")
def get_monthly_customers_route(db: Session = Depends(get_db)):
    customers = db.query(CustomerModel).filter(CustomerModel.is_monthly == True).all()
    # Return simple list
    return customers

# 2. Get Transactions for a Customer
@router.get("/transactions/{customer_id}")
def get_transactions(customer_id: str, db: Session = Depends(get_db)):
    transactions = db.query(TransactionModel).filter(TransactionModel.customer_id == customer_id).order_by(TransactionModel.date.desc()).all()
    customer = db.query(CustomerModel).filter(CustomerModel.id == customer_id).first()
    
    # Calculate totals
    total_jars = sum(t.jar_count for t in transactions)
    
    # Smart Logic for Ledger:
    # 1. If jars > 0: The 'amount' is treated as the COST of those jars (Debit).
    # 2. If jars == 0: The 'amount' is treated as a PAYMENT (Credit).
    # 3. Explicit Negative amounts are always treated as COST (adjustments).
    
    total_cost = 0.0
    total_paid = 0.0
    
    for t in transactions:
        val = t.amount
        if t.jar_count > 0:
            # It's a delivery. Amount represents cost.
            # Even if positive, we treat as cost.
            total_cost += abs(val) # Treat as Cost
        else:
            # No jars.
            if val > 0:
                 total_paid += val  # Payment
            else:
                 total_cost += abs(val) # Negative amount with 0 jars = Cost adjustment
    
    total_pending = total_cost - total_paid
    
    return {
        "transactions": transactions,
        "summary": {
            "customer_name": customer.customer_name if customer else "Unknown",
            "total_jars": total_jars,
            "total_cost": total_cost,
            "total_paid": total_paid,
            "total_pending": total_pending
        }
    }

# 3. Add Transaction
@router.post("/transaction")
def add_transaction(data: TransactionCreate, db: Session = Depends(get_db)):
    try:
        # data.date is expected as ISO string
        try:
            date_obj = datetime.fromisoformat(data.date.replace('Z', '+00:00'))
        except:
             # Fallback if just YYYY-MM-DD
             date_obj = datetime.strptime(data.date, "%Y-%m-%d")
             
        new_txn = TransactionModel(
            customer_id=data.customer_id,
            date=date_obj,
            jar_count=data.jar_count,
            amount=data.amount,
            description=data.description
        )
        db.add(new_txn)
        db.commit()
        db.refresh(new_txn)
        return new_txn
    except Exception as e:
        print(f"Error adding transaction: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# 4. Update Transaction
@router.put("/transaction/{txn_id}")
def update_transaction_route(txn_id: int, data: TransactionUpdate, db: Session = Depends(get_db)):
    txn = db.query(TransactionModel).filter(TransactionModel.id == txn_id).first()
    if not txn:
        raise HTTPException(status_code=404, detail="Transaction not found")
    
    if data.date:
        try:
            txn.date = datetime.fromisoformat(data.date.replace('Z', '+00:00'))
        except:
            txn.date = datetime.strptime(data.date, "%Y-%m-%d")

    if data.jar_count is not None:
        txn.jar_count = data.jar_count
    if data.amount is not None:
        txn.amount = data.amount
    if data.description is not None:
        txn.description = data.description
        
    db.commit()
    db.refresh(txn)
    return txn

# 5. Delete Transaction
@router.delete("/transaction/{txn_id}")
def delete_transaction_route(txn_id: int, db: Session = Depends(get_db)):
    txn = db.query(TransactionModel).filter(TransactionModel.id == txn_id).first()
    if not txn:
        raise HTTPException(status_code=404, detail="Transaction not found")
    
    db.delete(txn)
    db.commit()
    return {"message": "Transaction deleted"}

# 6. Toggle Monthly Status for Customer
@router.post("/toggle-status/{customer_id}")
def toggle_monthly_status(customer_id: str, is_monthly: bool, db: Session = Depends(get_db)):
    customer = db.query(CustomerModel).filter(CustomerModel.id == customer_id).first()
    if not customer:
         raise HTTPException(status_code=404, detail="Customer not found")
    
    customer.is_monthly = is_monthly
    db.commit()
    return {"message": f"Customer monthly status set to {is_monthly}"}

# 7. Download Monthly Customer PDF
@router.get("/download-pdf")
def download_monthly_pdf(db: Session = Depends(get_db)):
    try:
        # 1. Fetch Monthly Customers
        customers = db.query(CustomerModel).filter(CustomerModel.is_monthly == True).all()

        # 2. Create Buffer
        buffer = io.BytesIO()

        # 3. Create PDF Doc (Landscape A4)
        doc = SimpleDocTemplate(buffer, pagesize=landscape(A4), rightMargin=30, leftMargin=30, topMargin=30, bottomMargin=30)
        elements = []

        styles = getSampleStyleSheet()
        title = Paragraph("Monthly Customer Report", styles['Title'])
        elements.append(title)
        elements.append(Paragraph("<br/><br/>", styles['Normal'])) # Spacer

        # 4. Table Data
        # Columns: Date (Last Txn), Name, Address, Phone, Status, Jars, Paid, Pending
        data = [['Last Activity', 'Customer Name', 'Address', 'Mobile', 'Status', 'Total Jars', 'Paid', 'Pending']]

        for c in customers:
            # Calculate totals dynamically from transactions
            transactions = c.transactions # via relationship

            total_jars = 0
            total_paid = 0.0
            total_cost = 0.0

            last_date = "N/A"
            if transactions:
                # Sort by date descending to find last activity
                sorted_txns = sorted(transactions, key=lambda x: x.date, reverse=True)
                if sorted_txns:
                    last_date = sorted_txns[0].date.strftime("%Y-%m-%d")

                for t in transactions:
                    val = t.amount
                    if t.jar_count > 0:
                        total_cost += abs(val) # Cost
                        total_jars += t.jar_count
                    else:
                        if val > 0:
                             total_paid += val  # Payment
                        else:
                             total_cost += abs(val) # Cost adjustment

            total_pending = total_cost - total_paid

            # Status based on Pending
            status = "Settled" if total_pending <= 0 else "Outstanding"
            
            # Format rows
            row = [
                last_date,
                c.customer_name,
                (c.customer_address[:25] + '...') if c.customer_address and len(c.customer_address) > 25 else (c.customer_address or ""),
                c.customer_number,
                status,
                str(total_jars),
                f"${total_paid:.2f}",
                f"${total_pending:.2f}"
            ]
            data.append(row)

        # 5. Table Style
        # Auto-adjust column widths if possible, or set fixed
        # A4 Landscape width is ~840 points. Margins 30 -> 780 usable.
        col_widths = [80, 150, 150, 100, 80, 60, 80, 80]
        
        table = Table(data, colWidths=col_widths)
        style = TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.darkblue),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 12),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
            ('BACKGROUND', (0, 1), (-1, -1), colors.white),
            ('GRID', (0, 0), (-1, -1), 1, colors.black),
            ('FONTNAME', (0, 1), (-1, -1), 'Helvetica'),
            ('FONTSIZE', (0, 1), (-1, -1), 10),
            ('ALIGN', (1, 1), (2, -1), 'LEFT'), # Align Names & Address Left
        ])
        table.setStyle(style)
        
        # Alternating row colors
        for i in range(1, len(data)):
            if i % 2 == 0:
                ts = TableStyle([('BACKGROUND', (0, i), (-1, i), colors.lightgrey)])
                table.setStyle(ts)

        elements.append(table)

        # 6. Build
        doc.build(elements)

        buffer.seek(0)
        return StreamingResponse(
            buffer,
            media_type="application/pdf",
            headers={"Content-Disposition": "attachment; filename=monthly_customers_report.pdf"}
        )

    except Exception as e:
        print(f"Error generating PDF: {e}")
        raise HTTPException(status_code=500, detail=f"Error generating PDF: {str(e)}")

# 8. Download Single Customer Ledger PDF
@router.get("/download-pdf/{customer_id}")
def download_customer_ledger(customer_id: str, db: Session = Depends(get_db)):
    try:
        # 1. Fetch Customer & Transactions
        customer = db.query(CustomerModel).filter(CustomerModel.id == customer_id).first()
        if not customer:
            raise HTTPException(status_code=404, detail="Customer not found")
            
        transactions = db.query(TransactionModel).filter(TransactionModel.customer_id == customer_id).order_by(TransactionModel.date.desc()).all()
        
        # 2. Logic (Replicated from get_transactions)
        prepared_data = []
        total_jars = 0
        total_cost = 0.0
        total_paid = 0.0
        
        # We need to process in chronological order for a running balance, 
        # but the request is usually for a statement. 
        # Let's stick to the list view format but maybe sorted safely.
        # Currently transactions are DESC (latest first).
        
        for t in transactions:
            val = t.amount
            cost = 0.0
            paid = 0.0
            
            if t.jar_count > 0:
                cost = abs(val) # Cost
                total_jars += t.jar_count
            else:
                if val > 0:
                    paid = val  # Payment
                else:
                    cost = abs(val) # Cost adjustment
            
            total_cost += cost
            total_paid += paid
            
            prepared_data.append({
                "date": t.date.strftime("%Y-%m-%d"),
                "desc": t.description or "-",
                "jars": str(t.jar_count) if t.jar_count > 0 else "-",
                "debit": f"{cost:.2f}" if cost > 0 else "-",
                "credit": f"{paid:.2f}" if paid > 0 else "-"
            })
            
        total_pending = total_cost - total_paid
        
        # 3. Create PDF
        buffer = io.BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=30, leftMargin=30, topMargin=30, bottomMargin=30)
        elements = []
        styles = getSampleStyleSheet()
        
        # Header
        elements.append(Paragraph(f"Customer Ledger: {customer.customer_name}", styles['Title']))
        elements.append(Paragraph(f"Address: {customer.customer_address or ''} | Mobile: {customer.customer_number}", styles['Normal']))
        elements.append(Paragraph("<br/>", styles['Normal']))
        
        # Summary Area
        summary_data = [
            ['Total Jars', 'Total Cost (Debit)', 'Total Paid (Credit)', 'Net Pending'],
            [str(total_jars), f"${total_cost:.2f}", f"${total_paid:.2f}", f"${total_pending:.2f}"]
        ]
        summary_table = Table(summary_data, colWidths=[100, 120, 120, 100])
        summary_table.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,0), colors.lightgrey),
            ('ALIGN', (0,0), (-1,-1), 'CENTER'),
            ('GRID', (0,0), (-1,-1), 1, colors.black),
            ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
        ]))
        elements.append(summary_table)
        elements.append(Paragraph("<br/><br/>", styles['Normal']))
        
        # Transaction Table
        table_data = [['Date', 'Description', 'Jars', 'Debit', 'Credit']]
        for row in prepared_data:
            table_data.append([row['date'], row['desc'], row['jars'], row['debit'], row['credit']])
            
        t_table = Table(table_data, colWidths=[80, 200, 60, 80, 80])
        t_table.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,0), colors.darkblue),
            ('TEXTCOLOR', (0,0), (-1,0), colors.whitesmoke),
            ('ALIGN', (0,0), (-1,-1), 'CENTER'),
            ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
            ('GRID', (0,0), (-1,-1), 1, colors.black),
            ('ALIGN', (1,1), (1,-1), 'LEFT'), # Description align left
        ]))
        
        # Alternating colors
        for i in range(1, len(table_data)):
            if i % 2 == 0:
                t_table.setStyle(TableStyle([('BACKGROUND', (0,i), (-1,i), colors.whitesmoke)]))
                
        elements.append(t_table)
        
        doc.build(elements)
        buffer.seek(0)
        
        filename = f"{customer.customer_name.replace(' ', '_')}_ledger.pdf"
        return StreamingResponse(
            buffer,
            media_type="application/pdf",
            headers={"Content-Disposition": f"attachment; filename={filename}"}
        )
        
    except Exception as e:
        print(f"Error generating customer PDF: {e}")
        raise HTTPException(status_code=500, detail=str(e))
