import logging
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from database import get_session
from models import PriceHistory, Product
from schemas.product import ProductCreate, ProductRead, ProductUpdate
from services.price_service import check_product_price
from services.scraper.detector import detect_platform, get_scraper

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/products", response_model=ProductRead, status_code=201)
def create_product(payload: ProductCreate, session: Session = Depends(get_session)):
    """
    Add a product to track. On creation we immediately scrape the page so
    the product card shows a name, price, and image right away.
    """
    # Detect platform (raises 422 for unsupported URLs)
    try:
        platform = detect_platform(payload.url)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    # Reject duplicates
    existing = session.exec(select(Product).where(Product.url == payload.url)).first()
    if existing:
        raise HTTPException(
            status_code=409, detail="This URL is already being tracked."
        )

    # Scrape on-add to populate name/price/image
    try:
        scraper, _ = get_scraper(payload.url)
        data = scraper.scrape(payload.url)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Scrape failed: {e}")

    now = datetime.utcnow()
    product = Product(
        name=payload.name or data.name,
        url=payload.url,
        platform=platform.value,
        target_price=payload.target_price,
        current_price=data.price,
        currency=data.currency,
        image_url=data.image_url,
        check_interval_minutes=payload.check_interval_minutes,
        created_at=now,
        updated_at=now,
        last_checked_at=now,
    )
    session.add(product)
    session.flush()  # get product.id

    # Record the first price snapshot
    history = PriceHistory(
        product_id=product.id,
        price=data.price,
        currency=data.currency,
        in_stock=data.in_stock,
        scraped_at=now,
        raw_price_text=data.raw_price_text,
    )
    session.add(history)
    session.commit()
    session.refresh(product)
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

    update_data = payload.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(product, field, value)
    product.updated_at = datetime.utcnow()

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


@router.post("/products/{product_id}/check", response_model=ProductRead)
def manual_check(product_id: int, session: Session = Depends(get_session)):
    """Trigger an immediate price check for a single product."""
    product = session.get(Product, product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")

    history = check_product_price(product_id, session)
    if history is None:
        raise HTTPException(status_code=502, detail="Scrape failed — check server logs")

    session.refresh(product)
    return product
