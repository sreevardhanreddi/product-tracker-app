from datetime import datetime
from typing import Optional

from sqlmodel import Field, Relationship, SQLModel


class PriceHistory(SQLModel, table=True):
    __tablename__ = "price_history"

    id: Optional[int] = Field(default=None, primary_key=True)
    product_id: int = Field(foreign_key="products.id", index=True)
    product_link_id: Optional[int] = Field(
        default=None, foreign_key="product_links.id", index=True
    )
    price: float
    currency: str = Field(default="INR", max_length=10)
    in_stock: bool = Field(default=True)
    scraped_at: datetime = Field(default_factory=datetime.utcnow)
    raw_price_text: Optional[str] = Field(default=None, max_length=100)

    product: Optional["Product"] = Relationship(back_populates="price_history")
