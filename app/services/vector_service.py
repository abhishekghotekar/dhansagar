import uuid
from datetime import datetime
from sqlalchemy.orm import Session
from app.db.chroma_db import collection
from app.embeddings.embedding_model import create_embedding
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

# 🔵 Store customer in vector DB and Postgres
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

    # convert to searchable text
    text = customer_to_text(data)

    # create embedding
    embedding = create_embedding(text)

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
                 amount=data.get('paid', 0.0), # Assuming payload 'paid' is essentially amount here. Or should it be? 
                 # Wait, usually Transaction 'amount' is cost. But 'paid' is payment?
                 # Let's map: jar -> jar_count.
                 # If 'paid' is entered, is it payment? Yes.
                 # But if 'pending' is entered?
                 # Let's simple log: Jars: X, Amount: Y.
                 # Ideally we should log what happened. Initial balance.
                 description="Initial Record"
             )
             db.add(init_txn)
             db.commit()
             
    except Exception as e:
        print(f"Error storing in Postgres: {e}")
        db.rollback()
        raise e

    # Add timestamp to metadata for Chroma
    metadata = data.copy()
    metadata['created_at'] = now.isoformat()
    metadata['updated_at'] = now.isoformat()
    # ensure date isn't None in metadata if it was parsed
    if 'date' in metadata and metadata['date'] is None:
         metadata['date'] = customer_date.isoformat()

    # store in chroma
    try:
        collection.add(
            ids=[doc_id],
            embeddings=[embedding],
            documents=[text],
            metadatas=[metadata]
        )
    except Exception as e:
        print(f"Error adding to Chroma: {e}")

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
            # Since the frontend sends TOTAL jar count for the customer
            # We calculate diff. E.g. Old 10, New 12 -> Added 2 jars.
            # BUT wait, the chat logic sends NEW total or incremental? 
            # Chat logic: `update_data['jar'] = current_jars + entities['jars_change']`
            # So `data` contains the NEW TOTAL.
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
        # Only log if there's a difference in Jars or Paiments, OR if explicitly requested?
        # If user just edits name, no transaction.
        if jar_diff != 0 or abs(paid_diff) > 0.001:
             # What is 'amount' in Transaction?
             # User prompt "how many amount".
             # Usually means Bill Amount? Or Payment Amount?
             # If paid_diff > 0, they PAID money.
             # If jar_diff > 0, they TOOK jars.
             
             # Let's assume Transaction 'amount' field tracks FINANCIAL movement.
             # But 'jar_count' tracks JAR movement.
             # If jars added -> positive jar_count.
             # If paid added -> positive amount? Or negative?
             # Let's stick to positive means "Payment/Cash In" or "Value".
             # If I want to match the Ledger concept:
             # Jars = Usage. Amount = Cash.
             
             # User wants "Date | Jars | Amount".
             # If I put jar_diff and paid_diff, it works.
             # If paid_diff is POSITIVE, it means customer PAID more. (Good).
             
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

    # Update Vector DB (Chroma)
    text = customer_to_text(data)
    embedding = create_embedding(text)
    
    # Update metadata with timestamp
    metadata = data.copy()
    metadata['updated_at'] = now.isoformat()
    
    try:
        collection.update(
            ids=[doc_id],
            embeddings=[embedding],
            documents=[text],
            metadatas=[metadata]
        )
    except Exception as e:
        print(f"Error updating Chroma: {e}")
    
    return True


# 🔍 Search customer (AI search)
def search_customer(query: str):
    try:
        query_embedding = create_embedding(query)
    except Exception as e:
        print(f"Error creating embedding for search: {e}")
        return []

    results = collection.query(
        query_embeddings=[query_embedding],
        n_results=1
    )

    # Format results
    formatted_results = []
    if results['ids'] and results['ids'][0]:
        for i, _id in enumerate(results['ids'][0]):
            if i < len(results['metadatas'][0]):
                item = results['metadatas'][0][i]
                item['id'] = _id
                item['distance'] = results['distances'][0][i]
                item['document'] = results['documents'][0][i]
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
    
    # Delete from Chroma
    try:
        collection.delete(ids=[doc_id])
    except Exception as e:
        print(f"Error deleting from Chroma: {e}")
        
    return True
