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
        "Supported platforms: Amazon, Flipkart, Shopify, Myntra."
    )


def get_scraper(url: str):
    """
    Factory: detect the platform for a URL and return (scraper_instance, platform_str).
    Imports are deferred to avoid heavy playwright import at module load time.
    """
    from config import settings

    from .amazon import AmazonScraper
    from .flipkart import FlipkartScraper
    from .healthkart import HealthKartScraper
    from .myntra import MyntraScraper
    from .shopify import ShopifyScraper
    from .thewholetruth import TheWholeTruthScraper

    platform = detect_platform(url)
    kwargs = {
        "headless": settings.PLAYWRIGHT_HEADLESS,
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
    else:
        return ShopifyScraper(**kwargs), platform.value
