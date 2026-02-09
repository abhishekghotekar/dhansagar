import uuid
from datetime import datetime
from sqlalchemy.orm import Session
from app.db.chroma_db import collection
from app.embeddings.embedding_model import create_embedding
from app.utils.formatter import customer_to_text
from app.db.models import CustomerModel


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
            created_at=now,
            updated_at=now
        )
        db.add(new_customer)
        db.commit()
        db.refresh(new_customer)
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
    collection.add(
        ids=[doc_id],
        embeddings=[embedding],
        documents=[text],
        metadatas=[metadata]
    )

    return doc_id


# ✏️ Update customer
def update_customer(doc_id: str, data: dict, db: Session):
    # Update Postgres first
    customer = db.query(CustomerModel).filter(CustomerModel.id == doc_id).first()
    if not customer:
        return False

    now = datetime.now()

    try:
        if 'customer_name' in data: customer.customer_name = data['customer_name']
        if 'customer_address' in data: customer.customer_address = data['customer_address']
        if 'customer_number' in data: customer.customer_number = data['customer_number']
        if 'jar' in data: customer.jar = data['jar']
        if 'paid' in data: customer.paid = data['paid']
        if 'pending' in data: customer.pending = data['pending']
        if 'success' in data: customer.success = data['success']
        if 'date' in data and data['date']: 
            customer.date = parse_date(data['date'])
        
        customer.updated_at = now
        
        db.commit()
        db.refresh(customer)
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
def get_all_customers(db: Session):
    # Fetch from Postgres
    customers = db.query(CustomerModel).all()
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
