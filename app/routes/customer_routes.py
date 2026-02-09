from fastapi import APIRouter, Depends, HTTPException
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
    data = get_all_customers(db)
    return data


# ❌ Delete customer
@router.delete("/delete/{doc_id}")
def delete(doc_id: str, db: Session = Depends(get_db)):
    delete_customer(doc_id, db)
    return {"message": "Customer deleted"}
