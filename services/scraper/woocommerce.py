import html as html_lib
import json
import re
import time
from urllib.parse import urlparse

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
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
    "Upgrade-Insecure-Requests": "1",
}

_TITLE_SELECTORS = [
    "h1.product_title",
    "h1.entry-title",
    "h1",
]

_PRICE_SELECTORS = [
    ".summary p.price .woocommerce-Price-amount",
    ".summary .price .woocommerce-Price-amount",
    "p.price .woocommerce-Price-amount",
    ".price .woocommerce-Price-amount",
    "meta[property='product:price:amount']",
    "meta[itemprop='price']",
]

_IMAGE_SELECTORS = [
    ".woocommerce-product-gallery__image img",
    "img.wp-post-image",
    "meta[property='og:image']",
]


class WooCommerceScraper(BaseScraper):
    """Scrapes WooCommerce product pages such as Robu and WOL3D."""

    def scrape(self, url: str) -> ScrapedProduct:
        requests_error: Exception | None = None
        try:
            return self._scrape_via_requests(url)
        except Exception as e:
            requests_error = e

        browser_error: Exception | None = None
        # Firefox bypasses Cloudflare Turnstile more reliably than Chromium.
        # We try Firefox first; Chromium is only attempted if Firefox fails to
        # *load* the page (launch error, navigation error, or CF not cleared).
        # Once any browser gets past Cloudflare we stop the loop immediately —
        # a data-extraction failure at that point won't be fixed by Chromium.
        with sync_playwright() as pw:
            for browser_type, launch_kwargs in [
                (pw.firefox, {"headless": self.headless}),
                (pw.chromium, {"headless": self.headless, "args": ["--disable-http2"]}),
            ]:
                try:
                    browser = browser_type.launch(**launch_kwargs)
                except Exception:
                    continue

                ctx = browser.new_context(user_agent=_USER_AGENT)
                page = ctx.new_page()
                page_loaded = False
                try:
                    self._safe_goto(page, url)
                    self._wait_for_cloudflare_challenge(page)
                    if self._looks_like_cloudflare_challenge(page):
                        # CF still active — try the next browser type
                        raise ScraperError(
                            "Cloudflare bot verification did not clear automatically"
                        )

                    # Page is real — don't open a second browser even if
                    # data extraction below fails.
                    page_loaded = True
                    page.wait_for_timeout(1200)

                    product_ld = self._extract_product_ld_json(page.content())
                    if product_ld:
                        try:
                            result = self._build_from_ld_json(product_ld)
                            result.scrape_method = "browser"
                            return result
                        except ScraperError:
                            pass  # price missing in ld+json — fall through to DOM

                    name = self._extract_name(page)
                    price_raw, price = self._extract_price(page)
                    image_url = self._extract_image(page)
                    in_stock = self._extract_stock_from_text(page.content())

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
                    if page_loaded:
                        # The page loaded fine; a second browser won't help.
                        raise ScraperError(
                            f"WooCommerce data extraction failed after page load: {e}"
                        ) from e
                    browser_error = e
                finally:
                    browser.close()

        raise ScraperError(
            f"WooCommerce scrape failed via requests ({requests_error}) "
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

    def _wait_for_cloudflare_challenge(self, page) -> None:
        """
        Some WooCommerce stores sit behind Cloudflare. If the JS challenge is
        auto-solvable, give it time to redirect before reading product markup.
        """
        if not self._looks_like_cloudflare_challenge(page):
            return

        deadline = time.monotonic() + max(self.timeout_ms / 1000, 60)
        while time.monotonic() < deadline:
            page.wait_for_timeout(1000)
            if not self._looks_like_cloudflare_challenge(page):
                try:
                    page.wait_for_load_state("domcontentloaded", timeout=5000)
                except Exception:
                    pass
                return

    @staticmethod
    def _looks_like_cloudflare_challenge(page) -> bool:
        try:
            title = page.title().lower()
            body = page.locator("body").inner_text(timeout=1000).lower()
        except Exception:
            return False

        markers = [
            "just a moment",
            "checking if the site connection is secure",
            "verify you are human",
            "verifying you are human",
            "needs to review the security",
            "cf-browser-verification",
            "performing security verification",
            "security service to protect against malicious bots",
            "this page is displayed while the website verifies you are not a bot",
        ]
        text = f"{title}\n{body}"
        return any(marker in text for marker in markers)

    @staticmethod
    def _looks_like_cloudflare_html(html: str) -> bool:
        lowered = html.lower()
        markers = [
            "just a moment",
            "checking if the site connection is secure",
            "verify you are human",
            "verifying you are human",
            "needs to review the security",
            "cf-browser-verification",
            "/cdn-cgi/challenge-platform/",
            "performing security verification",
            "security service to protect against malicious bots",
            "this page is displayed while the website verifies you are not a bot",
        ]
        return any(marker in lowered for marker in markers)

    def _scrape_via_requests(self, url: str) -> ScrapedProduct:
        store_api_result = self._scrape_via_store_api(url)
        if store_api_result:
            return store_api_result

        html = self._fetch_html(url)

        product_ld = self._extract_product_ld_json(html)
        if product_ld:
            try:
                result = self._build_from_ld_json(product_ld)
                result.scrape_method = "requests"
                return result
            except ScraperError:
                pass  # price missing in ld+json — fall through to HTML parsing

        price_raw = self._extract_price_from_html(html)
        if not price_raw:
            raise ScraperError("Price not found in WooCommerce requests response")

        name = (
            self._extract_meta_content(html, "property", "og:title")
            or self._extract_title_from_html(html)
            or "WooCommerce Product"
        )
        image_url = self._extract_meta_content(html, "property", "og:image")

        return ScrapedProduct(
            name=name,
            price=self._parse_price(price_raw),
            currency="INR",
            in_stock=self._extract_stock_from_text(html),
            image_url=image_url,
            raw_price_text=price_raw,
            scrape_method="requests",
        )

    def _fetch_html(self, url: str) -> str:
        response = requests.get(
            url,
            headers=_HEADERS,
            timeout=20,
            allow_redirects=True,
        )
        if response.ok and not self._looks_like_cloudflare_html(response.text):
            return response.text

        curl_html = self._fetch_html_via_curl_cffi(url)
        if curl_html and not self._looks_like_cloudflare_html(curl_html):
            return curl_html

        response.raise_for_status()
        return response.text

    def _scrape_via_store_api(self, url: str) -> ScrapedProduct | None:
        parsed = urlparse(url)
        slug = parsed.path.rstrip("/").split("/")[-1]
        if not slug:
            return None

        api_url = f"{parsed.scheme}://{parsed.netloc}/wp-json/wc/store/v1/products"
        params = {"slug": slug}
        data = self._fetch_store_api_json(api_url, params)
        if not isinstance(data, list) or not data:
            return None

        product = data[0]
        prices = product.get("prices") or {}
        raw_price = (
            prices.get("price")
            or prices.get("sale_price")
            or prices.get("regular_price")
        )
        if raw_price is None:
            return None

        price = self._parse_store_api_price(raw_price, prices)
        currency = prices.get("currency_code") or "INR"
        image_url = self._extract_store_api_image(product)
        in_stock = bool(product.get("is_in_stock", True))

        return ScrapedProduct(
            name=str(product.get("name") or "WooCommerce Product").strip(),
            price=price,
            currency=currency,
            in_stock=in_stock,
            image_url=image_url,
            raw_price_text=self._format_price(price, currency),
            scrape_method="requests",
        )

    def _fetch_store_api_json(self, api_url: str, params: dict) -> list | dict | None:
        try:
            response = requests.get(
                api_url,
                params=params,
                headers={
                    **_HEADERS,
                    "Accept": "application/json,text/plain,*/*",
                },
                timeout=20,
                allow_redirects=True,
            )
            if response.ok and not self._looks_like_cloudflare_html(response.text):
                return response.json()
        except Exception:
            pass

        try:
            from curl_cffi import requests as curl_requests
        except ImportError:
            return None

        response = curl_requests.get(
            api_url,
            params=params,
            headers={
                **_HEADERS,
                "Accept": "application/json,text/plain,*/*",
            },
            timeout=30,
            allow_redirects=True,
            impersonate="chrome",
        )
        response.raise_for_status()
        if self._looks_like_cloudflare_html(response.text):
            return None
        return response.json()

    def _fetch_html_via_curl_cffi(self, url: str) -> str | None:
        try:
            from curl_cffi import requests as curl_requests
        except ImportError:
            return None

        response = curl_requests.get(
            url,
            headers=_HEADERS,
            timeout=30,
            allow_redirects=True,
            impersonate="chrome",
        )
        response.raise_for_status()
        return response.text

    def _build_from_ld_json(self, data: dict) -> ScrapedProduct:
        offers = data.get("offers") or data.get("Offers")
        offer = offers[0] if isinstance(offers, list) and offers else offers
        if not isinstance(offer, dict):
            raise ScraperError("Product offers not found in ld+json")

        # Some WooCommerce themes (e.g. wol3d) nest the price inside
        # priceSpecification rather than directly on the Offer.
        price_spec = offer.get("priceSpecification")
        if isinstance(price_spec, list) and price_spec:
            price_spec = price_spec[0]
        if isinstance(price_spec, dict):
            spec_price = price_spec.get("price") or price_spec.get("minPrice")
            spec_currency = price_spec.get("priceCurrency")
        else:
            spec_price = None
            spec_currency = None

        raw_price = (
            offer.get("price")
            or offer.get("lowPrice")
            or offer.get("highPrice")
            or spec_price
            or data.get("price")
        )
        if raw_price is None:
            raise ScraperError("Product price not found in ld+json")

        image_url = self._extract_image_url_from_ld(data.get("image"))

        availability = str(
            offer.get("availability") or offer.get("Availability") or ""
        ).lower()
        in_stock = "outofstock" not in availability if availability else True
        currency = offer.get("priceCurrency") or spec_currency or "INR"
        price = self._parse_price(str(raw_price))
        raw_price_text = self._format_price(price, currency)

        return ScrapedProduct(
            name=str(data.get("name") or "WooCommerce Product").strip(),
            price=price,
            currency=currency,
            in_stock=in_stock,
            image_url=image_url,
            raw_price_text=raw_price_text,
        )

    def _parse_store_api_price(self, raw_price, prices: dict) -> float:
        raw_text = str(raw_price)
        if "." in raw_text:
            return self._parse_price(raw_text)

        price = self._parse_price(raw_text)
        minor_unit = prices.get("currency_minor_unit")
        try:
            minor_unit_int = int(minor_unit)
        except (TypeError, ValueError):
            minor_unit_int = 0

        if minor_unit_int > 0:
            return price / (10**minor_unit_int)
        return price

    @staticmethod
    def _extract_store_api_image(product: dict) -> str | None:
        images = product.get("images")
        if not isinstance(images, list) or not images:
            return None

        first = images[0]
        if isinstance(first, dict):
            return first.get("src") or first.get("thumbnail")
        return None

    @staticmethod
    def _extract_image_url_from_ld(image) -> str | None:
        if isinstance(image, list):
            if not image:
                return None
            return WooCommerceScraper._extract_image_url_from_ld(image[0])
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
            text = (
                el.get_attribute("content")
                if selector.startswith("meta")
                else el.inner_text()
            )
            price_raw = self._extract_price_from_html(text or "")
            if price_raw:
                return price_raw, self._parse_price(price_raw)

        price_raw = self._extract_price_from_html(page.content())
        if not price_raw:
            raise ScraperError("Price not found on WooCommerce page")
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
            product = WooCommerceScraper._find_product_node(data)
            if product:
                return product
        return None

    @staticmethod
    def _find_product_node(data) -> dict | None:
        if isinstance(data, list):
            for item in data:
                product = WooCommerceScraper._find_product_node(item)
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
                product = WooCommerceScraper._find_product_node(item)
                if product:
                    return product
        return None

    @staticmethod
    def _extract_price_from_html(text: str) -> str | None:
        text = html_lib.unescape(text)
        patterns = [
            r'<meta\s+[^>]*(?:property=["\']product:price:amount["\']|itemprop=["\']price["\'])[^>]*content=["\']([^"\']+)["\']',
            r'class=["\'][^"\']*woocommerce-Price-amount[^"\']*["\'][^>]*>.*?(?:₹|Rs\.?)\s*([\d,]+(?:\.\d+)?)',
            r"(?:₹|Rs\.?)\s*([\d,]+(?:\.\d+)?)",
        ]
        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
            if match:
                return match.group(1).strip()
        return None

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
        if "out of stock" in lowered:
            return False
        if "read more" in lowered and "add to cart" not in lowered:
            return False
        return True

    @staticmethod
    def _format_price(price: float, currency: str) -> str:
        symbol = "₹" if currency == "INR" else f"{currency} "
        if price == int(price):
            return f"{symbol}{int(price):,}"
        return f"{symbol}{price:,.2f}"
