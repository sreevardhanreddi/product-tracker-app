import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional


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
        Strip currency symbols, commas, and whitespace, then parse as float.
        Handles formats like ₹1,299.00 → 1299.0 and $19.99 → 19.99
        """
        cleaned = re.sub(r"[^\d.]", "", raw.replace(",", ""))
        if not cleaned:
            raise ScraperError(f"Could not parse price from: {raw!r}")
        return float(cleaned)
