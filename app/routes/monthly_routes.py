from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from app.db.postgres import get_db
from app.db.models import CustomerModel, TransactionModel
from app.schemas.transaction_schema import TransactionCreate, TransactionUpdate
from datetime import datetime

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
    total_amount = sum(t.amount for t in transactions)
    
    return {
        "transactions": transactions,
        "summary": {
            "customer_name": customer.customer_name if customer else "Unknown",
            "total_jars": total_jars,
            "total_amount": total_amount
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
