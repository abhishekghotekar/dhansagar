from sqlalchemy import Column, String, Integer, Float, Boolean, DateTime, ForeignKey
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
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
    
    # New Field for Monthly Credit Feature
    is_monthly = Column(Boolean, default=False)
    
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now(), server_default=func.now())

    # Relationship to transactions
    transactions = relationship("TransactionModel", back_populates="customer", cascade="all, delete-orphan")


class TransactionModel(Base):
    __tablename__ = "transactions"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    customer_id = Column(String, ForeignKey("customers.id"), nullable=False)
    date = Column(DateTime(timezone=True), nullable=False)
    jar_count = Column(Integer, default=0)
    amount = Column(Float, default=0.0)
    description = Column(String, nullable=True)
    
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now(), server_default=func.now())

    customer = relationship("CustomerModel", back_populates="transactions")
