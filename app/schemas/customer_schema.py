from pydantic import BaseModel, Field, ConfigDict
from typing import List, Optional

class TransactionSchema(BaseModel):
    id: str
    date: str
    jar: int = Field(0, ge=0)
    paid: float = Field(0.0, ge=0.0)
    pending: float = Field(0.0, ge=0.0)
    description: Optional[str] = "Delivery Log"
    manual_bill: Optional[float] = Field(0.0, ge=0.0)

    model_config = ConfigDict(from_attributes=True)

class CustomerCreate(BaseModel):
    customer_name: str = Field(..., min_length=1, example="Avinash")
    customer_number: str = Field(..., min_length=1, example="9876543210")
    customer_address: Optional[str] = ""
    success: bool = False
    date: Optional[str] = None
    jar: Optional[int] = Field(0, ge=0)
    paid: Optional[float] = Field(0.0, ge=0.0)
    pending: Optional[float] = Field(0.0, ge=0.0)
    rate: Optional[float] = Field(25.0, ge=0.0)

    model_config = ConfigDict(from_attributes=True)

class Customer(CustomerCreate):
    transactions: Optional[List[TransactionSchema]] = None

    model_config = ConfigDict(from_attributes=True)

class CustomerUpdate(BaseModel):
    customer_name: Optional[str] = None
    customer_address: Optional[str] = None
    customer_number: Optional[str] = None
    jar: Optional[int] = Field(None, ge=0)
    paid: Optional[float] = Field(None, ge=0.0)
    pending: Optional[float] = Field(None, ge=0.0)
    success: Optional[bool] = None
    date: Optional[str] = None
    rate: Optional[float] = Field(None, ge=0.0)
    transactions: Optional[List[TransactionSchema]] = None

    model_config = ConfigDict(from_attributes=True)

class DailyEntryCreate(BaseModel):
    customer_id: Optional[str] = None
    record_id: Optional[str] = None
    date: Optional[str] = None
    jar: int = Field(0, ge=0, description="Delivered jar count")
    paid: float = Field(0.0, ge=0.0, description="Amount paid / Jama")
    pending: float = Field(0.0, ge=0.0, description="Manual bill amount / Baki")
    description: Optional[str] = "Delivery"

    model_config = ConfigDict(from_attributes=True)

class DailyEntryUpdate(BaseModel):
    record_id: Optional[str] = None
    customer_id: Optional[str] = None
    date: Optional[str] = None
    jar: Optional[int] = Field(None, ge=0)
    paid: Optional[float] = Field(None, ge=0.0)
    pending: Optional[float] = Field(None, ge=0.0)
    description: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)
