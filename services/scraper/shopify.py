from urllib.parse import parse_qs, urlparse

import requests
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
       Stock status comes from a second request to /products/{handle}.js,
       since the .json endpoint strips per-variant `available`.
    2. Fall back to Playwright if the JSON endpoint fails or returns
       unexpected data (e.g., store requires login or handle differs).
    """

    def scrape(self, url: str) -> ScrapedProduct:
        try:
            return self._scrape_requests(url)
        except Exception:
            pass
        return self._scrape_playwright(url)

    def _scrape_requests(self, url: str) -> ScrapedProduct:
        parsed = urlparse(url)
        handle = parsed.path.rstrip("/").split("/")[-1]
        api_url = f"{parsed.scheme}://{parsed.hostname}/products/{handle}.json"
        variant_id = self._extract_variant_id(url)

        r = requests.get(
            api_url,
            headers=_JSON_HEADERS,
            timeout=10,
            allow_redirects=True,
        )
        r.raise_for_status()
        data = r.json().get("product")
        if not data:
            raise ScraperError("No 'product' key in Shopify JSON response")

        variant = self._select_variant(data.get("variants", []), variant_id)
        price = float(variant["price"])
        in_stock = self._fetch_stock_status(parsed, handle, variant.get("id"))
        image_url = self._select_image_url(data, variant)
        # Shopify JSON doesn't always expose currency; fall back to USD
        currency = variant.get("price_currency") or "USD"

        return ScrapedProduct(
            name=data["title"],
            price=price,
            currency=currency,
            in_stock=in_stock,
            image_url=image_url,
            raw_price_text=variant["price"],
            scrape_method="requests",
        )

    @staticmethod
    def _fetch_stock_status(parsed, handle: str, variant_id) -> bool:
        """
        Fetch per-variant stock status from the .js endpoint, since the
        public .json endpoint strips `available`. Best-effort: if the
        request fails, assume in stock rather than failing the scrape.
        """
        js_url = f"{parsed.scheme}://{parsed.hostname}/products/{handle}.js"
        try:
            r = requests.get(
                js_url,
                headers=_JSON_HEADERS,
                timeout=10,
                allow_redirects=True,
            )
            r.raise_for_status()
            data = r.json()
            for variant in data.get("variants", []):
                if str(variant.get("id")) == str(variant_id):
                    return bool(variant.get("available", False))
            return bool(data.get("available", True))
        except Exception:
            return True

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

                in_stock = True
                add_btn = page.query_selector(
                    "button[name='add'], .product-form__submit, [data-add-to-cart]"
                )
                if add_btn:
                    btn_text = add_btn.inner_text().strip().lower()
                    if (
                        add_btn.is_disabled()
                        or "sold out" in btn_text
                        or "unavailable" in btn_text
                        or "out of stock" in btn_text
                    ):
                        in_stock = False

                return ScrapedProduct(
                    name=name,
                    price=price,
                    currency="USD",
                    in_stock=in_stock,
                    image_url=image_url,
                    raw_price_text=price_raw,
                    scrape_method="browser",
                )
            finally:
                browser.close()

    @staticmethod
    def _extract_variant_id(url: str) -> str | None:
        query = parse_qs(urlparse(url).query)
        variant_ids = query.get("variant")
        return variant_ids[0] if variant_ids else None

    @staticmethod
    def _select_variant(variants: list[dict], variant_id: str | None) -> dict:
        if not variants:
            raise ScraperError("No variants found in Shopify JSON response")

        if variant_id:
            for variant in variants:
                if str(variant.get("id")) == variant_id:
                    return variant

        return variants[0]

    @staticmethod
    def _select_image_url(data: dict, variant: dict) -> str | None:
        image_id = variant.get("image_id")
        if image_id and data.get("images"):
            for image in data["images"]:
                if str(image.get("id")) == str(image_id):
                    return image.get("src")

        if variant.get("featured_image"):
            featured_image = variant["featured_image"]
            if isinstance(featured_image, dict):
                return featured_image.get("src")

        if data.get("image"):
            image = data["image"]
            if isinstance(image, dict):
                return image.get("src")

        if data.get("images"):
            first_image = data["images"][0]
            if isinstance(first_image, dict):
                return first_image.get("src")

        return None
