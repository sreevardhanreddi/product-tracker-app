from urllib.parse import urlparse


def format_source_label(platform: str | None, url: str | None) -> str:
    normalized_platform = (platform or "").strip().lower()
    if normalized_platform == "shopify":
        hostname = _hostname_from_url(url)
        return f"SHOPIFY · {hostname.upper()}" if hostname else "SHOPIFY"
    if normalized_platform:
        return normalized_platform.upper()
    return "UNKNOWN"


def _hostname_from_url(url: str | None) -> str:
    if not url:
        return ""
    try:
        return (urlparse(url).hostname or "").replace("www.", "")
    except ValueError:
        return ""
