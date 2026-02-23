from playwright.sync_api import sync_playwright

from .base import BaseScraper, ScrapedProduct, ScraperError

_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)


class FlipkartScraper(BaseScraper):
    """
    Scrapes Flipkart product pages using Playwright (sync API).

    Handles the login popup that Flipkart shows on first load.
    Price is always INR on Flipkart.
    Stock: absence of the "NOTIFY ME" button indicates the item is in stock.
    """

    def scrape(self, url: str) -> ScrapedProduct:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=self.headless)
            ctx = browser.new_context(user_agent=_USER_AGENT)
            page = ctx.new_page()
            try:
                page.goto(url, timeout=self.timeout_ms, wait_until="domcontentloaded")

                # Dismiss the login popup if it appears
                close_btn = page.query_selector("button._2KpZ6l._2doB4z")
                if close_btn:
                    close_btn.click()
                    page.wait_for_timeout(500)

                # Title — two possible selectors for different page layouts
                title_el = page.query_selector(".B_NuCI") or page.query_selector(
                    "._35KyD6"
                )
                name = (
                    title_el.inner_text().strip() if title_el else page.title().strip()
                )

                # Price — specific selector preferred over the more general one
                price_el = page.query_selector(
                    "._30jeq3._16Jk6d"
                ) or page.query_selector("._30jeq3")
                if not price_el:
                    raise ScraperError("Price not found on Flipkart page")
                price_raw = price_el.inner_text().strip()
                price = self._parse_price(price_raw)

                # Out-of-stock: Flipkart shows a "NOTIFY ME" button when unavailable
                in_stock = page.query_selector("._2AkGiR") is None

                # Product image
                image_url: str | None = None
                img_el = page.query_selector("._396cs4 img") or page.query_selector(
                    "._2r_T1I img"
                )
                if img_el:
                    image_url = img_el.get_attribute("src")

                return ScrapedProduct(
                    name=name,
                    price=price,
                    currency="INR",
                    in_stock=in_stock,
                    image_url=image_url,
                    raw_price_text=price_raw,
                )
            finally:
                browser.close()
