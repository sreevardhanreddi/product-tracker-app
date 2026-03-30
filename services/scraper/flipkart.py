import json
import re

import requests
from playwright.sync_api import sync_playwright

from .base import BaseScraper, ScrapedProduct, ScraperError

_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

_REQUESTS_HEADERS = {
    "User-Agent": _USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-IN,en;q=0.9",
}


class FlipkartScraper(BaseScraper):
    """
    Scrapes Flipkart product pages.

    Strategy:
    1. Try requests library with browser user-agent, parsing JSON-LD from HTML.
    2. Fall back to Playwright if requests fails or yields incomplete data.

    Price is always INR on Flipkart.
    Stock: absence of the "NOTIFY ME" button indicates the item is in stock.
    """

    def scrape(self, url: str) -> ScrapedProduct:
        try:
            return self._scrape_via_requests(url)
        except Exception:
            pass
        return self._scrape_via_browser(url)

    def _scrape_via_requests(self, url: str) -> ScrapedProduct:
        r = requests.get(
            url, headers=_REQUESTS_HEADERS, timeout=20, allow_redirects=True
        )
        r.raise_for_status()
        html = r.text

        jsonld = self._extract_jsonld_from_html(html)
        name: str | None = None
        price_raw: str | None = None
        price: float | None = None
        image_url: str | None = None
        in_stock: bool | None = None

        if jsonld:
            if jsonld.get("name"):
                name = str(jsonld["name"]).strip()

            offers = jsonld.get("offers", {}) if isinstance(jsonld, dict) else {}
            offer_price = offers.get("price")
            if offer_price is not None:
                price_raw = str(offer_price)
                price = self._parse_price(price_raw)

            avail = str(offers.get("availability", "")).lower()
            if "outofstock" in avail:
                in_stock = False
            elif "instock" in avail:
                in_stock = True

            images = jsonld.get("image")
            if isinstance(images, list) and images:
                image_url = images[0]
            elif isinstance(images, str):
                image_url = images

        if not name:
            raise ScraperError("Product title not found in Flipkart requests response")

        if price is None:
            raise ScraperError("Price not found in Flipkart requests response")

        if in_stock is None:
            in_stock = "notify me" not in html.lower()

        return ScrapedProduct(
            name=name,
            price=price,
            currency="INR",
            in_stock=in_stock,
            image_url=image_url,
            raw_price_text=price_raw,
            scrape_method="requests",
        )

    def _scrape_via_browser(self, url: str) -> ScrapedProduct:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=self.headless)
            ctx = browser.new_context(user_agent=_USER_AGENT)
            page = ctx.new_page()
            try:
                page.goto(url, timeout=self.timeout_ms, wait_until="domcontentloaded")

                # Dismiss the login popup if it appears
                close_btn = page.query_selector("button._2KpZ6l._2doB4z")
                if close_btn:
                    close_btn.click()
                    page.wait_for_timeout(500)

                # Prefer JSON-LD from page source when available.
                jsonld = self._extract_jsonld_product(page)
                name: str | None = None
                price_raw: str | None = None
                price: float | None = None
                image_url: str | None = None
                in_stock: bool | None = None

                if jsonld:
                    if jsonld.get("name"):
                        name = str(jsonld["name"]).strip()

                    offers = (
                        jsonld.get("offers", {}) if isinstance(jsonld, dict) else {}
                    )
                    offer_price = offers.get("price")
                    if offer_price is not None:
                        price_raw = str(offer_price)
                        price = self._parse_price(price_raw)

                    avail = str(offers.get("availability", "")).lower()
                    if "outofstock" in avail:
                        in_stock = False
                    elif "instock" in avail:
                        in_stock = True

                    images = jsonld.get("image")
                    if isinstance(images, list) and images:
                        image_url = images[0]
                    elif isinstance(images, str):
                        image_url = images

                if not name:
                    raise ScraperError("Product title not found in Flipkart JSON-LD")

                # Price fallback to DOM selectors
                if price is None:
                    price_el = (
                        page.query_selector(
                            ".v1zwn21j:has-text('₹')"
                        )  # new Flipkart UI (selling price)
                        or page.query_selector("._30jeq3._16Jk6d")  # old UI (specific)
                        or page.query_selector("._30jeq3")  # old UI (general)
                    )
                    if not price_el:
                        raise ScraperError("Price not found on Flipkart page")
                    price_raw = price_el.inner_text().strip()
                    price = self._parse_price(price_raw)

                # Out-of-stock: Flipkart shows a "NOTIFY ME" button when unavailable
                if in_stock is None:
                    in_stock = page.query_selector("._2AkGiR") is None

                # Product image
                if not image_url:
                    img_el = page.query_selector("._396cs4 img") or page.query_selector(
                        "._2r_T1I img"
                    )
                    if img_el:
                        image_url = img_el.get_attribute("src")

                return ScrapedProduct(
                    name=name,
                    price=price,
                    currency="INR",
                    in_stock=in_stock,
                    image_url=image_url,
                    raw_price_text=price_raw,
                    scrape_method="browser",
                )
            finally:
                browser.close()

    def _extract_jsonld_from_html(self, html: str) -> dict | None:
        """Extract Product JSON-LD from raw HTML text."""
        for m in re.finditer(
            r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
            html,
            re.IGNORECASE | re.DOTALL,
        ):
            text = m.group(1).strip()
            if not text:
                continue
            try:
                payload = json.loads(text)
            except Exception:
                payload = self._try_parse_partial_jsonld(text)
            product = self._find_product_node(payload)
            if product:
                return product
        return None

    def _extract_jsonld_product(self, page) -> dict | None:
        scripts = page.query_selector_all("script[type='application/ld+json']")
        for script in scripts:
            text = (script.inner_text() or "").strip()
            if not text:
                continue
            try:
                payload = json.loads(text)
            except Exception:
                # Some pages embed malformed JSON-LD; try to salvage Product object by regex.
                payload = self._try_parse_partial_jsonld(text)

            product = self._find_product_node(payload)
            if product:
                return product
        return None

    def _try_parse_partial_jsonld(self, text: str):
        match = re.search(r"\{.*\"@type\"\s*:\s*\"Product\".*\}", text, re.DOTALL)
        if not match:
            return None
        try:
            return json.loads(match.group(0))
        except Exception:
            return None

    def _find_product_node(self, payload):
        if payload is None:
            return None
        if isinstance(payload, dict):
            t = str(payload.get("@type", "")).lower()
            if t == "product":
                return payload
            graph = payload.get("@graph")
            if isinstance(graph, list):
                for node in graph:
                    if (
                        isinstance(node, dict)
                        and str(node.get("@type", "")).lower() == "product"
                    ):
                        return node
            return None
        if isinstance(payload, list):
            for item in payload:
                found = self._find_product_node(item)
                if found:
                    return found
        return None
