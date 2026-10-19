import json
import re
from urllib.parse import parse_qs, urlparse

from playwright.sync_api import sync_playwright

from . import http
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
        response = http.get(url)
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
            variant_title=variant_title,
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
        """
        Read the selected variant from the page's RSC payload (escaped JSON).

        The search is bounded to this variant's own object, up to the next
        variant, so a key missing here can't be satisfied by a later
        variant's or product's data (the payload also carries stale Dukaan
        records with "inventory":0).
        """
        start = re.search(rf'\\"id\\":\\"{re.escape(sku_id)}\\",\\"title\\":\\"', html)
        if not start:
            return None
        next_variant = html.find('\\"id\\":\\"variant_', start.end())
        segment = html[start.start() : next_variant if next_variant != -1 else None]

        def field(pattern: str) -> str | None:
            match = re.search(rf'\\"{pattern}', segment)
            return match.group(1) if match else None

        def flag(key: str) -> bool | None:
            value = field(rf'{key}\\":(true|false)')
            return None if value is None else value == "true"

        inventory = field(r'inventory\\":(-?\d+)')
        image = field(r'variant_image\\":\[\{[^{}]*?\\"url\\":\\"([^"\\]+)')
        if image and not image.startswith("http"):
            image = f"https://media.thewholetruthfoods.com/{image.lstrip('/')}"

        return {
            "id": sku_id,
            "title": (field(r'title\\":\\"([^"\\]*)') or "").strip() or None,
            "sku": field(r'sku\\":\\"([^"\\]*)'),
            # What the storefront itself uses to show "Notify me" instead of
            # "Add to cart"; the per-variant inventory count is no longer sent.
            "force_oos": flag("force_oos"),
            "allow_backorder": bool(flag("allow_backorder")),
            "manage_inventory": flag("manage_inventory") is not False,
            "inventory": int(inventory) if inventory is not None else None,
            "image_url": image,
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
            if variant["force_oos"] is not None:
                return not variant["force_oos"]
            if not variant["manage_inventory"]:
                return True
            if variant["inventory"] is not None:
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
