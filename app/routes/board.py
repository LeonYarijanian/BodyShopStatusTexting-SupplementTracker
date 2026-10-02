"""Job Board (GET /)."""

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import CurrentUser, get_db, require_user
from app.business_days import utcnow
from app.enums import ACTIVE_STAGES, ConsentStatus, SupplementStatus
from app.messaging.engine import consent_statuses
from app.models import RepairOrder, Supplement
from app.routes import render, shop_settings
from app.routes.repair_orders import days_in_shop, payer_label
from app.supplements import aging_bucket, days_open

router = APIRouter()


def board_context(db: Session, current: CurrentUser) -> dict:
    now = utcnow()
    tz = current.shop.timezone
    ros = db.scalars(
        select(RepairOrder)
        .where(RepairOrder.shop_id == current.shop_id, RepairOrder.current_stage.in_(ACTIVE_STAGES))
        .order_by(RepairOrder.checked_in_at, RepairOrder.id)
    ).all()
    consents = consent_statuses(db, current.shop_id)

    oldest_open: dict[int, int] = {}
    for supplement in db.scalars(
        select(Supplement).where(Supplement.shop_id == current.shop_id, Supplement.status == SupplementStatus.SUBMITTED)
    ).all():
        days = days_open(supplement, now, tz)
        if days is not None and days >= oldest_open.get(supplement.repair_order_id, -1):
            oldest_open[supplement.repair_order_id] = days

    columns = {stage: [] for stage in ACTIVE_STAGES}
    for ro in ros:
        customer = ro.customer
        supplement_days = oldest_open.get(ro.id)
        columns[ro.current_stage].append(
            {
                "ro": ro,
                "customer_name": f"{customer.first_name} {customer.last_name[0]}." if customer.last_name else customer.first_name,
                "days_in_shop": days_in_shop(ro, now),
                "payer": payer_label(ro),
                "no_texts": consents.get(customer.phone_e164) != ConsentStatus.OPTED_IN,
                "supplement_days": supplement_days,
                "supplement_bucket": aging_bucket(supplement_days).value if supplement_days is not None else None,
            }
        )
    settings = shop_settings(db, current.shop_id)
    return {"columns": columns, "messaging_mode": settings.messaging_mode}


@router.get("/")
def board_page(request: Request, current: CurrentUser = Depends(require_user), db: Session = Depends(get_db)):
    return render(request, "board.html", db, current, error=None, **board_context(db, current))
