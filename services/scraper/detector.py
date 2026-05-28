from enum import Enum
from urllib.parse import urlparse

import httpx


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
    if "hydrotech3dchennai.com" in host:
        return Platform.HYDROTECH3D

    # Shopify probe via the public JSON API endpoint
    handle = parsed.path.rstrip("/").split("/")[-1]
    probe_url = f"{parsed.scheme}://{parsed.hostname}/products/{handle}.json"
    try:
        r = httpx.get(probe_url, timeout=5, follow_redirects=True)
        if r.status_code == 200 and "product" in r.json():
            return Platform.SHOPIFY
    except Exception:
        pass

    raise ValueError(
        f"Could not detect platform for URL: {url}. "
        "Supported platforms: Amazon, Flipkart, Shopify, Myntra, HealthKart, "
        "TrueBasics, The Whole Truth, Nutrabay, Robu, WOL3D, Hydrotech 3D."
    )


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
    elif platform in (Platform.ROBU, Platform.WOL3D):
        return WooCommerceScraper(**kwargs), platform.value
    elif platform == Platform.HYDROTECH3D:
        return WixScraper(**kwargs), platform.value
    else:
        return ShopifyScraper(**kwargs), platform.value
