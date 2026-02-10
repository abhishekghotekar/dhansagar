from sqlalchemy.orm import Session
from app.db.models import CustomerModel, TransactionModel
from app.schemas.customer_schema import CustomerUpdate
from app.schemas.transaction_schema import TransactionCreate, TransactionUpdate
from datetime import datetime

# --- Monthly / Transaction Services ---

def get_monthly_customers(db: Session):
    return db.query(CustomerModel).filter(CustomerModel.is_monthly == True).all()

def create_transaction(data: dict, db: Session):
    date = datetime.fromisoformat(data['date'])
    transaction = TransactionModel(
        customer_id=data['customer_id'],
        date=date,
        jar_count=data['jar_count'],
        amount=data['amount'],
        description=data.get('description')
    )
    db.add(transaction)
    db.commit()
    db.refresh(transaction)
    return transaction

def get_transactions_by_customer(customer_id: str, db: Session):
    return db.query(TransactionModel).filter(TransactionModel.customer_id == customer_id).order_by(TransactionModel.date.desc()).all()

def update_transaction(transaction_id: int, data: dict, db: Session):
    transaction = db.query(TransactionModel).filter(TransactionModel.id == transaction_id).first()
    if not transaction:
        return None
    
    if 'date' in data and data['date']:
        transaction.date = datetime.fromisoformat(data['date'])
    if 'jar_count' in data:
        transaction.jar_count = data['jar_count']
    if 'amount' in data:
        transaction.amount = data['amount']
    if 'description' in data:
        transaction.description = data['description']
        
    db.commit()
    db.refresh(transaction)
    return transaction

def delete_transaction(transaction_id: int, db: Session):
    transaction = db.query(TransactionModel).filter(TransactionModel.id == transaction_id).first()
    if transaction:
        db.delete(transaction)
        db.commit()
        return True
    return False

def calculate_totals(customer_id: str, db: Session):
    transactions = get_transactions_by_customer(customer_id, db)
    total_jars = sum(t.jar_count for t in transactions)
    total_amount = sum(t.amount for t in transactions)
    return {"total_jars": total_jars, "total_amount": total_amount}
