import html as html_lib
import json
import re

import requests
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

_TITLE_SELECTORS = [
    "[data-hook='product-title']",
    "h1",
]

_PRICE_SELECTORS = [
    "[data-hook='formatted-primary-price']",
    "[data-hook='product-price']",
    "[data-hook='product-price-after-discount']",
    "span:has-text('₹')",
]

_IMAGE_SELECTORS = [
    "[data-hook='product-image'] img",
    "main img",
    "meta[property='og:image']",
]


class WixScraper(BaseScraper):
    """Scrapes Wix product pages such as hydrotech3dchennai.com."""

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
                page.wait_for_timeout(2000)

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
                    in_stock=self._extract_stock_from_text(html),
                    image_url=image_url,
                    raw_price_text=price_raw,
                    scrape_method="browser",
                )
            except Exception as e:
                browser_error = e
            finally:
                browser.close()

        raise ScraperError(
            f"Wix scrape failed via requests ({requests_error}) "
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
        response = requests.get(
            url,
            headers=_HEADERS,
            timeout=20,
            allow_redirects=True,
        )
        response.raise_for_status()
        html = response.text

        product_ld = self._extract_product_ld_json(html)
        if product_ld:
            result = self._build_from_ld_json(product_ld)
            result.scrape_method = "requests"
            return result

        name = (
            self._extract_meta_content(html, "property", "og:title")
            or self._extract_title_from_html(html)
            or "Wix Product"
        )
        price_raw = self._extract_price_from_html(html)
        if not price_raw:
            raise ScraperError("Price not found in Wix requests response")

        return ScrapedProduct(
            name=name,
            price=self._parse_price(price_raw),
            currency="INR",
            in_stock=self._extract_stock_from_text(html),
            image_url=self._extract_meta_content(html, "property", "og:image"),
            raw_price_text=price_raw,
            scrape_method="requests",
        )

    def _build_from_ld_json(self, data: dict) -> ScrapedProduct:
        offers = data.get("offers") or data.get("Offers")
        offer = offers[0] if isinstance(offers, list) and offers else offers
        if not isinstance(offer, dict):
            raise ScraperError("Product offers not found in ld+json")

        raw_price = offer.get("price") or data.get("price")
        if raw_price is None:
            raise ScraperError("Product price not found in ld+json")

        image_url = self._extract_image_url_from_ld(data.get("image"))

        availability = str(
            offer.get("availability") or offer.get("Availability") or ""
        ).lower()
        in_stock = "outofstock" not in availability if availability else True
        currency = offer.get("priceCurrency") or "INR"

        price = self._parse_price(str(raw_price))

        return ScrapedProduct(
            name=str(data.get("name") or "Wix Product").strip(),
            price=price,
            currency=currency,
            in_stock=in_stock,
            image_url=image_url,
            raw_price_text=self._format_price(price, currency),
        )

    @staticmethod
    def _extract_image_url_from_ld(image) -> str | None:
        if isinstance(image, list):
            if not image:
                return None
            return WixScraper._extract_image_url_from_ld(image[0])
        if isinstance(image, dict):
            return image.get("url") or image.get("contentUrl")
        return image

    def _extract_name(self, page) -> str:
        for selector in _TITLE_SELECTORS:
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
            price_raw = self._extract_price_from_html(el.inner_text())
            if price_raw:
                return price_raw, self._parse_price(price_raw)

        price_raw = self._extract_price_from_html(page.content())
        if not price_raw:
            raise ScraperError("Price not found on Wix page")
        return price_raw, self._parse_price(price_raw)

    def _extract_image(self, page) -> str | None:
        for selector in _IMAGE_SELECTORS:
            el = page.query_selector(selector)
            if not el:
                continue
            src = (
                el.get_attribute("content")
                if selector.startswith("meta")
                else el.get_attribute("src")
            )
            if src:
                return src
        return None

    @staticmethod
    def _extract_product_ld_json(html: str) -> dict | None:
        for match in re.finditer(
            r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
            html,
            re.IGNORECASE | re.DOTALL,
        ):
            raw = html_lib.unescape(match.group(1)).strip()
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                continue
            product = WixScraper._find_product_node(data)
            if product:
                return product
        return None

    @staticmethod
    def _find_product_node(data) -> dict | None:
        if isinstance(data, list):
            for item in data:
                product = WixScraper._find_product_node(item)
                if product:
                    return product
        if not isinstance(data, dict):
            return None
        node_type = data.get("@type")
        if node_type == "Product" or (
            isinstance(node_type, list) and "Product" in node_type
        ):
            return data
        graph = data.get("@graph")
        if isinstance(graph, list):
            for item in graph:
                product = WixScraper._find_product_node(item)
                if product:
                    return product
        return None

    @staticmethod
    def _extract_price_from_html(text: str) -> str | None:
        text = html_lib.unescape(text)
        main_price = re.search(
            r"Price\s*(?:</[^>]+>\s*)*(?:<[^>]+>\s*)*(₹\s*[\d,]+(?:\.\d+)?)",
            text,
            re.IGNORECASE | re.DOTALL,
        )
        if main_price:
            return main_price.group(1).strip()

        generic = re.search(r"₹\s*[\d,]+(?:\.\d+)?", text)
        return generic.group(0).strip() if generic else None

    @staticmethod
    def _extract_meta_content(html: str, attr: str, value: str) -> str | None:
        match = re.search(
            rf'<meta\s+[^>]*{attr}=["\']{re.escape(value)}["\'][^>]*content=["\']([^"\']+)["\']',
            html,
            re.IGNORECASE,
        )
        return html_lib.unescape(match.group(1)).strip() if match else None

    @staticmethod
    def _extract_title_from_html(html: str) -> str | None:
        match = re.search(
            r"<title[^>]*>(.*?)</title>",
            html,
            re.IGNORECASE | re.DOTALL,
        )
        if not match:
            return None
        title = re.sub(r"\s+", " ", html_lib.unescape(match.group(1))).strip()
        return title or None

    @staticmethod
    def _extract_stock_from_text(text: str) -> bool:
        lowered = text.lower()
        return "out of stock" not in lowered and "sold out" not in lowered

    @staticmethod
    def _format_price(price: float, currency: str) -> str:
        symbol = "₹" if currency == "INR" else f"{currency} "
        if price == int(price):
            return f"{symbol}{int(price):,}"
        return f"{symbol}{price:,.2f}"
