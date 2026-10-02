"""The public customer status page (Section 16 item 8). No login; the long random token is the key."""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import get_db
from app.business_days import utcnow
from app.enums import STAGE_LABELS, PayerType, Stage
from app.locations import address_for, phone_for, shop_locations
from app.messaging.templates import format_us_phone
from app.models import RepairOrder, Shop, StageEvent
from app.routes import templates
from app.status_page import TOKEN_RE, link_is_active

router = APIRouter()

HEADERS = {
    "Cache-Control": "no-store",
    "X-Robots-Tag": "noindex, nofollow",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
}
MILESTONES = [Stage.CHECKED_IN, Stage.PARTS_ORDERED, Stage.BODY_REPAIR, Stage.PAINT, Stage.READY_FOR_PICKUP, Stage.DELIVERED]
STAGE_ORDER = list(Stage)


def _not_found(request: Request):
    return templates.TemplateResponse(request, "status_page.html", {"request": request, "ro": None}, status_code=404, headers=HEADERS)


@router.get("/s/{token}", response_class=HTMLResponse)
def status_page(token: str, request: Request, db: Session = Depends(get_db)):
    if not TOKEN_RE.match(token):
        return _not_found(request)
    ro = db.scalar(select(RepairOrder).where(RepairOrder.status_token == token))
    now = utcnow()
    if ro is None or not link_is_active(ro, now):
        return _not_found(request)
    shop = db.get(Shop, ro.shop_id)
    events = db.scalars(select(StageEvent).where(StageEvent.repair_order_id == ro.id).order_by(StageEvent.changed_at, StageEvent.id)).all()
    reached = {event.to_stage for event in events}
    current_index = STAGE_ORDER.index(ro.current_stage)
    milestones = []
    for stage in MILESTONES:
        if ro.payer_type == PayerType.CUSTOMER_PAY and stage == Stage.WAITING_ON_INSURANCE:
            continue
        current = stage == ro.current_stage
        done = not current and (stage in reached or (ro.current_stage != Stage.ON_HOLD and STAGE_ORDER.index(stage) < current_index))
        milestones.append({"label": STAGE_LABELS[stage], "done": done, "current": current})
    history = [{"label": STAGE_LABELS[e.to_stage], "at": e.changed_at} for e in reversed(events)]
    context = {
        "request": request,
        "ro": ro,
        "shop": shop,
        "shop_phone": format_us_phone(phone_for(shop, ro.location)),
        "shop_phone_e164": phone_for(shop, ro.location),
        "address": address_for(shop, ro.location),
        "location_name": ro.location.name if ro.location and len(shop_locations(db, shop.id)) > 1 else None,
        "stage_label": STAGE_LABELS[ro.current_stage],
        "milestones": milestones,
        "history": history,
        "tz": shop.timezone,
        "updated_at": events[-1].changed_at if events else ro.checked_in_at,
    }
    return templates.TemplateResponse(request, "status_page.html", context, headers=HEADERS)
