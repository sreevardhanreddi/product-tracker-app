from datetime import datetime
from typing import List, Optional

from sqlmodel import Field, Relationship, SQLModel


class Product(SQLModel, table=True):
    __tablename__ = "products"

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(max_length=500)
    url: str = Field(max_length=2000, unique=True)
    platform: str = Field(max_length=50)  # "amazon" | "flipkart" | "shopify"
    target_price: Optional[float] = Field(default=None)
    current_price: Optional[float] = Field(default=None)
    currency: str = Field(default="INR", max_length=10)
    image_url: Optional[str] = Field(default=None, max_length=2000)
    is_active: bool = Field(default=True)
    check_interval_minutes: int = Field(default=60)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    last_checked_at: Optional[datetime] = Field(default=None)

    price_history: List["PriceHistory"] = Relationship(back_populates="product")
    alerts: List["Alert"] = Relationship(back_populates="product")
