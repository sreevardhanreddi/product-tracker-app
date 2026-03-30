import logging
from datetime import datetime

from sqlmodel import Session, select

from models import Alert, PriceHistory, Product, ProductLink
from services.alert_service import dispatch_alert
from services.scraper.detector import get_scraper

logger = logging.getLogger(__name__)


def refresh_product_cache(product: Product, session: Session) -> None:
    """
    Keep product-level fields as a cached summary across active links.
    Lowest in-stock price wins for current_price/currency/url/platform/image.
    """
    links = session.exec(
        select(ProductLink).where(
            ProductLink.product_id == product.id,
            ProductLink.is_active == True,  # noqa: E712
        )
    ).all()

    now = datetime.utcnow()
    if not links:
        product.current_price = None
        product.image_url = None
        product.last_checked_at = None
        product.updated_at = now
        session.add(product)
        return

    checked_links = [link for link in links if link.last_checked_at is not None]
    if checked_links:
        product.last_checked_at = max(link.last_checked_at for link in checked_links)

    priced_links = [link for link in links if link.current_price is not None]
    if not priced_links:
        product.current_price = None
        product.updated_at = now
        session.add(product)
        return

    best_link = min(priced_links, key=lambda link: link.current_price)
    product.current_price = best_link.current_price
    product.currency = best_link.currency
    product.platform = best_link.platform
    product.url = best_link.url
    product.image_url = best_link.image_url
    product.updated_at = now
    session.add(product)


def check_product_link_price(
    product_link_id: int, session: Session
) -> PriceHistory | None:
    """
    Run one scrape cycle for one product link and update product-level cache.
    """
    link = session.get(ProductLink, product_link_id)
    if not link or not link.is_active:
        logger.debug(f"Skipping link {product_link_id}: not found or inactive")
        return None

    product = session.get(Product, link.product_id)
    if not product or not product.is_active:
        logger.debug(
            f"Skipping link {product_link_id}: parent product inactive/missing"
        )
        return None

    try:
        scraper, _ = get_scraper(link.url)
        data = scraper.scrape(link.url)
    except Exception as e:
        logger.error(f"Scrape failed for link {product_link_id} ({link.url}): {e}")
        return None

    now = datetime.utcnow()

    history = PriceHistory(
        product_id=product.id,
        product_link_id=link.id,
        price=data.price,
        currency=data.currency,
        in_stock=data.in_stock,
        scraped_at=now,
        raw_price_text=data.raw_price_text,
    )
    session.add(history)

    link.current_price = data.price
    link.currency = data.currency
    link.last_checked_at = now
    link.updated_at = now
    if data.image_url:
        link.image_url = data.image_url
    if data.scrape_method:
        link.last_scrape_method = data.scrape_method
    session.add(link)

    # Replace queue-time placeholder with first successful scraped title.
    if data.name and (
        not product.name or product.name.strip().lower().startswith("pending -")
    ):
        product.name = data.name.strip()

    refresh_product_cache(product, session)

    if (
        product.target_price is not None
        and data.price <= product.target_price
        and data.in_stock
    ):
        message = (
            f"'{product.name}' dropped to {data.currency} {data.price:.2f} "
            f"(your target: {data.currency} {product.target_price:.2f}). "
            f"Buy it here: {link.url}"
        )
        alert = Alert(
            product_id=product.id,
            triggered_price=data.price,
            target_price=product.target_price,
            channel="pending",
            message=message,
            sent=False,
            created_at=now,
        )
        session.add(alert)
        session.flush()
        channel = dispatch_alert(alert, product)
        alert.channel = channel
        alert.sent = True
        alert.sent_at = datetime.utcnow()
        session.add(alert)

    session.commit()
    session.refresh(history)
    return history


def check_product_price(product_id: int, session: Session) -> list[PriceHistory]:
    """
    Trigger checks for all active links under a product.
    """
    product = session.get(Product, product_id)
    if not product or not product.is_active:
        logger.debug(f"Skipping product {product_id}: not found or inactive")
        return []

    link_ids = session.exec(
        select(ProductLink.id).where(
            ProductLink.product_id == product_id,
            ProductLink.is_active == True,  # noqa: E712
        )
    ).all()
    rows: list[PriceHistory] = []
    for link_id in link_ids:
        history = check_product_link_price(link_id, session)
        if history:
            rows.append(history)
    return rows
