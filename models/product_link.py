from datetime import datetime
from typing import Optional

from sqlmodel import Field, Relationship, SQLModel

from .source_label import format_source_label


class ProductLink(SQLModel, table=True):
    __tablename__ = "product_links"

    id: Optional[int] = Field(default=None, primary_key=True)
    product_id: int = Field(foreign_key="products.id", index=True)
    url: str = Field(max_length=2000, unique=True)
    platform: str = Field(max_length=50)  # "amazon" | "flipkart" | "shopify" | "myntra"
    current_price: Optional[float] = Field(default=None)
    currency: str = Field(default="INR", max_length=10)
    image_url: Optional[str] = Field(default=None, max_length=2000)
    is_active: bool = Field(default=True)
    check_interval_minutes: int = Field(default=60)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    last_checked_at: Optional[datetime] = Field(default=None)
    last_scrape_method: Optional[str] = Field(default=None, max_length=20)

    product: Optional["Product"] = Relationship(back_populates="links")

    @property
    def source_label(self) -> str:
        return format_source_label(self.platform, self.url)
