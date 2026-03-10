import logging

import sentry_sdk
from fastapi import FastAPI

from config import settings

# Configure logging
logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)

# Sentry (no-op if SENTRY_DSN is unset)
if settings.SENTRY_DSN:
    sentry_sdk.init(dsn=settings.SENTRY_DSN, environment=settings.APP_ENV)

from models import Alert, PriceHistory, Product, ProductLink  # noqa: F401, E402
from routes.alerts import router as alerts_router
from routes.frontend import router as frontend_router
from routes.price_history import router as price_history_router
from routes.products import router as products_router

app = FastAPI(
    title="Product Price Tracker",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

app.include_router(products_router, prefix="/api", tags=["products"])
app.include_router(price_history_router, prefix="/api", tags=["price_history"])
app.include_router(alerts_router, prefix="/api", tags=["alerts"])
# Frontend route last (catch-all for /)
app.include_router(frontend_router, tags=["frontend"])
