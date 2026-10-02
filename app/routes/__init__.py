"""Shared helpers for the route modules: Jinja2 setup and page rendering."""

from pathlib import Path

from fastapi import Request
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.auth import CurrentUser, csrf_token
from app.business_days import to_local, utcnow
from app.enums import ACTIVE_STAGES, STAGE_LABELS, MessagingMode, Stage
from app.models import ShopSettings
from app.money import cents_to_input, format_cents

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def _local_dt(value, tz, fmt="%Y-%m-%d %H:%M"):
    if value is None:
        return ""
    return to_local(value, tz).strftime(fmt)


templates.env.filters["money"] = format_cents
templates.env.filters["dollars_input"] = cents_to_input
templates.env.filters["local_dt"] = _local_dt
templates.env.globals["STAGE_LABELS"] = STAGE_LABELS
templates.env.globals["ACTIVE_STAGES"] = ACTIVE_STAGES
templates.env.globals["ALL_STAGES"] = list(Stage)


def is_htmx(request: Request) -> bool:
    return request.headers.get("HX-Request") == "true"


def shop_settings(db: Session, shop_id: int) -> ShopSettings:
    from sqlalchemy import select

    return db.scalar(select(ShopSettings).where(ShopSettings.shop_id == shop_id))


def topbar(db: Session, current: CurrentUser) -> dict:
    from app.supplements import dollars_waiting, follow_ups_due_count
    from app.routes.repair_orders import needs_reply_count

    now = utcnow()
    settings = shop_settings(db, current.shop_id)
    return {
        "needs_reply": needs_reply_count(db, current.shop_id),
        "follow_ups_due": follow_ups_due_count(db, current.shop_id, now),
        "waiting": format_cents(dollars_waiting(db, current.shop_id)),
        "mode": settings.messaging_mode if settings else MessagingMode.DEMO,
    }


def render(request: Request, name: str, db: Session | None = None, current: CurrentUser | None = None, status_code: int = 200, **context):
    context["request"] = request
    context["csrf_token"] = csrf_token(request)
    context["current"] = current
    flash = request.session.pop("flash", None) if "session" in request.scope else None
    context.setdefault("flash", flash)
    if current is not None and db is not None:
        context.setdefault("topbar", topbar(db, current))
        context.setdefault("tz", current.shop.timezone)
    return templates.TemplateResponse(request, name, context, status_code=status_code)
