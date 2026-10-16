from datetime import datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, Field, model_validator

# Only Shopify can be confirmed from the user's browser (its product .js is
# CORS-open), so it's the only hint the server accepts when detection fails.
PlatformHint = Literal["shopify"]


class ProductLinkCreate(BaseModel):
    url: str
    check_interval_minutes: Optional[int] = None
    platform: Optional[PlatformHint] = None


class ClientPriceSync(BaseModel):
    """A price the user's browser fetched when the server-side scrape failed."""

    name: Optional[str] = None
    price: float = Field(gt=0)
    currency: str = "INR"
    in_stock: bool = True
    image_url: Optional[str] = None
    raw_price_text: Optional[str] = None


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
    in_stock: Optional[bool]
    last_in_stock_price: Optional[float]
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
    # Platform hint for `url`, used only if server-side detection fails.
    platform: Optional[PlatformHint] = None

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


class ProductListResponse(BaseModel):
    items: List[ProductRead]
    total: int
    page: int
    page_size: int
