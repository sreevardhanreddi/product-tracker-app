import logging
import re
import time
from urllib.parse import parse_qs, urlparse

import requests
from playwright.sync_api import sync_playwright

from .base import BaseScraper, ScrapedProduct, ScraperError

logger = logging.getLogger(__name__)

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

# Shopify throttles the public /products/*.json and *.js endpoints per store.
# The limit is short-lived, so a few backed-off retries recover far more often
# than they fail — and failing here is expensive (browser fallback) or wrong
# (a missed stock reading turns into a false back-in-stock alert).
_RETRY_STATUSES = {429, 430, 503}
_MAX_ATTEMPTS = 4
_BACKOFF_BASE_SECONDS = 1.5
_MAX_BACKOFF_SECONDS = 20.0

_HTML_HEADERS = {
    **_JSON_HEADERS,
    "Accept": "text/html,application/xhtml+xml",
}

# Headless Shopify storefronts (Hydrogen/Next.js on Vercel, Netlify, ...) serve
# the customer-facing domain themselves and keep the Shopify store on a separate
# domain. Their catch-all route answers /products/{handle}.json with 200 + the
# HTML page, so the failure is silent rather than a 404. Shopify still serves
# that store's own assets from <store-domain>/cdn/shop/..., which is how we find
# the domain that does speak the product JSON API.
_CDN_SHOP_HOST_RE = re.compile(r"https?://([A-Za-z0-9.-]+)/cdn/shop/")
_MYSHOPIFY_HOST_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]*\.myshopify\.com")

# Resolving the backing domain costs a full storefront HTML fetch, and price
# checks re-scrape the same hosts on every cycle. Keyed by storefront hostname;
# entries are rewritten whenever a lookup proves them stale.
_BACKING_HOST_CACHE: dict[str, str] = {}


class ShopifyThrottled(ScraperError):
    """Shopify kept rate-limiting us after every retry was exhausted."""


class NotShopifyJson(ScraperError):
    """A host answered /products/{handle}.json with something that isn't it."""


def _retry_delay(response, attempt: int) -> float:
    """Honour Retry-After when present, else exponential backoff."""
    retry_after = (response.headers or {}).get("Retry-After") if response else None
    if retry_after:
        try:
            return min(float(retry_after), _MAX_BACKOFF_SECONDS)
        except (TypeError, ValueError):
            pass
    return min(_BACKOFF_BASE_SECONDS * (2**attempt), _MAX_BACKOFF_SECONDS)


def get_with_retry(url: str, timeout: int = 10):
    """
    GET a Shopify JSON endpoint, retrying through rate-limit responses.

    Raises ShopifyThrottled if every attempt was throttled, so callers can
    tell "the store is rate-limiting us" apart from "the store said no".
    """
    last_response = None
    for attempt in range(_MAX_ATTEMPTS):
        response = requests.get(
            url,
            headers=_JSON_HEADERS,
            timeout=timeout,
            allow_redirects=True,
        )
        if response.status_code not in _RETRY_STATUSES:
            response.raise_for_status()
            return response

        last_response = response
        if attempt < _MAX_ATTEMPTS - 1:
            time.sleep(_retry_delay(response, attempt))

    raise ShopifyThrottled(
        f"Shopify rate-limited {url} "
        f"({last_response.status_code}) after {_MAX_ATTEMPTS} attempts"
    )


class ShopifyScraper(BaseScraper):
    """
    Scrapes Shopify product pages.

    Strategy:
    1. Try the public JSON API: GET /products/{handle}.json
       Fast, no browser needed; works on most Shopify stores.
       Stock status comes from a second request to /products/{handle}.js,
       since the .json endpoint strips per-variant `available`.
       On headless storefronts the JSON API lives on a different domain than
       the one in the link, so we resolve that domain from the page HTML.
    2. Fall back to Playwright if the JSON endpoint fails or returns
       unexpected data (e.g., store requires login or handle differs).
    """

    def scrape(self, url: str) -> ScrapedProduct:
        try:
            return self._scrape_requests(url)
        except ShopifyThrottled as e:
            # Falling back costs a browser launch, and with prefetch_multiplier=1
            # those serialise across the worker pool. Log at warning so a store
            # that is persistently throttling us is visible rather than just slow.
            logger.warning("Shopify throttled, falling back to browser | %s", e)
        except Exception as e:
            logger.info("Shopify JSON path failed (%s), falling back to browser", e)
        return self._scrape_playwright(url)

    def _scrape_requests(self, url: str) -> ScrapedProduct:
        parsed = urlparse(url)
        handle = parsed.path.rstrip("/").split("/")[-1]
        variant_id = self._extract_variant_id(url)

        data, api_host = self._fetch_product_json(url, parsed, handle)

        variant = self._select_variant(data.get("variants", []), variant_id)
        price = float(variant["price"])
        in_stock = self._fetch_stock_status(
            parsed.scheme, api_host, handle, variant.get("id")
        )
        image_url = self._select_image_url(data, variant)
        # Shopify JSON doesn't always expose currency; every store this tracker
        # follows is Indian, matching the ScrapedProduct default.
        currency = variant.get("price_currency") or "INR"

        return ScrapedProduct(
            name=data["title"],
            price=price,
            currency=currency,
            in_stock=in_stock,
            image_url=image_url,
            raw_price_text=variant["price"],
            scrape_method="requests",
        )

    @classmethod
    def _fetch_product_json(cls, url: str, parsed, handle: str) -> tuple[dict, str]:
        """
        Fetch the product JSON, resolving which host actually serves the API.

        Returns (product_data, api_host) so the follow-up .js stock request
        goes to the same host. Ordinary stores answer on the link's own host
        and never pay for the HTML fetch; headless storefronts fall through to
        domain discovery, and the result is cached per storefront host.
        """
        host = parsed.hostname
        if not host:
            raise ScraperError(f"No hostname in Shopify URL: {url!r}")

        candidates = [c for c in (_BACKING_HOST_CACHE.get(host), host) if c]
        tried: list[str] = []
        last_error: NotShopifyJson | None = None

        for candidate in candidates:
            if candidate in tried:
                continue
            tried.append(candidate)
            try:
                data = cls._get_product_json(parsed.scheme, candidate, handle)
            except NotShopifyJson as e:
                last_error = e
                continue
            _BACKING_HOST_CACHE[host] = candidate
            return data, candidate

        backing = cls._discover_backing_host(url, host)
        if backing is None or backing in tried:
            raise last_error or NotShopifyJson(
                f"{host} does not serve the Shopify product JSON API"
            )

        logger.info("Resolved headless Shopify storefront %s -> %s", host, backing)
        data = cls._get_product_json(parsed.scheme, backing, handle)
        _BACKING_HOST_CACHE[host] = backing
        return data, backing

    @staticmethod
    def _get_product_json(scheme: str, host: str, handle: str) -> dict:
        """GET /products/{handle}.json from one host and unwrap the product."""
        r = get_with_retry(f"{scheme}://{host}/products/{handle}.json")
        try:
            payload = r.json()
        except ValueError as e:
            # A headless storefront's catch-all route returns the HTML page
            # with a 200, so this is the normal signal that we have the
            # customer-facing domain rather than the Shopify one.
            raise NotShopifyJson(f"{host} returned non-JSON for {handle}.json") from e

        product = payload.get("product") if isinstance(payload, dict) else None
        if not product:
            raise NotShopifyJson(f"No 'product' key in JSON from {host}")
        return product

    @staticmethod
    def _discover_backing_host(url: str, storefront_host: str) -> str | None:
        """
        Find the Shopify-served domain behind a headless storefront.

        Prefers a *.myshopify.com reference, then any host serving the store's
        own /cdn/shop/ assets. cdn.shopify.com is excluded: it is the shared
        asset CDN (and uses /s/files/ paths), not a store domain.
        """
        try:
            r = requests.get(
                url, headers=_HTML_HEADERS, timeout=15, allow_redirects=True
            )
            r.raise_for_status()
        except Exception as e:
            logger.info("Could not fetch storefront HTML for %s: %s", url, e)
            return None

        # RSC/JSON payloads embed URLs with escaped slashes.
        body = r.text.replace("\\/", "/")

        for match in _MYSHOPIFY_HOST_RE.findall(body):
            return match.lower()

        for host in _CDN_SHOP_HOST_RE.findall(body):
            host = host.lower()
            if host != storefront_host.lower() and host != "cdn.shopify.com":
                return host

        return None

    @staticmethod
    def _fetch_stock_status(scheme: str, host: str, handle: str, variant_id) -> bool:
        """
        Fetch per-variant stock status from the .js endpoint, since the
        public .json endpoint strips `available`.

        A throttled request must NOT be treated as "in stock": price_service
        raises a back-in-stock alert on a False -> True transition, so
        guessing True here sends a false alert. On throttling we raise, which
        drops the whole scrape to the Playwright fallback where stock is read
        from the add-to-cart button instead.
        """
        js_url = f"{scheme}://{host}/products/{handle}.js"
        r = get_with_retry(js_url)
        data = r.json()
        for variant in data.get("variants", []):
            if str(variant.get("id")) == str(variant_id):
                return bool(variant.get("available", False))
        return bool(data.get("available", True))

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
                    currency="INR",
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
