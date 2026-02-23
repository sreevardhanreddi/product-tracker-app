from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class ProductCreate(BaseModel):
    url: str
    name: Optional[str] = None
    target_price: Optional[float] = None
    check_interval_minutes: int = 60


class ProductUpdate(BaseModel):
    name: Optional[str] = None
    target_price: Optional[float] = None
    check_interval_minutes: Optional[int] = None
    is_active: Optional[bool] = None


class ProductRead(BaseModel):
    id: int
    name: str
    url: str
    platform: str
    target_price: Optional[float]
    current_price: Optional[float]
    currency: str
    image_url: Optional[str]
    is_active: bool
    check_interval_minutes: int
    created_at: datetime
    updated_at: datetime
    last_checked_at: Optional[datetime]

    model_config = {"from_attributes": True}
