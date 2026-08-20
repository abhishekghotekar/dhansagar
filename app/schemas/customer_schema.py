from pydantic import BaseModel, Field
from datetime import datetime
from typing import List, Optional

class TransactionSchema(BaseModel):
    id: str
    date: str
    jar: int
    paid: float
    pending: float
    description: Optional[str] = "Delivery Log"
    manual_bill: Optional[float] = 0.0

class Customer(BaseModel):
    customer_name: str = Field(..., example="Avinash")
    customer_number: str = Field(..., example="9876543210")
    customer_address: Optional[str] = ""
    success: bool = False
    date: str | None = None
    jar: Optional[int] = 0
    paid: Optional[float] = 0.0
    pending: Optional[float] = 0.0
    is_monthly: Optional[bool] = False
    rate: Optional[float] = 25.0
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
    rate: float | None = None
    transactions: Optional[List[TransactionSchema]] = None

class DailyEntryCreate(BaseModel):
    customer_id: Optional[str] = None
    date: Optional[str] = None
    jar_count: int = Field(0, description="Delivered jar count")
    amount: float = Field(0.0, description="Amount paid / Jama")
    pending: float = Field(0.0, description="Manual pending amount (Bill)")
    description: Optional[str] = "Delivery"

class DailyEntryUpdate(BaseModel):
    date: Optional[str] = None
    jar_count: Optional[int] = None
    amount: Optional[float] = None
    pending: Optional[float] = None
    description: Optional[str] = None

