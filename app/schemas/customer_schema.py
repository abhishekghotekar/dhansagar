from pydantic import BaseModel, Field
from datetime import datetime
from typing import List, Optional

class TransactionSchema(BaseModel):
    id: str
    date: str
    jar: int
    paid: float
    pending: float

class Customer(BaseModel):
    customer_name: str = Field(..., example="Avinash")
    customer_address: str = Field(..., example="Shirdi")
    customer_number: str = Field(..., example="9876543210")
    jar: int = 0
    paid: float = 0.0
    pending: float = 0.0
    success: bool = False
    date: str | None = None
    is_monthly: bool = False
    transactions: Optional[List[TransactionSchema]] = None

    class Config:
        from_attributes = True

class CustomerUpdate(BaseModel):
    customer_name: str | None = None
    customer_address: str | None = None
    customer_number: str | None = None
    jar: int | None = None
    paid: float | None = None
    pending: float | None = None
    success: bool | None = None
    date: str | None = None
    is_monthly: bool | None = None
    transactions: Optional[List[TransactionSchema]] = None

