from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field, model_validator


class ProductLinkCreate(BaseModel):
    url: str
    check_interval_minutes: Optional[int] = None


class ProductLinkUpdate(BaseModel):
    check_interval_minutes: Optional[int] = None
    is_active: Optional[bool] = None
    alerts_enabled: Optional[bool] = None


class ProductLinkRead(BaseModel):
    id: int
    product_id: int
    url: str
    platform: str
    source_label: str
    current_price: Optional[float]
    currency: str
    image_url: Optional[str]
    is_active: bool
    alerts_enabled: bool
    check_interval_minutes: int
    created_at: datetime
    updated_at: datetime
    last_checked_at: Optional[datetime]
    last_scrape_method: Optional[str]

    model_config = {"from_attributes": True}


class ProductCreate(BaseModel):
    # Backward-compatible single-link input
    url: Optional[str] = None
    # New multi-link input
    links: Optional[List[ProductLinkCreate]] = None

    name: Optional[str] = None
    target_price: Optional[float] = None
    check_interval_minutes: int = 60

    @model_validator(mode="after")
    def validate_links(self):
        if not self.url and not self.links:
            raise ValueError("Provide either 'url' or a non-empty 'links' list.")
        if self.url and self.links:
            raise ValueError("Provide only one of 'url' or 'links', not both.")
        if self.links is not None and len(self.links) == 0:
            raise ValueError("'links' must not be empty.")
        return self


class ProductUpdate(BaseModel):
    name: Optional[str] = None
    target_price: Optional[float] = None
    check_interval_minutes: Optional[int] = None
    is_active: Optional[bool] = None
    alerts_enabled: Optional[bool] = None
    links: Optional[List[ProductLinkUpdate]] = None


class ProductRead(BaseModel):
    id: int
    name: str
    url: str
    platform: str
    source_label: str
    target_price: Optional[float]
    current_price: Optional[float]
    currency: str
    image_url: Optional[str]
    is_active: bool
    alerts_enabled: bool
    check_interval_minutes: int
    created_at: datetime
    updated_at: datetime
    last_checked_at: Optional[datetime]
    links: List[ProductLinkRead] = Field(default_factory=list)

    model_config = {"from_attributes": True}
