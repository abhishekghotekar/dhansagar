from pydantic import BaseModel, Field

class Customer(BaseModel):
    customer_name: str = Field(..., example="Avinash")
    customer_address: str = Field(..., example="Shirdi")
    customer_number: str = Field(..., example="9876543210")
    jar: int = 0
    paid: float = 0.0
    pending: float = 0.0
    success: bool = False
    date: str | None = None
