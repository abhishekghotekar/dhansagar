from sqlalchemy import Column, String, Integer, Float, Boolean, DateTime
from sqlalchemy.sql import func
from app.db.postgres import Base

class CustomerModel(Base):
    __tablename__ = "customers"

    id = Column(String, primary_key=True, index=True)
    customer_name = Column(String, index=True)
    customer_address = Column(String)
    customer_number = Column(String, index=True)
    jar = Column(Integer, default=0)
    paid = Column(Float, default=0.0)
    pending = Column(Float, default=0.0)
    success = Column(Boolean, default=False)
    date = Column(DateTime(timezone=True), nullable=True)
    
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now(), server_default=func.now())
