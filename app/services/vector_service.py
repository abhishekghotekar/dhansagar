import uuid
from datetime import datetime
from sqlalchemy.orm import Session
from sqlalchemy import or_
from app.utils.formatter import customer_to_text
from app.db.models import CustomerModel, TransactionModel


# Helper to parse date
def parse_date(date_val):
    if not date_val:
        return datetime.now()
    if isinstance(date_val, datetime):
        return date_val
    try:
        return datetime.strptime(date_val, "%Y-%m-%d")
    except ValueError:
        try:
            return datetime.fromisoformat(date_val)
        except:
            return datetime.now()

# 🔵 Store customer in Postgres (Chroma removed)
def store_customer(data: dict, db: Session):
    # Check for duplicate customer by number
    cust_num = data.get('customer_number')
    if cust_num:
        existing_customer = db.query(CustomerModel).filter(
            CustomerModel.customer_number == cust_num
        ).first()
        
        if existing_customer:
            print(f"Duplicate customer found: {cust_num}. Updating existing record {existing_customer.id}")
            update_customer(existing_customer.id, data, db)
            return existing_customer.id

    # unique id
    doc_id = str(uuid.uuid4())
    
    # current time
    now = datetime.now()
    
    # Parse date
    customer_date = parse_date(data.get('date'))

    # store in postgres
    try:
        new_customer = CustomerModel(
            id=doc_id,
            customer_name=data['customer_name'],
            customer_address=data['customer_address'],
            customer_number=data['customer_number'],
            jar=data.get('jar', 0),
            paid=data.get('paid', 0.0),
            pending=data.get('pending', 0.0),
            success=data.get('success', False),
            date=customer_date,
            is_monthly=data.get('is_monthly', False),
            created_at=now,
            updated_at=now
        )
        db.add(new_customer)
        db.commit()
        db.refresh(new_customer)
        
        # 🟢 Create initial transaction if jars/paid > 0
        if data.get('jar') or data.get('paid'):
             init_txn = TransactionModel(
                 customer_id=doc_id,
                 date=customer_date,
                 jar_count=data.get('jar', 0),
                 amount=data.get('paid', 0.0), # Assuming payload 'paid' is essentially amount here.
                 description="Initial Record"
             )
             db.add(init_txn)
             db.commit()
             
    except Exception as e:
        print(f"Error storing in Postgres: {e}")
        db.rollback()
        raise e

    return doc_id


# ✏️ Update customer
def update_customer(doc_id: str, data: dict, db: Session):
    # Update Postgres first
    customer = db.query(CustomerModel).filter(CustomerModel.id == doc_id).first()
    if not customer:
        return False

    now = datetime.now()
    
    # Track changes for Transaction log
    jar_diff = 0
    paid_diff = 0.0
    
    # Pre-update values
    old_jar = customer.jar or 0
    old_paid = customer.paid or 0.0

    try:
        if 'customer_name' in data: customer.customer_name = data['customer_name']
        if 'customer_address' in data: customer.customer_address = data['customer_address']
        if 'customer_number' in data: customer.customer_number = data['customer_number']
        
        # Jar Update logic
        if 'jar' in data: 
            new_jar = int(data['jar'])
            jar_diff = new_jar - old_jar
            customer.jar = new_jar
            
        # Paid Update logic
        if 'paid' in data: 
            new_paid = float(data['paid'])
            paid_diff = new_paid - old_paid
            customer.paid = new_paid
            
        if 'pending' in data: customer.pending = data['pending']
        if 'success' in data: customer.success = data['success']
        if 'is_monthly' in data: customer.is_monthly = data['is_monthly']
        
        txn_date = now
        if 'date' in data and data['date']: 
            customer.date = parse_date(data['date'])
            txn_date = customer.date
        
        customer.updated_at = now
        
        db.commit()
        db.refresh(customer)
        
        # 🟢 Create Transaction Log if there's activity
        if jar_diff != 0 or abs(paid_diff) > 0.001:
             
             description = "Update"
             if jar_diff > 0: description = f"Added {jar_diff} jars"
             if paid_diff > 0: description = f"Paid {paid_diff}"
             if jar_diff > 0 and paid_diff > 0: description = f"Added {jar_diff} jars & Paid {paid_diff}"
             
             new_txn = TransactionModel(
                 customer_id=doc_id,
                 date=txn_date,
                 jar_count=jar_diff,
                 amount=paid_diff, # Recording the CHANGE in payment
                 description=description
             )
             db.add(new_txn)
             db.commit()

    except Exception as e:
        print(f"Error updating Postgres: {e}")
        db.rollback()
        raise e
    
    return True


# 🔍 Search customer (Postgres ILIKE search)
def search_customer(query: str, db: Session):
    if not query:
        return []

    # Search in name, address, number
    search_term = f"%{query}%"

    results = db.query(CustomerModel).filter(
        or_(
            CustomerModel.customer_name.ilike(search_term),
            CustomerModel.customer_address.ilike(search_term),
            CustomerModel.customer_number.ilike(search_term)
        )
    ).all()

    formatted_results = []
    for customer in results:
        item = {
            "id": customer.id,
            "customer_name": customer.customer_name,
            "customer_address": customer.customer_address,
            "customer_number": customer.customer_number,
            "jar": customer.jar,
            "paid": customer.paid,
            "pending": customer.pending,
            "success": customer.success,
            "date": customer.date.isoformat() if customer.date else None,
            "is_monthly": customer.is_monthly,
            "created_at": customer.created_at.isoformat() if customer.created_at else None,
            "updated_at": customer.updated_at.isoformat() if customer.updated_at else None,
            "distance": 0.0 # Mock distance
        }
        item['document'] = f"{customer.customer_name} | {customer.customer_address} | {customer.customer_number}"
        formatted_results.append(item)

    return formatted_results


# 📄 Get all customers (from Postgres)
def get_all_customers(db: Session, exclude_monthly: bool = False):
    # Fetch from Postgres
    query = db.query(CustomerModel)
    
    if exclude_monthly:
        query = query.filter(CustomerModel.is_monthly == False)
        
    customers = query.all()
    # Sort by updated_at desc if available, to show newest updates first
    try:
        customers.sort(key=lambda x: x.updated_at or x.created_at or datetime.min, reverse=True)
    except:
        pass # Fallback if dates are None
    return customers


# ❌ Delete customer
def delete_customer(doc_id: str, db: Session):
    # Delete from Postgres
    customer = db.query(CustomerModel).filter(CustomerModel.id == doc_id).first()
    if customer:
        db.delete(customer)
        db.commit()
    
    return True
