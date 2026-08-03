import time
from enum import Enum
from urllib.parse import urlparse

import httpx

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0.0.0 Safari/537.36"
)

_PROBE_HEADERS = {
    "User-Agent": _USER_AGENT,
    "Accept": "application/json,text/plain,*/*",
    "Accept-Language": "en-IN,en;q=0.9",
}

# Shopify rate-limits its public endpoints per store. Detection runs once when
# a link is added, so a transient 429 here is the difference between "product
# added" and a hard "Could not detect platform" error for the user.
_RETRY_STATUSES = {429, 430, 503}
_PROBE_ATTEMPTS = 3


class Platform(str, Enum):
    AMAZON = "amazon"
    FLIPKART = "flipkart"
    SHOPIFY = "shopify"
    MYNTRA = "myntra"
    HEALTHKART = "healthkart"
    TRUEBASICS = "truebasics"
    THEWHOLETRUTH = "thewholetruth"
    NUTRABAY = "nutrabay"
    ROBU = "robu"
    WOL3D = "wol3d"
    MECKEYS = "meckeys"
    STACKSKB = "stackskb"
    HYDROTECH3D = "hydrotech3d"


def detect_platform(url: str) -> Platform:
    """
    Detect which platform a product URL belongs to.

    Rules (in order):
    1. hostname contains "amazon."   → AMAZON
    2. hostname contains "flipkart." → FLIPKART
    3. Shopify probe: GET /products/{handle}.json with 5s timeout;
       if response is 200 and contains a "product" key → SHOPIFY
    4. Otherwise → raises ValueError
    """
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()

    if "amazon." in host:
        return Platform.AMAZON
    if "flipkart." in host:
        return Platform.FLIPKART
    if "myntra." in host:
        return Platform.MYNTRA
    if "healthkart." in host:
        return Platform.HEALTHKART
    if "truebasics." in host:
        return Platform.TRUEBASICS
    if "thewholetruthfoods." in host:
        return Platform.THEWHOLETRUTH
    if "nutrabay." in host:
        return Platform.NUTRABAY
    if "robu.in" in host:
        return Platform.ROBU
    if "wol3d.com" in host:
        return Platform.WOL3D
    if "meckeys.com" in host:
        return Platform.MECKEYS
    if "stackskb.com" in host:
        return Platform.STACKSKB
    if "hydrotech3dchennai.com" in host:
        return Platform.HYDROTECH3D

    # Shopify probe via the public JSON API endpoint
    handle = parsed.path.rstrip("/").split("/")[-1]
    probe_url = f"{parsed.scheme}://{parsed.hostname}/products/{handle}.json"
    if _probe_shopify_json(probe_url):
        return Platform.SHOPIFY

    # The JSON endpoint was throttled or unavailable. The storefront HTML is
    # rate-limited separately and carries unambiguous Shopify markers, so it
    # still gives a reliable answer when the API refuses to talk to us.
    if _probe_shopify_html(url):
        return Platform.SHOPIFY

    raise ValueError(
        f"Could not detect platform for URL: {url}. "
        "Supported platforms: Amazon, Flipkart, Shopify, Myntra, HealthKart, "
        "TrueBasics, The Whole Truth, Nutrabay, Robu, WOL3D, Meckeys, StacksKB, "
        "Hydrotech 3D."
    )


def _probe_shopify_json(probe_url: str) -> bool:
    """GET /products/{handle}.json, retrying through rate-limit responses."""
    for attempt in range(_PROBE_ATTEMPTS):
        try:
            r = httpx.get(
                probe_url,
                headers=_PROBE_HEADERS,
                timeout=10,
                follow_redirects=True,
            )
        except Exception:
            return False

        if r.status_code in _RETRY_STATUSES:
            if attempt < _PROBE_ATTEMPTS - 1:
                retry_after = r.headers.get("Retry-After")
                try:
                    delay = min(float(retry_after), 10.0) if retry_after else None
                except (TypeError, ValueError):
                    delay = None
                time.sleep(delay if delay is not None else 1.5 * (2**attempt))
            continue

        try:
            return r.status_code == 200 and "product" in r.json()
        except Exception:
            return False

    return False


def _probe_shopify_html(url: str) -> bool:
    """Look for Shopify storefront markers in the product page HTML."""
    try:
        r = httpx.get(
            url,
            headers={**_PROBE_HEADERS, "Accept": "text/html,application/xhtml+xml"},
            timeout=15,
            follow_redirects=True,
        )
    except Exception:
        return False

    if r.status_code != 200:
        return False

    body = r.text
    return "cdn.shopify.com" in body or "Shopify.theme" in body


def get_scraper(url: str, headless: bool | None = None):
    """
    Factory: detect the platform for a URL and return (scraper_instance, platform_str).
    Imports are deferred to avoid heavy playwright import at module load time.
    Pass headless=False to override the .env setting (e.g. for debug runs).
    """
    from config import settings

    from .amazon import AmazonScraper
    from .flipkart import FlipkartScraper
    from .healthkart import HealthKartScraper
    from .myntra import MyntraScraper
    from .nutrabay import NutrabayScraper
    from .shopify import ShopifyScraper
    from .thewholetruth import TheWholeTruthScraper
    from .wix import WixScraper
    from .woocommerce import WooCommerceScraper

    platform = detect_platform(url)
    kwargs = {
        "headless": settings.PLAYWRIGHT_HEADLESS if headless is None else headless,
        "timeout_ms": settings.PLAYWRIGHT_TIMEOUT_MS,
    }

    if platform == Platform.AMAZON:
        return AmazonScraper(**kwargs), platform.value
    elif platform == Platform.FLIPKART:
        return FlipkartScraper(**kwargs), platform.value
    elif platform == Platform.MYNTRA:
        return MyntraScraper(**kwargs), platform.value
    elif platform == Platform.HEALTHKART:
        return HealthKartScraper(**kwargs), platform.value
    elif platform == Platform.TRUEBASICS:
        return HealthKartScraper(**kwargs), platform.value
    elif platform == Platform.THEWHOLETRUTH:
        return TheWholeTruthScraper(**kwargs), platform.value
    elif platform == Platform.NUTRABAY:
        return NutrabayScraper(**kwargs), platform.value
    elif platform in (
        Platform.ROBU,
        Platform.WOL3D,
        Platform.MECKEYS,
        Platform.STACKSKB,
    ):
        return WooCommerceScraper(**kwargs), platform.value
    elif platform == Platform.HYDROTECH3D:
        return WixScraper(**kwargs), platform.value
    else:
        return ShopifyScraper(**kwargs), platform.value
