from playwright.sync_api import sync_playwright

from .base import BaseScraper, ScrapedProduct, ScraperError

_BLOCKED_RESOURCE_TYPES = {"image", "font", "media", "stylesheet"}

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)


class AmazonScraper(BaseScraper):
    """
    Scrapes Amazon product pages using Playwright (sync API).

    Price extraction order:
      1. .a-price .a-offscreen  — full formatted price string (most reliable)
      2. .a-price-whole + .a-price-fraction combined

    Raises ScraperError if a CAPTCHA page is detected or required
    elements are not found.
    """

    def scrape(self, url: str) -> ScrapedProduct:
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

                # Price: prefer the full offscreen-formatted value
                price_raw: str | None = None
                offscreen = page.query_selector(".a-price .a-offscreen")
                if offscreen:
                    price_raw = offscreen.inner_text().strip()
                else:
                    whole = page.query_selector(".a-price-whole")
                    frac = page.query_selector(".a-price-fraction")
                    if whole:
                        price_raw = whole.inner_text().strip().rstrip(".")
                        if frac:
                            price_raw += "." + frac.inner_text().strip()

                if not price_raw:
                    raise ScraperError("Price element not found on Amazon page")

                price = self._parse_price(price_raw)

                # Stock status
                in_stock = True
                avail_el = page.query_selector("#availability span")
                if avail_el:
                    in_stock = "in stock" in avail_el.inner_text().strip().lower()

                # Product image
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
                )
            finally:
                browser.close()
