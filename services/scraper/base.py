import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional

# A number with optional thousands separators and decimals: 1,29,999.00 / 19.99
_PRICE_NUMBER_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")


@dataclass
class ScrapedProduct:
    name: str
    price: float
    currency: str = "INR"
    in_stock: bool = True
    image_url: Optional[str] = None
    raw_price_text: Optional[str] = None
    scrape_method: Optional[str] = None  # "requests" or "browser"


class ScraperError(Exception):
    pass


class BaseScraper(ABC):
    """
    All scrapers use playwright.sync_api for Celery worker compatibility.
    Celery workers run without an asyncio event loop, so async_playwright
    cannot be used. Each scrape() call opens and closes its own browser
    context to avoid shared state between concurrent Celery tasks.
    """

    def __init__(self, headless: bool = True, timeout_ms: int = 30000) -> None:
        self.headless = headless
        self.timeout_ms = timeout_ms

    @abstractmethod
    def scrape(self, url: str) -> ScrapedProduct:
        """Fetch product name, current price, stock status, and image URL."""
        ...

    def _parse_price(self, raw: str) -> float:
        """
        Parse the first number in the text as the price.
        Handles formats like ₹1,299.00 → 1299.0, $19.99 → 19.99, Rs.499 → 499.0

        Only the first number is used: price blocks often hold the sale price,
        the MRP and a discount together ("₹1,729 ₹2,399 28% OFF"), and joining
        all their digits produced prices like 17292399172928.
        """
        match = _PRICE_NUMBER_RE.search(raw)
        if not match:
            raise ScraperError(f"Could not parse price from: {raw!r}")
        return float(match.group(0).replace(",", ""))
