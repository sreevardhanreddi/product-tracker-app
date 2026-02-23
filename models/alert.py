from datetime import datetime
from typing import Optional

from sqlmodel import Field, Relationship, SQLModel


class Alert(SQLModel, table=True):
    __tablename__ = "alerts"

    id: Optional[int] = Field(default=None, primary_key=True)
    product_id: int = Field(foreign_key="products.id", index=True)
    triggered_price: float
    target_price: float
    channel: str = Field(max_length=50)  # "email" | "telegram" | "log"
    message: str = Field(max_length=1000)
    sent: bool = Field(default=False)
    sent_at: Optional[datetime] = Field(default=None)
    created_at: datetime = Field(default_factory=datetime.utcnow)

    product: Optional["Product"] = Relationship(back_populates="alerts")
