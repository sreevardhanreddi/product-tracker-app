from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session, select

from database import get_session
from models import PriceHistory, Product
from schemas.price_history import PriceHistoryRead

router = APIRouter()


@router.get("/products/{product_id}/prices")
def get_price_history(
    product_id: int,
    limit: int = Query(default=200, le=1000),
    offset: int = 0,
    product_link_id: Optional[int] = None,
    from_date: Optional[datetime] = None,
    to_date: Optional[datetime] = None,
    session: Session = Depends(get_session),
) -> Dict[str, Any]:
    product = session.get(Product, product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")

    query = select(PriceHistory).where(PriceHistory.product_id == product_id)
    if product_link_id is not None:
        query = query.where(PriceHistory.product_link_id == product_link_id)
    if from_date:
        query = query.where(PriceHistory.scraped_at >= from_date)
    if to_date:
        query = query.where(PriceHistory.scraped_at <= to_date)

    # Chronological order so Chart.js gets a left-to-right timeline
    query = query.order_by(PriceHistory.scraped_at.asc()).offset(offset).limit(limit)
    rows = session.exec(query).all()

    return {
        "product_id": product.id,
        "product_name": product.name,
        "currency": product.currency,
        "data": [PriceHistoryRead.model_validate(r) for r in rows],
    }
