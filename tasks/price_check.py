import logging

from sqlmodel import Session, select

from database import engine
from models import Product
from services.price_service import check_product_price
from tasks.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(
    name="tasks.price_check.check_all_products",
    bind=True,
)
def check_all_products(self):
    """
    Periodic fan-out task: fetch all active product IDs and dispatch
    an individual check_single_product task for each. This keeps each
    product's failure isolated and retryable independently.
    """
    with Session(engine) as session:
        product_ids = session.exec(
            select(Product.id).where(Product.is_active == True)  # noqa: E712
        ).all()

    for pid in product_ids:
        check_single_product.delay(pid)

    logger.info(f"Dispatched price checks for {len(product_ids)} products")
    return {"dispatched": len(product_ids)}


@celery_app.task(
    name="tasks.price_check.check_single_product",
    bind=True,
    max_retries=3,
    default_retry_delay=60,
)
def check_single_product(self, product_id: int):
    """
    Run price check for a single product. Retries up to 3 times on failure
    with a 60-second delay between attempts.
    """
    try:
        with Session(engine) as session:
            history = check_product_price(product_id, session)
        if history:
            return {"product_id": product_id, "price": history.price}
        return {"product_id": product_id, "skipped": True}
    except Exception as exc:
        logger.error(f"Price check failed for product {product_id}: {exc}")
        raise self.retry(exc=exc)
