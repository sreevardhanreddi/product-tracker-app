import json
import re

from playwright.sync_api import sync_playwright

from .base import BaseScraper, ScrapedProduct, ScraperError

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0.0.0 Safari/537.36"
)

_HEADERS = {
    "User-Agent": _USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-IN,en;q=0.9",
}

# Rendered selling price, e.g.
#   <span class="text-xxl font-bold ...">₹13,999</span>
# inside the price row. The MRP sits in a sibling `vip-product-mrp` block, so
# anchoring on the row keeps the struck-through list price out of the match.
_PRICE_ROW_RE = re.compile(
    r"vip-product-price-row.*?>\s*(?:₹|&#8377;|Rs\.?\s*)([\d,]+)",
    re.DOTALL,
)

_PRICE_SELECTORS = [
    ".vip-product-price-row span.font-bold",
    ".vip-product-price-row span",
    "meta[itemprop='price']",
]


class PepperfryScraper(BaseScraper):
    """
    Scrapes Pepperfry product pages.

    The page is server-rendered Angular and carries a complete schema.org
    Product block — name, `offers.price` (the selling price, not the MRP),
    `availability` and the image list — so no browser is needed to read it.

    The catch is Akamai Bot Manager in front of the site: plain `requests` is
    answered `403` on every attempt regardless of headers, and even `HEAD` on a
    URL that serves `200` to `GET` is refused. It is a TLS-fingerprint check,
    so the requests path goes through `curl_cffi` with Chrome impersonation,
    which is answered consistently in ~0.1s. Playwright remains the fallback
    for the day that stops working.
    """

    def scrape(self, url: str) -> ScrapedProduct:
        requests_error: Exception | None = None
        try:
            return self._scrape_via_requests(url)
        except Exception as e:
            requests_error = e

        browser_error: Exception | None = None
        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                headless=self.headless,
                args=["--disable-http2"],
            )
            ctx = browser.new_context(user_agent=_USER_AGENT)
            page = ctx.new_page()
            try:
                self._safe_goto(page, url)
                page.wait_for_timeout(1200)

                html = page.content()
                product_ld = self._extract_product_ld_json(html)
                if product_ld:
                    result = self._build_from_ld_json(product_ld)
                    result.scrape_method = "browser"
                    return result

                name = self._extract_name(page)
                price_raw, price = self._extract_price(page)
                image_url = self._extract_image(page)

                return ScrapedProduct(
                    name=name,
                    price=price,
                    currency="INR",
                    in_stock="out of stock" not in html.lower(),
                    image_url=image_url,
                    raw_price_text=price_raw,
                    scrape_method="browser",
                )
            except Exception as e:
                browser_error = e
            finally:
                browser.close()

        raise ScraperError(
            f"Pepperfry scrape failed via requests ({requests_error}) "
            f"and browser fallback ({browser_error})"
        )

    def _safe_goto(self, page, url: str) -> None:
        attempts = [
            ("domcontentloaded", self.timeout_ms),
            ("commit", self.timeout_ms),
        ]
        last_error: Exception | None = None
        for wait_until, timeout in attempts:
            try:
                page.goto(url, timeout=timeout, wait_until=wait_until)
                return
            except Exception as e:
                last_error = e
        raise ScraperError(f"Page navigation failed: {last_error}")

    def _scrape_via_requests(self, url: str) -> ScrapedProduct:
        html = self._fetch_html(url)

        product_ld = self._extract_product_ld_json(html)
        if product_ld:
            try:
                result = self._build_from_ld_json(product_ld)
                result.scrape_method = "requests"
                return result
            except ScraperError:
                pass  # price missing in ld+json — fall through to HTML parsing

        match = _PRICE_ROW_RE.search(html)
        if not match:
            raise ScraperError("Price not found in Pepperfry requests response")
        price_raw = match.group(1)

        name = (
            self._extract_meta_content(html, "property", "og:title")
            or "Pepperfry Product"
        )

        return ScrapedProduct(
            name=name,
            price=self._parse_price(price_raw),
            currency="INR",
            in_stock="out of stock" not in html.lower(),
            image_url=self._extract_meta_content(html, "property", "og:image"),
            raw_price_text=f"₹{price_raw}",
            scrape_method="requests",
        )

    def _fetch_html(self, url: str) -> str:
        # Deliberately no plain-`requests` attempt first: Akamai answers it 403
        # every time, so trying would only add a round trip before the same
        # impersonated fetch.
        try:
            from curl_cffi import requests as curl_requests
        except ImportError as e:
            raise ScraperError(
                "curl_cffi is required to fetch Pepperfry (Akamai rejects "
                "plain requests)"
            ) from e

        response = curl_requests.get(
            url,
            headers=_HEADERS,
            timeout=30,
            allow_redirects=True,
            impersonate="chrome",
        )
        response.raise_for_status()
        return response.text

    @staticmethod
    def _extract_product_ld_json(html: str) -> dict | None:
        """Return the schema.org Product block; the page also ships a
        BreadcrumbList under the same script type."""
        for match in re.finditer(
            r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
            html,
            re.IGNORECASE | re.DOTALL,
        ):
            try:
                data = json.loads(match.group(1).strip())
            except json.JSONDecodeError:
                continue
            if isinstance(data, dict) and data.get("@type") == "Product":
                return data
        return None

    def _build_from_ld_json(self, data: dict) -> ScrapedProduct:
        offers = data.get("offers")
        if isinstance(offers, list) and offers:
            offers = offers[0]
        if not isinstance(offers, dict):
            raise ScraperError("Product offers not found in ld+json")

        raw_price = offers.get("price") or offers.get("lowPrice")
        if raw_price is None:
            raise ScraperError("Product price not found in ld+json")

        currency = offers.get("priceCurrency") or "INR"
        availability = str(offers.get("availability") or "").lower()
        price = self._parse_price(str(raw_price))

        return ScrapedProduct(
            name=str(data.get("name") or "Pepperfry Product").strip(),
            price=price,
            currency=currency,
            in_stock="outofstock" not in availability if availability else True,
            image_url=self._extract_image_url_from_ld(data.get("image")),
            raw_price_text=f"₹{price:,.0f}" if currency == "INR" else f"{price}",
        )

    @staticmethod
    def _extract_image_url_from_ld(image) -> str | None:
        if isinstance(image, list):
            image = image[0] if image else None
        if isinstance(image, dict):
            return image.get("url") or image.get("contentUrl")
        return image

    @staticmethod
    def _extract_meta_content(html: str, attr: str, value: str) -> str | None:
        match = re.search(
            rf'<meta[^>]+{attr}=["\']{re.escape(value)}["\'][^>]*content=["\']([^"\']+)["\']',
            html,
            re.IGNORECASE,
        )
        return match.group(1).strip() if match else None

    def _extract_name(self, page) -> str:
        for selector in ("h1.vip-product-name", "h1"):
            el = page.query_selector(selector)
            if el:
                text = el.inner_text().strip()
                if text:
                    return text
        return page.title().strip()

    def _extract_price(self, page) -> tuple[str, float]:
        for selector in _PRICE_SELECTORS:
            el = page.query_selector(selector)
            if not el:
                continue
            text = (
                el.get_attribute("content")
                if selector.startswith("meta")
                else el.inner_text()
            )
            if text and any(ch.isdigit() for ch in text):
                return text.strip(), self._parse_price(text)
        raise ScraperError("Price not found on Pepperfry page")

    def _extract_image(self, page) -> str | None:
        for selector in (
            ".vip-product-image img",
            "img[itemprop='image']",
            "picture img",
        ):
            el = page.query_selector(selector)
            if el:
                src = el.get_attribute("src")
                if src:
                    return src
        return None
