from celery import Celery

from config import settings

celery_app = Celery(
    "product_tracker",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
    include=["tasks.price_check"],
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    # Fetch one task at a time so each Playwright browser gets its own process slot
    worker_prefetch_multiplier=1,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
)

# Periodic schedule: fan-out price check every N minutes
celery_app.conf.beat_schedule = {
    "check-all-products": {
        "task": "tasks.price_check.check_all_products",
        "schedule": settings.PRICE_CHECK_INTERVAL_MINUTES * 60,  # in seconds
    }
}
