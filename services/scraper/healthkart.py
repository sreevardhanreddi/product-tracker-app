import json
import re

import httpx
from playwright.sync_api import sync_playwright

from .base import BaseScraper, ScrapedProduct, ScraperError

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

_TITLE_SELECTORS = [
    "h1.variantInfo_var-info__nm___7cbU",
    "h1.pdp-name",
    "h1.pdp-title",
    "h1",
]

_PRICE_SELECTORS = [
    ".variantInfo_price-value-value__SaH_3",
    ".price-value-value",
    "meta[itemprop='price']",
    ".pdp-discount-container .pdp-price strong",
    ".pdp-price strong",
    ".pdp-price",
    ".pdp-mrp-verbiage .pdp-mrp-verbiage-amt",
]

_IMAGE_SELECTORS = [
    "img.doubleSlickCarousel2_without-zoom__LhKzz",
    "img[itemprop='image']",
    "img.image-grid-image",
    "img.pdp-image",
    "picture img",
]


class HealthKartScraper(BaseScraper):
    """
    Scrapes HealthKart product pages using Playwright.
    """

    def scrape(self, url: str) -> ScrapedProduct:
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
                next_data = self._extract_next_data(html)
                if next_data:
                    return self._build_from_next_data(next_data)

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
                )
            except Exception as e:
                browser_error = e
            finally:
                browser.close()

        try:
            return self._scrape_via_http(url)
        except Exception as http_error:
            raise ScraperError(
                f"HealthKart scrape failed via browser ({browser_error}) "
                f"and HTTP fallback ({http_error})"
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

    def _scrape_via_http(self, url: str) -> ScrapedProduct:
        headers = {
            "User-Agent": _USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-IN,en;q=0.9",
        }
        r = httpx.get(url, headers=headers, timeout=20, follow_redirects=True)
        r.raise_for_status()
        html = r.text

        # Prefer __NEXT_DATA__ (Next.js) - most reliable for HealthKart PDP
        next_data = self._extract_next_data(html)
        if next_data:
            return self._build_from_next_data(next_data)

        price_raw = self._extract_price_from_html(html)
        if not price_raw:
            raise ScraperError("Price not found in HealthKart HTTP response")
        price = self._parse_price(price_raw)

        ld = self._extract_ld_json(html)
        name = (
            (ld.get("name") if ld else None)
            or self._extract_meta_content(html, "property", "og:title")
            or self._extract_meta_content(html, "name", "title")
            or "HealthKart Product"
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
        )

    def _extract_next_data(self, html: str) -> dict | None:
        """Extract product data from Next.js __NEXT_DATA__ script."""
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
        results = (
            data.get("props", {}).get("pageProps", {}).get("data", {}).get("results")
        )
        return results if isinstance(results, dict) else None

    def _build_from_next_data(self, results: dict) -> ScrapedProduct:
        """Build ScrapedProduct from HealthKart __NEXT_DATA__ results."""
        offer_pr = results.get("offer_pr")
        if offer_pr is None:
            raise ScraperError("Price (offer_pr) not found in HealthKart __NEXT_DATA__")
        price = float(offer_pr)

        name = results.get("nm") or results.get("spName") or "HealthKart Product"
        if isinstance(name, str):
            name = name.strip()

        pr_img = results.get("pr_img") or {}
        image_url = pr_img.get("l_link") or pr_img.get("m_link") or pr_img.get("o_link")
        if not image_url and results.get("images"):
            first_img = results["images"][0]
            if isinstance(first_img, dict):
                image_url = (
                    first_img.get("l_link")
                    or first_img.get("m_link")
                    or first_img.get("o_link")
                )

        oos = results.get("oos", False)
        in_stock = not oos

        return ScrapedProduct(
            name=name,
            price=price,
            currency="INR",
            in_stock=in_stock,
            image_url=image_url,
            raw_price_text=f"₹{int(price):,}",
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
        # meta[itemprop='price'] uses content attribute, not inner_text
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

        # Fallback to full HTML text parsing for React-commented fragments
        html = page.content()
        parsed = self._extract_price_from_html(html)
        if not parsed:
            raise ScraperError("Price not found on HealthKart page")
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
        # Schema.org meta: <meta itemprop="price" content="1399">
        meta_match = re.search(
            r'<meta\s+[^>]*itemprop=["\']price["\'][^>]*content=["\']([\d,]+(?:\.\d+)?)["\']',
            text,
            re.IGNORECASE,
        )
        if meta_match:
            return meta_match.group(1)

        # HealthKart variant price: variantInfo_price-value-value__SaH_3 or price-value-value
        # e.g. <!-- -->₹<!-- -->1,399
        variant_match = re.search(
            r'class=["\'][^"\']*variantInfo_price-value-value[^"\']*["\'][^>]*>\s*(?:<!--[^>]*-->)?\s*(?:Rs\.?|₹)?\s*(?:<!--[^>]*-->)?\s*([\d,]+(?:\.\d+)?)',
            text,
            re.IGNORECASE,
        )
        if variant_match:
            return variant_match.group(1)

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
