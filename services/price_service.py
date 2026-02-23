import logging
from datetime import datetime

from sqlmodel import Session

from models import Alert, PriceHistory, Product
from services.alert_service import dispatch_alert
from services.scraper.detector import get_scraper

logger = logging.getLogger(__name__)


def check_product_price(product_id: int, session: Session) -> PriceHistory | None:
    """
    Run a full price check cycle for one product:
      1. Load product — skip if not found or inactive
      2. Detect platform and scrape current price
      3. Persist a PriceHistory row
      4. Update Product.current_price, last_checked_at, updated_at
      5. If target_price is set and price dropped to/below it: create Alert and dispatch

    Returns the new PriceHistory row, or None if skipped/failed.
    """
    product = session.get(Product, product_id)
    if not product or not product.is_active:
        logger.debug(f"Skipping product {product_id}: not found or inactive")
        return None

    try:
        scraper, _ = get_scraper(product.url)
        data = scraper.scrape(product.url)
    except Exception as e:
        logger.error(f"Scrape failed for product {product_id} ({product.url}): {e}")
        return None

    now = datetime.utcnow()

    # Record the price snapshot
    history = PriceHistory(
        product_id=product.id,
        price=data.price,
        currency=data.currency,
        in_stock=data.in_stock,
        scraped_at=now,
        raw_price_text=data.raw_price_text,
    )
    session.add(history)

    # Update the product's cached fields
    product.current_price = data.price
    product.last_checked_at = now
    product.updated_at = now
    # Backfill image if we didn't have one yet
    if data.image_url and not product.image_url:
        product.image_url = data.image_url
    session.add(product)

    # Alert check: fire only when price is at or below target and item is available
    if (
        product.target_price is not None
        and data.price <= product.target_price
        and data.in_stock
    ):
        message = (
            f"'{product.name}' dropped to {data.currency} {data.price:.2f} "
            f"(your target: {data.currency} {product.target_price:.2f}). "
            f"Buy it here: {product.url}"
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
        session.flush()  # populate alert.id before dispatching

        channel = dispatch_alert(alert, product)
        alert.channel = channel
        alert.sent = True
        alert.sent_at = datetime.utcnow()
        session.add(alert)

    session.commit()
    session.refresh(history)
    return history
