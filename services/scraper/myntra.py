import json
import re

import requests
from playwright.sync_api import sync_playwright

from .base import BaseScraper, ScrapedProduct, ScraperError

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

_TITLE_SELECTORS = [
    "h1.pdp-name",
    "h1.pdp-title",
    "h1",
]

_PRICE_SELECTORS = [
    ".pdp-discount-container .pdp-price strong",
    ".pdp-price strong",
    ".pdp-price",
    # Selling Price from the MRP verbiage block
    ".pdp-mrp-verbiage .pdp-mrp-verbiage-amt",
]


class MyntraScraper(BaseScraper):
    """
    Scrapes Myntra product pages using Playwright.
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

                name = self._extract_name(page)
                price_raw, price = self._extract_price(page)
                image_url = self._extract_image(page)
                in_stock = "out of stock" not in page.content().lower()

                return ScrapedProduct(
                    name=name,
                    price=price,
                    currency="INR",
                    in_stock=in_stock,
                    image_url=image_url,
                    raw_price_text=price_raw,
                    scrape_method="browser",
                )
            except Exception as e:
                browser_error = e
            finally:
                browser.close()

        raise ScraperError(
            f"Myntra scrape failed via requests ({requests_error}) "
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
        headers = {
            "User-Agent": _USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-IN,en;q=0.9",
        }
        r = requests.get(url, headers=headers, timeout=20, allow_redirects=True)
        r.raise_for_status()
        html = r.text

        price_raw = self._extract_price_from_html(html)
        if not price_raw:
            raise ScraperError("Price not found in Myntra requests response")
        price = self._parse_price(price_raw)

        ld = self._extract_ld_json(html)
        name = (
            (ld.get("name") if ld else None)
            or self._extract_meta_content(html, "property", "og:title")
            or self._extract_meta_content(html, "name", "title")
            or "Myntra Product"
        )
        image_url = (ld.get("image") if ld else None) or self._extract_meta_content(
            html, "property", "og:image"
        )
        in_stock = (
            (ld.get("offers", {}).get("availability", "").lower() != "outofstock")
            if ld
            else "out of stock" not in html.lower()
        )

        return ScrapedProduct(
            name=name,
            price=price,
            currency="INR",
            in_stock=in_stock,
            image_url=image_url,
            raw_price_text=price_raw,
            scrape_method="requests",
        )

    def _extract_name(self, page) -> str:
        for sel in _TITLE_SELECTORS:
            el = page.query_selector(sel)
            if el:
                txt = el.inner_text().strip()
                if txt:
                    return txt
        return page.title().strip()

    def _extract_price(self, page) -> tuple[str, float]:
        for sel in _PRICE_SELECTORS:
            el = page.query_selector(sel)
            if el:
                txt = el.inner_text().strip()
                parsed = self._extract_price_from_html(txt)
                if parsed:
                    return parsed, self._parse_price(parsed)

        # Fallback to full HTML text parsing for React-commented fragments
        html = page.content()
        parsed = self._extract_price_from_html(html)
        if not parsed:
            raise ScraperError("Price not found on Myntra page")
        return parsed, self._parse_price(parsed)

    def _extract_image(self, page) -> str | None:
        img_el = (
            page.query_selector("img.image-grid-image")
            or page.query_selector("img.pdp-image")
            or page.query_selector("picture img")
        )
        if not img_el:
            return None
        return img_el.get_attribute("src")

    @staticmethod
    def _extract_price_from_html(text: str) -> str | None:
        # Prefer explicit selling price from verbiage block:
        # <b>Selling Price</b><span class="pdp-mrp-verbiage-amt">Rs. 1799</span>
        selling_match = re.search(
            r"Selling\s*Price\s*</b>\s*<span[^>]*>\s*(?:Rs\.?|₹)\s*([\d,]+(?:\.\d+)?)",
            text,
            re.IGNORECASE,
        )
        if selling_match:
            return selling_match.group(1)

        # Main price node: <span class="pdp-price" ...><strong>₹1799</strong></span>
        # Use [^>]* to tolerate any extra attributes (e.g. tabindex="0").
        pdp_match = re.search(
            r'class=["\']pdp-price["\'][^>]*>\s*<strong>\s*(?:Rs\.?|₹)\s*([\d,]+(?:\.\d+)?)\s*</strong>',
            text,
            re.IGNORECASE,
        )
        if pdp_match:
            return pdp_match.group(1)

        # Plain-text path (used when inner_text() is passed instead of raw HTML):
        # e.g. "₹1799" or "Rs. 1799"
        plain_match = re.match(
            r"^\s*(?:Rs\.?|₹)\s*([\d,]+(?:\.\d+)?)\s*$",
            text,
            re.IGNORECASE,
        )
        if plain_match:
            return plain_match.group(1)

        # ld+json fallback: <script type="application/ld+json"> with offers.price
        for ld_match in re.finditer(
            r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
            text,
            re.IGNORECASE | re.DOTALL,
        ):
            try:
                data = json.loads(ld_match.group(1))
            except json.JSONDecodeError:
                continue
            offers = data.get("offers") if isinstance(data, dict) else None
            if isinstance(offers, dict):
                price = offers.get("price")
                if price is not None:
                    return str(price).replace(",", "")

        # Generic fallback: first currency+number in the fragment.
        generic_match = re.search(
            r"(?:Rs\.?|₹)\s*([\d,]+(?:\.\d+)?)",
            text,
            re.IGNORECASE,
        )
        if generic_match:
            return generic_match.group(1)

        return None

    @staticmethod
    def _extract_ld_json(html: str) -> dict | None:
        """Return the first Product ld+json block found in the page, or None."""
        for m in re.finditer(
            r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
            html,
            re.IGNORECASE | re.DOTALL,
        ):
            try:
                data = json.loads(m.group(1))
            except json.JSONDecodeError:
                continue
            if isinstance(data, dict) and data.get("@type") == "Product":
                return data
        return None

    @staticmethod
    def _extract_meta_content(html: str, attr: str, value: str) -> str | None:
        match = re.search(
            rf'<meta\s+[^>]*{attr}=["\']{re.escape(value)}["\'][^>]*content=["\']([^"\']+)["\']',
            html,
            re.IGNORECASE,
        )
        if match:
            return match.group(1).strip()
        return None
