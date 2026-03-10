import logging
from datetime import datetime, timedelta

from sqlmodel import Session, select

from database import engine
from models import ProductLink
from services.price_service import check_product_link_price
from tasks.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(
    name="tasks.price_check.check_due_product_links",
    bind=True,
)
def check_due_product_links(self):
    """
    Periodic fan-out task: dispatch checks only for active links whose
    check interval has elapsed.
    """
    now = datetime.utcnow()
    due_link_ids: list[int] = []

    with Session(engine) as session:
        links = session.exec(
            select(ProductLink).where(ProductLink.is_active == True)  # noqa: E712
        ).all()
        logger.info(
            "Fan-out check | active_links=%d at=%s", len(links), now.isoformat()
        )
        for link in links:
            if link.last_checked_at is None:
                logger.debug("Link %d never checked — queuing", link.id)
                due_link_ids.append(link.id)
                continue
            next_due = link.last_checked_at + timedelta(
                minutes=link.check_interval_minutes
            )
            if now >= next_due:
                logger.debug(
                    "Link %d due | last_checked=%s next_due=%s",
                    link.id,
                    link.last_checked_at.isoformat(),
                    next_due.isoformat(),
                )
                due_link_ids.append(link.id)

    for link_id in due_link_ids:
        check_single_product_link.delay(link_id)

    logger.info("Dispatched price checks | due=%d", len(due_link_ids))
    return {"dispatched": len(due_link_ids)}


@celery_app.task(
    name="tasks.price_check.check_single_product_link",
    bind=True,
    max_retries=3,
    default_retry_delay=60,
)
def check_single_product_link(self, product_link_id: int):
    """
    Run price check for a single product link. Retries up to 3 times on failure
    with a 60-second delay between attempts.
    """
    logger.info(
        "Checking price | link_id=%d attempt=%d",
        product_link_id,
        self.request.retries + 1,
    )
    try:
        with Session(engine) as session:
            history = check_product_link_price(product_link_id, session)
        if history:
            logger.info(
                "Price recorded | link_id=%d product_id=%d price=%.2f",
                product_link_id,
                history.product_id,
                history.price,
            )
            return {
                "product_link_id": product_link_id,
                "product_id": history.product_id,
                "price": history.price,
            }
        logger.info("Price check skipped (no change) | link_id=%d", product_link_id)
        return {"product_link_id": product_link_id, "skipped": True}
    except Exception as exc:
        logger.error(
            "Price check failed | link_id=%d attempt=%d error=%s",
            product_link_id,
            self.request.retries + 1,
            exc,
        )
        raise self.retry(exc=exc)
