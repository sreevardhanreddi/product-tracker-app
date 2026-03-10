from urllib.parse import urlparse

import httpx
from playwright.sync_api import sync_playwright

from .base import BaseScraper, ScrapedProduct, ScraperError

# Ordered list of CSS selectors to try for product title on Shopify stores
_TITLE_SELECTORS = [
    "h1.product__title",
    "h1[itemprop='name']",
    ".product-single__title",
    ".product__title",
    "h1",
]

# Ordered list of CSS selectors to try for product price on Shopify stores
_PRICE_SELECTORS = [
    "[data-product-price]",
    ".price__current",
    ".product-price",
    ".price",
    "[class*='price']",
]

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0.0.0 Safari/537.36"
)

_JSON_HEADERS = {
    "User-Agent": _USER_AGENT,
    "Accept": "application/json,text/plain,*/*",
    "Accept-Language": "en-IN,en;q=0.9",
    "Referer": "https://www.google.com/",
}


class ShopifyScraper(BaseScraper):
    """
    Scrapes Shopify product pages.

    Strategy:
    1. Try the public JSON API: GET /products/{handle}.json
       Fast, no browser needed; works on most Shopify stores.
    2. Fall back to Playwright if the JSON endpoint fails or returns
       unexpected data (e.g., store requires login or handle differs).
    """

    def scrape(self, url: str) -> ScrapedProduct:
        try:
            return self._scrape_json(url)
        except Exception:
            pass
        return self._scrape_playwright(url)

    def _scrape_json(self, url: str) -> ScrapedProduct:
        parsed = urlparse(url)
        handle = parsed.path.rstrip("/").split("/")[-1]
        api_url = f"{parsed.scheme}://{parsed.hostname}/products/{handle}.json"

        r = httpx.get(
            api_url,
            headers=_JSON_HEADERS,
            timeout=10,
            follow_redirects=True,
        )
        r.raise_for_status()
        data = r.json().get("product")
        if not data:
            raise ScraperError("No 'product' key in Shopify JSON response")

        variant = data["variants"][0]
        price = float(variant["price"])
        in_stock = bool(variant.get("available", True))
        image_url = data["images"][0]["src"] if data.get("images") else None
        # Shopify JSON doesn't always expose currency; fall back to USD
        currency = variant.get("price_currency") or "USD"

        return ScrapedProduct(
            name=data["title"],
            price=price,
            currency=currency,
            in_stock=in_stock,
            image_url=image_url,
            raw_price_text=variant["price"],
        )

    def _scrape_playwright(self, url: str) -> ScrapedProduct:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=self.headless)
            page = browser.new_page()
            try:
                page.goto(url, timeout=self.timeout_ms, wait_until="domcontentloaded")

                name: str | None = None
                for sel in _TITLE_SELECTORS:
                    el = page.query_selector(sel)
                    if el:
                        name = el.inner_text().strip()
                        break
                if not name:
                    name = page.title().strip()

                price_raw: str | None = None
                for sel in _PRICE_SELECTORS:
                    el = page.query_selector(sel)
                    if el:
                        price_raw = el.inner_text().strip()
                        break
                if not price_raw:
                    raise ScraperError("Price not found on Shopify page")

                price = self._parse_price(price_raw)

                img_el = (
                    page.query_selector(".product__media img")
                    or page.query_selector(".product-featured-media img")
                    or page.query_selector("img[class*='product']")
                )
                image_url = img_el.get_attribute("src") if img_el else None

                return ScrapedProduct(
                    name=name,
                    price=price,
                    currency="USD",
                    in_stock=True,
                    image_url=image_url,
                    raw_price_text=price_raw,
                )
            finally:
                browser.close()
