from pydantic import BaseModel, Field, ConfigDict
from datetime import datetime
from typing import Optional

class TransactionBase(BaseModel):
    customer_id: str
    date: str = Field(..., description="ISO formatted date string")
    jar_count: int = 0
    amount: float = 0.0
    description: Optional[str] = None
    manual_bill: Optional[float] = 0.0

    model_config = ConfigDict(from_attributes=True)

class TransactionCreate(TransactionBase):
    pass

class TransactionUpdate(BaseModel):
    date: Optional[str] = None
    jar_count: Optional[int] = None
    amount: Optional[float] = None
    description: Optional[str] = None
    manual_bill: Optional[float] = None

    model_config = ConfigDict(from_attributes=True)

class TransactionResponse(TransactionBase):
    id: str
    pending: float = 0.0
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    
    model_config = ConfigDict(from_attributes=True)
