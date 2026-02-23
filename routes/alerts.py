from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session, select

from database import get_session
from models import Alert
from schemas.alert import AlertRead

router = APIRouter()


@router.get("/alerts", response_model=List[AlertRead])
def list_alerts(
    product_id: Optional[int] = None,
    sent: Optional[bool] = None,
    limit: int = Query(default=50, le=200),
    session: Session = Depends(get_session),
):
    query = select(Alert)
    if product_id is not None:
        query = query.where(Alert.product_id == product_id)
    if sent is not None:
        query = query.where(Alert.sent == sent)
    query = query.order_by(Alert.created_at.desc()).limit(limit)
    return session.exec(query).all()


@router.delete("/alerts/{alert_id}", status_code=204)
def dismiss_alert(alert_id: int, session: Session = Depends(get_session)):
    alert = session.get(Alert, alert_id)
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")
    session.delete(alert)
    session.commit()
