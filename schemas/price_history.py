from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class PriceHistoryRead(BaseModel):
    id: int
    product_id: int
    product_link_id: Optional[int]
    price: float
    currency: str
    in_stock: bool
    scraped_at: datetime
    raw_price_text: Optional[str]

    model_config = {"from_attributes": True}
