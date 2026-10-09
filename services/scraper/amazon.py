import re

import requests
from playwright.sync_api import sync_playwright

from .base import BaseScraper, ScrapedProduct, ScraperError

_BLOCKED_RESOURCE_TYPES = {"image", "font", "media", "stylesheet"}

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

_REQUESTS_HEADERS = {
    "User-Agent": _USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "en-IN,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}

_A_PRICE_OPEN_RE = re.compile(r'<span\b[^>]*\bclass="(a-price(?:\s[^"]*)?)"[^>]*>')
_OFFSCREEN_RE = re.compile(r'class="a-offscreen">\s*([^<]*?)\s*</span>')
_WHOLE_RE = re.compile(r'class="a-price-whole">\s*([\d,]+)')
_FRACTION_RE = re.compile(r'class="a-price-fraction">\s*(\d+)')


def _extract_price_text(html: str) -> str | None:
    """
    Return the buy-box price text from an Amazon product page.

    Prefers the `.priceToPay` element, then any `.a-price` that is not an
    `.a-text-price` (the struck-through M.R.P.). Amazon sometimes leaves the
    `.a-offscreen` text blank and renders only `.a-price-whole`, so that is
    used as a fallback within each element.
    """
    opens = list(_A_PRICE_OPEN_RE.finditer(html))
    candidates = []
    for i, m in enumerate(opens):
        classes = m.group(1).split()
        if "a-text-price" in classes:
            continue
        end = opens[i + 1].start() if i + 1 < len(opens) else m.end() + 1000
        body = html[m.end() : min(end, m.end() + 1000)]
        candidates.append(("priceToPay" not in classes, body))
    candidates.sort(key=lambda c: c[0])

    for _, body in candidates:
        offscreen = _OFFSCREEN_RE.search(body)
        if offscreen and offscreen.group(1):
            return offscreen.group(1)
        whole = _WHOLE_RE.search(body)
        if whole:
            fraction = _FRACTION_RE.search(body)
            return whole.group(1) + ("." + fraction.group(1) if fraction else "")
    return None


class AmazonScraper(BaseScraper):
    """
    Scrapes Amazon product pages.

    Strategy:
    1. Try requests library with browser user-agent (fast, no browser overhead).
    2. Fall back to Playwright if requests fails (CAPTCHA, JS-rendered content, etc.).

    Price extraction: see _extract_price_text — .priceToPay first, never the
    .a-text-price M.R.P., with .a-price-whole as fallback for blank .a-offscreen.

    Raises ScraperError if a CAPTCHA page is detected or required
    elements are not found.
    """

    def scrape(self, url: str) -> ScrapedProduct:
        try:
            return self._scrape_via_requests(url)
        except Exception:
            pass
        return self._scrape_via_browser(url)

    def _scrape_via_requests(self, url: str) -> ScrapedProduct:
        r = requests.get(
            url, headers=_REQUESTS_HEADERS, timeout=20, allow_redirects=True
        )
        r.raise_for_status()
        html = r.text

        if "Type the characters" in html or "Enter the characters" in html:
            raise ScraperError("Amazon CAPTCHA encountered in requests response")

        # Title
        title_match = re.search(
            r'id=["\']productTitle["\'][^>]*>\s*(.*?)\s*</span>', html, re.DOTALL
        )
        name = title_match.group(1).strip() if title_match else None
        if not name:
            og_match = re.search(
                r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)["\']',
                html,
                re.IGNORECASE,
            )
            name = og_match.group(1).strip() if og_match else None
        if not name:
            raise ScraperError("Product title not found in Amazon requests response")

        price_raw = _extract_price_text(html)
        if not price_raw:
            raise ScraperError("Price element not found in Amazon requests response")
        price = self._parse_price(price_raw)

        # Stock
        in_stock = True
        avail_match = re.search(
            r'id=["\']availability["\'][^>]*>.*?<span[^>]*>\s*(.*?)\s*</span>',
            html,
            re.DOTALL,
        )
        if avail_match:
            in_stock = "in stock" in avail_match.group(1).lower()

        # Image
        image_url: str | None = None
        tag_match = re.search(r"<img\b[^>]*\bid=[\"']landingImage[\"'][^>]*>", html)
        if tag_match:
            img_tag = tag_match.group(0)
            attr_match = re.search(r'data-old-hires=["\']([^"\']+)["\']', img_tag)
            if not attr_match:
                attr_match = re.search(r'src=["\']([^"\']+)["\']', img_tag)
            if attr_match:
                image_url = attr_match.group(1)

        currency = "INR" if "amazon.in" in url else "USD"

        return ScrapedProduct(
            name=name,
            price=price,
            currency=currency,
            in_stock=in_stock,
            image_url=image_url,
            raw_price_text=price_raw,
            scrape_method="requests",
        )

    def _scrape_via_browser(self, url: str) -> ScrapedProduct:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=self.headless)
            ctx = browser.new_context(
                user_agent=_USER_AGENT,
                viewport={"width": 1280, "height": 800},
            )
            # Block resources that slow down page load without affecting price data
            ctx.route(
                "**/*",
                lambda route: (
                    route.abort()
                    if route.request.resource_type in _BLOCKED_RESOURCE_TYPES
                    else route.continue_()
                ),
            )
            page = ctx.new_page()
            try:
                page.goto(url, timeout=self.timeout_ms, wait_until="domcontentloaded")

                if "Type the characters" in page.content():
                    raise ScraperError(
                        "Amazon CAPTCHA encountered — cannot scrape this page"
                    )

                title_el = page.query_selector("#productTitle")
                name = (
                    title_el.inner_text().strip() if title_el else page.title().strip()
                )

                price_raw = _extract_price_text(page.content())
                if not price_raw:
                    raise ScraperError("Price element not found on Amazon page")

                price = self._parse_price(price_raw)

                in_stock = True
                avail_el = page.query_selector("#availability span")
                if avail_el:
                    in_stock = "in stock" in avail_el.inner_text().strip().lower()

                image_url: str | None = None
                img_el = page.query_selector("#landingImage")
                if img_el:
                    image_url = img_el.get_attribute(
                        "data-old-hires"
                    ) or img_el.get_attribute("src")

                currency = "INR" if "amazon.in" in url else "USD"

                return ScrapedProduct(
                    name=name,
                    price=price,
                    currency=currency,
                    in_stock=in_stock,
                    image_url=image_url,
                    raw_price_text=price_raw,
                    scrape_method="browser",
                )
            finally:
                browser.close()
