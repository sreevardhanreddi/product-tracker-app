import logging

from celery import Celery
from celery.signals import (
    task_failure,
    task_postrun,
    task_prerun,
    worker_ready,
    worker_shutdown,
)

from config import settings

logger = logging.getLogger(__name__)

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


@worker_ready.connect
def on_worker_ready(sender, **kwargs):
    logger.info(
        "Celery worker ready | hostname=%s broker=%s",
        sender.hostname,
        settings.CELERY_BROKER_URL,
    )


@worker_shutdown.connect
def on_worker_shutdown(sender, **kwargs):
    logger.info("Celery worker shutting down | hostname=%s", sender.hostname)


@task_prerun.connect
def on_task_prerun(task_id, task, args, kwargs, **_):
    logger.info(
        "Task starting | task=%s id=%s args=%s kwargs=%s",
        task.name,
        task_id,
        args,
        kwargs,
    )


@task_postrun.connect
def on_task_postrun(task_id, task, retval, state, **_):
    logger.info(
        "Task finished | task=%s id=%s state=%s retval=%s",
        task.name,
        task_id,
        state,
        retval,
    )


@task_failure.connect
def on_task_failure(task_id, exception, traceback, sender, **_):
    logger.error(
        "Task failed | task=%s id=%s exception=%s",
        sender.name,
        task_id,
        exception,
        exc_info=(type(exception), exception, traceback),
    )


# Periodic schedule: fan-out due link checks every N minutes
celery_app.conf.beat_schedule = {
    "check-due-product-links": {
        "task": "tasks.price_check.check_due_product_links",
        "schedule": settings.PRICE_CHECK_INTERVAL_MINUTES * 60,  # in seconds
    }
}
