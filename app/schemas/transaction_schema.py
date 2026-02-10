from pydantic import BaseModel, Field
from datetime import datetime

class TransactionBase(BaseModel):
    customer_id: str
    date: str = Field(..., description="ISO formated date string")
    jar_count: int = 0
    amount: float = 0.0
    description: str | None = None

class TransactionCreate(TransactionBase):
    pass

class TransactionUpdate(BaseModel):
    date: str | None = None
    jar_count: int | None = None
    amount: float | None = None
    description: str | None = None

class TransactionResponse(TransactionBase):
    id: int
    created_at: datetime
    updated_at: datetime
    
    class Config:
        from_attributes = True
