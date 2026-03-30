import json
import re
from urllib.parse import parse_qs, urlparse

import requests
from playwright.sync_api import sync_playwright

from .base import BaseScraper, ScrapedProduct, ScraperError

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)


class TheWholeTruthScraper(BaseScraper):
    """
    Scrapes The Whole Truth product pages from their server-rendered HTML.
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
                result = self._build_from_html(page.content(), url)
                result.scrape_method = "browser"
                return result
            except Exception as e:
                browser_error = e
            finally:
                browser.close()

        raise ScraperError(
            f"The Whole Truth scrape failed via requests ({requests_error}) "
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
        response = requests.get(url, headers=headers, timeout=20, allow_redirects=True)
        response.raise_for_status()
        result = self._build_from_html(response.text, url)
        result.scrape_method = "requests"
        return result

    def _build_from_html(self, html: str, url: str) -> ScrapedProduct:
        selected_sku = self._extract_selected_sku(url)
        product_ld = self._extract_product_ld_json(html)
        variant = (
            self._extract_variant_data(html, selected_sku) if selected_sku else None
        )

        base_name = (
            (product_ld.get("name") if product_ld else None)
            or self._extract_meta_content(html, "property", "og:title")
            or self._extract_title(html)
        )
        if not base_name:
            raise ScraperError("Product name not found on The Whole Truth page")

        variant_title = (variant or {}).get("title")
        name = self._build_name(base_name, variant_title)

        price = None
        raw_price_text = None
        if selected_sku:
            price = self._extract_landing_price(html, selected_sku)
            if price is not None:
                raw_price_text = self._format_price(price)

        if price is None and product_ld:
            offer = self._select_offer(product_ld, selected_sku, variant_title)
            if offer and offer.get("price") is not None:
                price = float(offer["price"])
                raw_price_text = self._format_price(price)

        if price is None:
            meta_price = self._extract_meta_content(
                html, "name", "product:price:amount"
            )
            if meta_price:
                price = self._parse_price(meta_price)
                raw_price_text = self._format_price(price)

        if price is None:
            raise ScraperError("Price not found on The Whole Truth page")

        image_url = (
            (variant or {}).get("image_url")
            or self._extract_meta_content(html, "property", "og:image")
            or (self._extract_offer_image(product_ld) if product_ld else None)
        )

        in_stock = self._resolve_stock(
            variant, product_ld, selected_sku, variant_title, html
        )

        return ScrapedProduct(
            name=name,
            price=price,
            currency="INR",
            in_stock=in_stock,
            image_url=image_url,
            raw_price_text=raw_price_text,
        )

    @staticmethod
    def _extract_selected_sku(url: str) -> str | None:
        query = parse_qs(urlparse(url).query)
        sku_ids = query.get("sku_id")
        return sku_ids[0] if sku_ids else None

    @staticmethod
    def _build_name(base_name: str, variant_title: str | None) -> str:
        base_name = base_name.strip()
        if not variant_title:
            return base_name
        if variant_title.lower() in base_name.lower():
            return base_name
        return f"{base_name} - {variant_title.strip()}"

    def _extract_landing_price(self, html: str, sku_id: str) -> float | None:
        matches = re.findall(
            rf'\{{[^{{}}]*\\"type\\":\\"LANDING_PRICE\\"[^{{}}]*\\"variant_id\\":\\"{re.escape(sku_id)}\\"[^{{}}]*\\"value\\":(\d+(?:\.\d+)?)',
            html,
            re.DOTALL,
        )
        if not matches:
            return None
        return min(float(match) for match in matches)

    def _extract_variant_data(self, html: str, sku_id: str) -> dict | None:
        match = re.search(
            rf'\\"id\\":\\"{re.escape(sku_id)}\\".*?\\"title\\":\\"(?P<title>[^"]*)\\".*?'
            rf'\\"sku\\":\\"(?P<sku>[^"]*)\\".*?'
            rf'\\"allow_backorder\\":(?P<allow_backorder>true|false).*?'
            rf'\\"manage_inventory\\":(?P<manage_inventory>true|false).*?'
            rf'\\"inventory\\":(?P<inventory>-?\d+).*?'
            rf'\\"primary_image\\":\\"(?P<primary_image>[^"]*)\\"',
            html,
            re.DOTALL,
        )
        if not match:
            return None

        primary_image = match.group("primary_image").strip()
        if primary_image and not primary_image.startswith("http"):
            primary_image = primary_image.lstrip("/")

        return {
            "id": sku_id,
            "title": match.group("title").strip() or None,
            "sku": match.group("sku").strip() or None,
            "allow_backorder": match.group("allow_backorder") == "true",
            "manage_inventory": match.group("manage_inventory") == "true",
            "inventory": int(match.group("inventory")),
            "image_url": primary_image or None,
        }

    def _resolve_stock(
        self,
        variant: dict | None,
        product_ld: dict | None,
        selected_sku: str | None,
        variant_title: str | None,
        html: str,
    ) -> bool:
        if variant:
            if not variant["manage_inventory"]:
                return True
            return variant["inventory"] > 0 or variant["allow_backorder"]

        offer = (
            self._select_offer(product_ld, selected_sku, variant_title)
            if product_ld
            else None
        )
        if offer:
            availability = str(offer.get("availability", "")).lower()
            if availability:
                return "outofstock" not in availability

        return "out of stock" not in html.lower()

    def _select_offer(
        self,
        product_ld: dict | None,
        selected_sku: str | None,
        variant_title: str | None,
    ) -> dict | None:
        if not product_ld:
            return None

        offers = product_ld.get("offers")
        if isinstance(offers, dict):
            offers = [offers]
        if not isinstance(offers, list):
            return None

        if selected_sku:
            selected_suffix = f"sku_id={selected_sku}"
            for offer in offers:
                if isinstance(offer, dict) and selected_suffix in str(
                    offer.get("url", "")
                ):
                    return offer

        if variant_title:
            for offer in offers:
                if isinstance(offer, dict) and offer.get("name") == variant_title:
                    return offer

        for offer in offers:
            if isinstance(offer, dict):
                return offer
        return None

    def _extract_product_ld_json(self, html: str) -> dict | None:
        for match in re.finditer(
            r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
            html,
            re.IGNORECASE | re.DOTALL,
        ):
            try:
                data = json.loads(match.group(1))
            except json.JSONDecodeError:
                continue
            if isinstance(data, dict) and data.get("@type") == "Product":
                return data
        return None

    @staticmethod
    def _extract_offer_image(product_ld: dict) -> str | None:
        image = product_ld.get("image")
        if isinstance(image, str):
            return image
        if isinstance(image, dict):
            return image.get("url") or image.get("image")
        return None

    @staticmethod
    def _extract_title(html: str) -> str | None:
        match = re.search(r"<title>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
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
        if match:
            return match.group(1).strip()
        return None

    @staticmethod
    def _format_price(price: float) -> str:
        whole = int(price)
        if float(whole) == float(price):
            return f"₹{whole:,}"
        return f"₹{price:,.2f}"
