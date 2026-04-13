import logging
from datetime import datetime
from typing import List, Optional
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from database import get_session
from models import Product, ProductLink
from schemas.product import ProductCreate, ProductLinkCreate, ProductRead, ProductUpdate
from services.price_service import check_product_link_price, refresh_product_cache
from services.scraper.detector import detect_platform

logger = logging.getLogger(__name__)
router = APIRouter()


def _payload_links(payload: ProductCreate) -> list[tuple[str, int]]:
    if payload.links:
        links: list[tuple[str, int]] = []
        for link in payload.links:
            interval = link.check_interval_minutes or payload.check_interval_minutes
            links.append((link.url, interval))
        return links
    return [(payload.url, payload.check_interval_minutes)] if payload.url else []


@router.post("/products", response_model=ProductRead, status_code=201)
def create_product(payload: ProductCreate, session: Session = Depends(get_session)):
    """
    Add a product with one or more trackable links.
    Persist first, then enqueue background checks for each link.
    """
    link_inputs = _payload_links(payload)
    unique_urls = {url for url, _ in link_inputs}
    if len(unique_urls) != len(link_inputs):
        raise HTTPException(
            status_code=422, detail="Duplicate URLs in request payload."
        )
    now = datetime.utcnow()

    link_rows = []
    for url, interval in link_inputs:
        existing = session.exec(
            select(ProductLink).where(ProductLink.url == url)
        ).first()
        if existing:
            raise HTTPException(
                status_code=409,
                detail=f"This URL is already being tracked: {url}",
            )
        try:
            platform = detect_platform(url)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))
        link_rows.append({"url": url, "interval": interval, "platform": platform.value})

    first = link_rows[0]
    fallback_name = f"Pending - {(urlparse(first['url']).hostname or 'product').replace('www.', '')}"
    product = Product(
        name=payload.name or fallback_name,
        url=first["url"],
        platform=first["platform"],
        target_price=payload.target_price,
        current_price=None,
        currency="INR",
        image_url=None,
        alerts_enabled=True,
        check_interval_minutes=payload.check_interval_minutes,
        created_at=now,
        updated_at=now,
        last_checked_at=None,
    )
    session.add(product)
    session.flush()

    created_link_ids: list[int] = []
    for row in link_rows:
        link = ProductLink(
            product_id=product.id,
            url=row["url"],
            platform=row["platform"],
            current_price=None,
            currency="INR",
            image_url=None,
            alerts_enabled=True,
            check_interval_minutes=row["interval"],
            created_at=now,
            updated_at=now,
            last_checked_at=None,
        )
        session.add(link)
        session.flush()
        created_link_ids.append(link.id)

    session.commit()
    session.refresh(product)

    from tasks.price_check import check_single_product_link

    for link_id in created_link_ids:
        try:
            check_single_product_link.delay(link_id)
        except Exception:
            logger.exception(
                "Failed to enqueue initial price check for product_link_id=%s", link_id
            )

    return product


@router.get("/products", response_model=List[ProductRead])
def list_products(
    is_active: Optional[bool] = None,
    session: Session = Depends(get_session),
):
    query = select(Product)
    if is_active is not None:
        query = query.where(Product.is_active == is_active)
    query = query.order_by(Product.created_at.desc())
    return session.exec(query).all()


@router.get("/products/{product_id}", response_model=ProductRead)
def get_product(product_id: int, session: Session = Depends(get_session)):
    product = session.get(Product, product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    return product


@router.put("/products/{product_id}", response_model=ProductRead)
def update_product(
    product_id: int,
    payload: ProductUpdate,
    session: Session = Depends(get_session),
):
    product = session.get(Product, product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")

    update_data = payload.model_dump(exclude_unset=True, exclude={"links"})
    for field, value in update_data.items():
        setattr(product, field, value)
    product.updated_at = datetime.utcnow()

    if payload.check_interval_minutes is not None:
        links = session.exec(
            select(ProductLink).where(ProductLink.product_id == product_id)
        ).all()
        for link in links:
            link.check_interval_minutes = payload.check_interval_minutes
            link.updated_at = datetime.utcnow()
            session.add(link)

    session.add(product)
    session.commit()
    session.refresh(product)
    return product


@router.delete("/products/{product_id}", status_code=204)
def delete_product(product_id: int, session: Session = Depends(get_session)):
    product = session.get(Product, product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    session.delete(product)
    session.commit()


@router.post(
    "/products/{product_id}/links", response_model=ProductRead, status_code=201
)
def add_product_link(
    product_id: int,
    payload: ProductLinkCreate,
    session: Session = Depends(get_session),
):
    """Add a new tracking link to an existing product."""
    product = session.get(Product, product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")

    existing = session.exec(
        select(ProductLink).where(ProductLink.url == payload.url)
    ).first()
    if existing:
        raise HTTPException(
            status_code=409, detail=f"URL already tracked: {payload.url}"
        )

    try:
        platform = detect_platform(payload.url)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    now = datetime.utcnow()
    link = ProductLink(
        product_id=product_id,
        url=payload.url,
        platform=platform.value,
        current_price=None,
        currency="INR",
        image_url=None,
        alerts_enabled=True,
        check_interval_minutes=payload.check_interval_minutes
        or product.check_interval_minutes,
        created_at=now,
        updated_at=now,
        last_checked_at=None,
    )
    session.add(link)
    session.flush()
    link_id = link.id
    session.commit()
    session.refresh(product)

    from tasks.price_check import check_single_product_link

    try:
        check_single_product_link.delay(link_id)
    except Exception:
        logger.exception(
            "Failed to enqueue price check for new link product_link_id=%s", link_id
        )

    return product


@router.delete("/products/{product_id}/links/{link_id}", status_code=204)
def delete_product_link(
    product_id: int,
    link_id: int,
    session: Session = Depends(get_session),
):
    product = session.get(Product, product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")

    link = session.get(ProductLink, link_id)
    if not link or link.product_id != product_id:
        raise HTTPException(status_code=404, detail="Product link not found")

    session.delete(link)
    session.flush()

    remaining = session.exec(
        select(ProductLink.id).where(ProductLink.product_id == product_id)
    ).first()
    if remaining is None:
        session.delete(product)
    else:
        refresh_product_cache(product, session)

    session.commit()


@router.post("/products/{product_id}/links/{link_id}/check", response_model=ProductRead)
def manual_check_link(
    product_id: int,
    link_id: int,
    session: Session = Depends(get_session),
):
    product = session.get(Product, product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")

    link = session.get(ProductLink, link_id)
    if not link or link.product_id != product_id:
        raise HTTPException(status_code=404, detail="Product link not found")

    history = check_product_link_price(link_id, session, headless=False)
    if history is None:
        raise HTTPException(status_code=502, detail="Scrape failed — check server logs")

    session.refresh(product)
    return product


@router.post("/products/{product_id}/check", response_model=ProductRead)
def manual_check(product_id: int, session: Session = Depends(get_session)):
    """Queue an immediate Celery price check for all active links under a product."""
    product = session.get(Product, product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")

    link_ids = session.exec(
        select(ProductLink.id).where(
            ProductLink.product_id == product_id,
            ProductLink.is_active == True,  # noqa: E712
        )
    ).all()
    if not link_ids:
        raise HTTPException(status_code=404, detail="No active product links found")

    from tasks.price_check import check_single_product_link

    queued = 0
    for link_id in link_ids:
        try:
            check_single_product_link.delay(link_id)
            queued += 1
        except Exception:
            logger.exception(
                "Failed to enqueue manual price check for product_link_id=%s", link_id
            )

    if queued == 0:
        raise HTTPException(
            status_code=502, detail="Unable to queue scrape — check worker logs"
        )

    return product


@router.post("/products/check-all", status_code=202)
def check_all_products(session: Session = Depends(get_session)):
    """Queue an immediate Celery price check for all active product links."""
    link_ids = session.exec(
        select(ProductLink.id).where(ProductLink.is_active == True)  # noqa: E712
    ).all()
    if not link_ids:
        raise HTTPException(status_code=404, detail="No active product links found")

    from tasks.price_check import check_single_product_link

    queued = 0
    for link_id in link_ids:
        try:
            check_single_product_link.delay(link_id)
            queued += 1
        except Exception:
            logger.exception(
                "Failed to enqueue price check for product_link_id=%s", link_id
            )

    return {"queued": queued}


@router.post("/products/{product_id}/alerts/deactivate", response_model=ProductRead)
def deactivate_product_alerts(product_id: int, session: Session = Depends(get_session)):
    product = session.get(Product, product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")

    product.alerts_enabled = False
    product.updated_at = datetime.utcnow()
    session.add(product)
    session.commit()
    session.refresh(product)
    return product


@router.post("/products/{product_id}/alerts/activate", response_model=ProductRead)
def activate_product_alerts(product_id: int, session: Session = Depends(get_session)):
    product = session.get(Product, product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")

    product.alerts_enabled = True
    product.updated_at = datetime.utcnow()
    session.add(product)
    session.commit()
    session.refresh(product)
    return product


@router.post(
    "/products/{product_id}/links/{link_id}/alerts/deactivate",
    response_model=ProductRead,
)
def deactivate_link_alerts(
    product_id: int,
    link_id: int,
    session: Session = Depends(get_session),
):
    product = session.get(Product, product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")

    link = session.get(ProductLink, link_id)
    if not link or link.product_id != product_id:
        raise HTTPException(status_code=404, detail="Product link not found")

    link.alerts_enabled = False
    link.updated_at = datetime.utcnow()
    session.add(link)
    session.commit()
    session.refresh(product)
    return product


@router.post(
    "/products/{product_id}/links/{link_id}/alerts/activate",
    response_model=ProductRead,
)
def activate_link_alerts(
    product_id: int,
    link_id: int,
    session: Session = Depends(get_session),
):
    product = session.get(Product, product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")

    link = session.get(ProductLink, link_id)
    if not link or link.product_id != product_id:
        raise HTTPException(status_code=404, detail="Product link not found")

    link.alerts_enabled = True
    link.updated_at = datetime.utcnow()
    session.add(link)
    session.commit()
    session.refresh(product)
    return product
