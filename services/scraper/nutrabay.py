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
    "h1[class*='pdp_pdp_title']",
    "h1.pdp-title",
    "h1",
]

_PRICE_SELECTORS = [
    "[class*='pdp_price_our'] strong",
    "[class*='pdp_price__'] strong",
    "meta[itemprop='price']",
]

_IMAGE_SELECTORS = [
    "[class*='pdp_image'] img",
    "img[itemprop='image']",
    "picture img",
]


class NutrabayScraper(BaseScraper):
    """
    Scrapes Nutrabay product pages: prefers HTTP GET + __NEXT_DATA__ JSON,
    falls back to Playwright if needed.
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
                variant = self._extract_variant_from_next_html(html)
                if variant:
                    result = self._build_from_variant_data(variant)
                    result.scrape_method = "browser"
                    return result

                name = self._extract_name(page)
                price_raw, price = self._extract_price(page)
                image_url = self._extract_image(page)
                in_stock = "out of stock" not in html.lower()

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
            f"Nutrabay scrape failed via requests ({requests_error}) "
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

        variant = self._extract_variant_from_next_html(html)
        if variant:
            result = self._build_from_variant_data(variant)
            result.scrape_method = "requests"
            return result

        price_raw = self._extract_price_from_html(html)
        if not price_raw:
            raise ScraperError("Price not found in Nutrabay requests response")
        price = self._parse_price(price_raw)

        ld = self._extract_ld_json(html)
        name = (
            (ld.get("name") if ld else None)
            or self._extract_meta_content(html, "property", "og:title")
            or self._extract_meta_content(html, "name", "title")
            or "Nutrabay Product"
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

    def _extract_variant_from_next_html(self, html: str) -> dict | None:
        """Parse Next.js __NEXT_DATA__ and return PDP variant payload if present."""
        match = re.search(
            r'<script\s+id=["\']__NEXT_DATA__["\'][^>]*type=["\']application/json["\'][^>]*>(.*?)</script>',
            html,
            re.IGNORECASE | re.DOTALL,
        )
        if not match:
            return None
        try:
            data = json.loads(match.group(1))
        except json.JSONDecodeError:
            return None
        blob = (
            data.get("props", {})
            .get("pageProps", {})
            .get("initialState", {})
            .get("parentProduct", {})
            .get("getVariantDetails", {})
            .get("data")
        )
        if not isinstance(blob, dict):
            return None
        inner = blob.get("data")
        if not isinstance(inner, dict):
            return None
        if inner.get("sale_price") is None:
            return None
        return inner

    def _build_from_variant_data(self, v: dict) -> ScrapedProduct:
        """Build ScrapedProduct from getVariantDetails.data.data."""
        raw_sp = v.get("sale_price")
        if raw_sp is None:
            raise ScraperError("sale_price not found in Nutrabay variant data")
        price = float(raw_sp)

        name = v.get("name") or v.get("parent_name") or "Nutrabay Product"
        if isinstance(name, str):
            name = name.strip()

        image_url = v.get("featured_image") or v.get("thumbnail_image")
        if not image_url and v.get("product_images"):
            first = v["product_images"][0]
            if isinstance(first, dict):
                image_url = first.get("l") or first.get("m") or first.get("s")

        stock = v.get("stock_status")
        if isinstance(stock, bool):
            in_stock = stock
        else:
            sq = v.get("stock_quantity")
            in_stock = int(sq) > 0 if sq is not None else True

        if price == int(price):
            raw_price_text = f"₹{int(price):,}"
        else:
            raw_price_text = f"₹{price}"

        return ScrapedProduct(
            name=name,
            price=price,
            currency="INR",
            in_stock=in_stock,
            image_url=image_url,
            raw_price_text=raw_price_text,
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
        meta_el = page.query_selector("meta[itemprop='price']")
        if meta_el:
            content = meta_el.get_attribute("content")
            if content:
                parsed = re.sub(r"[^\d.]", "", content.replace(",", ""))
                if parsed:
                    return content, self._parse_price(parsed)

        for sel in _PRICE_SELECTORS:
            if sel.startswith("meta"):
                continue
            el = page.query_selector(sel)
            if el:
                txt = el.inner_text().strip()
                parsed = self._extract_price_from_html(txt)
                if parsed:
                    return parsed, self._parse_price(parsed)

        html = page.content()
        parsed = self._extract_price_from_html(html)
        if not parsed:
            raise ScraperError("Price not found on Nutrabay page")
        return parsed, self._parse_price(parsed)

    def _extract_image(self, page) -> str | None:
        for sel in _IMAGE_SELECTORS:
            img_el = page.query_selector(sel)
            if img_el:
                src = img_el.get_attribute("src")
                if src and src.startswith("http"):
                    return src
        return None

    @staticmethod
    def _extract_price_from_html(text: str) -> str | None:
        meta_match = re.search(
            r'<meta\s+[^>]*itemprop=["\']price["\'][^>]*content=["\']([\d,]+(?:\.\d+)?)["\']',
            text,
            re.IGNORECASE,
        )
        if meta_match:
            return meta_match.group(1)

        # Main PDP: <small>₹</small>999 inside pdp_price_our
        pdp_our = re.search(
            r'class="[^"]*pdp_price_our[^"]*"[^>]*>.*?<strong>\s*<small>₹</small>\s*([\d,]+(?:\.\d+)?)',
            text,
            re.IGNORECASE | re.DOTALL,
        )
        if pdp_our:
            return pdp_our.group(1)

        sale_loose = re.search(r'"sale_price"\s*:\s*"?(\d+(?:\.\d+)?)"?', text)
        if sale_loose:
            return sale_loose.group(1)

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
