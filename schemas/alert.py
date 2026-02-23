from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class AlertRead(BaseModel):
    id: int
    product_id: int
    triggered_price: float
    target_price: float
    channel: str
    message: str
    sent: bool
    sent_at: Optional[datetime]
    created_at: datetime

    model_config = {"from_attributes": True}
