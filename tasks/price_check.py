import logging
import random
from collections import defaultdict
from datetime import datetime, timedelta
from urllib.parse import urlparse

from sqlmodel import Session, select

from config import settings
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
    due_links: list[tuple[int, str]] = []

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
                due_links.append((link.id, link.url))
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
                due_links.append((link.id, link.url))

    dispatched = _dispatch_staggered_by_host(due_links)

    logger.info("Dispatched price checks | due=%d", dispatched)
    return {"dispatched": dispatched}


def _hostname(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower().replace("www.", "")
    except ValueError:
        return ""


def _dispatch_staggered_by_host(due_links: list[tuple[int, str]]) -> int:
    """
    Queue due links, spacing apart the ones that share a hostname.

    Stores rate-limit per host, so dispatching every due link at once makes
    same-store links collide and earn a 429 — which the scraper then has to
    back off through, or worse, fails and re-bursts on the next beat. Links on
    different hosts are unaffected and still start immediately.
    """
    stagger = max(settings.PRICE_CHECK_HOST_STAGGER_SECONDS, 0)
    per_host: dict[str, int] = defaultdict(int)
    dispatched = 0

    for link_id, url in due_links:
        host = _hostname(url)
        position = per_host[host]
        per_host[host] += 1

        if stagger and position:
            # Jitter keeps repeated beats from lining up into the same pattern.
            countdown = position * stagger + random.uniform(0, stagger * 0.25)
            check_single_product_link.apply_async(args=[link_id], countdown=countdown)
            logger.debug(
                "Queued link %d | host=%s position=%d countdown=%.1fs",
                link_id,
                host,
                position,
                countdown,
            )
        else:
            check_single_product_link.delay(link_id)

        dispatched += 1

    busiest = sorted(per_host.items(), key=lambda kv: kv[1], reverse=True)[:3]
    if busiest:
        logger.info(
            "Stagger plan | stagger=%ds hosts=%d busiest=%s",
            stagger,
            len(per_host),
            ", ".join(f"{host}×{count}" for host, count in busiest),
        )

    return dispatched


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
