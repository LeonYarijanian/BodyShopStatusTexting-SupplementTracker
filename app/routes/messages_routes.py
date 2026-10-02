"""Texting actions on the RO page, the message log, and the DEMO-only send-now button."""

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth import CurrentUser, get_db, get_owned, require_user
from app.business_days import local_datetime_at, to_local, utcnow
from app.enums import ConsentMethod, ConsentStatus, MessageDirection, MessageStatus, MessagingMode
from app.messaging.engine import (
    MessagingError,
    cancel_message,
    current_consent,
    retry_message,
    run_sender,
    send_manual_text,
    sender_number,
    settings_for,
)
from app.messaging.inbound import handle_inbound
from app.models import Message, RepairOrder
from app.routes import is_htmx, render, topbar
from app.routes.repair_orders import CHECKIN_CONSENT_METHODS, CONSENT_SCRIPT, record_consent

router = APIRouter()

PAGE_SIZE = 50
CONSENT_LABELS = {ConsentStatus.OPTED_IN: "Opted in", ConsentStatus.OPTED_OUT: "Opted out"}
METHOD_LABELS = {
    ConsentMethod.IN_PERSON_VERBAL: "in person (verbal)",
    ConsentMethod.SIGNED_FORM: "signed form",
    ConsentMethod.KEYWORD: "text keyword",
    ConsentMethod.IMPORTED: "imported",
}


def message_time(message: Message) -> dt.datetime:
    if message.direction == MessageDirection.OUTBOUND and message.sent_at is not None:
        return message.sent_at
    if message.status == MessageStatus.SCHEDULED and message.scheduled_send_at is not None:
        return message.scheduled_send_at
    return message.created_at


def messages_panel_context(db: Session, current: CurrentUser, ro: RepairOrder) -> dict:
    tz = current.shop.timezone
    shop_settings = settings_for(db, current.shop_id)
    consent = current_consent(db, current.shop_id, ro.customer.phone_e164)
    messages = db.scalars(select(Message).where(Message.repair_order_id == ro.id).order_by(Message.created_at, Message.id)).all()
    thread = []
    for message in messages:
        when = to_local(message_time(message), tz)
        thread.append(
            {
                "m": message,
                "time": when.strftime("%Y-%m-%d %H:%M"),
                "scheduled_for": to_local(message.scheduled_send_at, tz).strftime("%H:%M") if message.scheduled_send_at else "",
            }
        )
    return {
        "ro": ro,
        "thread": thread,
        "consent": consent,
        "consent_label": CONSENT_LABELS[consent.status] if consent else "No consent",
        "consent_method_label": METHOD_LABELS.get(consent.method, "") if consent else "",
        "opted_out_by_text": consent is not None and consent.status == ConsentStatus.OPTED_OUT,
        "consent_script": CONSENT_SCRIPT,
        "consent_methods": CHECKIN_CONSENT_METHODS,
        "is_demo": shop_settings.messaging_mode == MessagingMode.DEMO,
        "panel_error": None,
    }


def _panel_response(request: Request, db: Session, current: CurrentUser, ro: RepairOrder, error: str | None = None):
    if is_htmx(request):
        context = messages_panel_context(db, current, ro)
        context["panel_error"] = error
        return render(request, "_ro_messages.html", db, current, status_code=422 if error else 200, oob=True, topbar=topbar(db, current), **context)
    if error:
        request.session["flash"] = {"kind": "error", "message": error}
    return RedirectResponse(f"/ro/{ro.id}", status_code=303)


@router.post("/ro/{ro_id}/consent")
async def record_consent_route(ro_id: int, request: Request, current: CurrentUser = Depends(require_user), db: Session = Depends(get_db)):
    ro = get_owned(db, RepairOrder, ro_id, current.shop_id)
    form = await request.form()
    method = (form.get("consent_method") or "").strip()
    now = utcnow()
    consent = current_consent(db, current.shop_id, ro.customer.phone_e164)
    error = None
    if consent is not None and consent.status == ConsentStatus.OPTED_OUT:
        error = "Customer opted out by text. They must text START to resubscribe."
    elif method not in [m.value for m in CHECKIN_CONSENT_METHODS]:
        error = "Choose how the customer gave consent."
    else:
        record_consent(db, current.shop_id, ro.customer, ConsentStatus.OPTED_IN, ConsentMethod(method), current.id, now)
        db.commit()
    return _panel_response(request, db, current, ro, error)


@router.post("/ro/{ro_id}/messages")
async def send_text_route(ro_id: int, request: Request, current: CurrentUser = Depends(require_user), db: Session = Depends(get_db)):
    ro = get_owned(db, RepairOrder, ro_id, current.shop_id)
    form = await request.form()
    error = None
    photo = None
    upload = form.get("photo")
    if upload is not None and hasattr(upload, "read") and getattr(upload, "filename", ""):
        from app.media import MAX_PHOTO_BYTES

        photo = await upload.read(MAX_PHOTO_BYTES + 1)
    try:
        send_manual_text(db, ro, form.get("body") or "", current.id, utcnow(), request.app.state.settings, photo=photo)
        db.commit()
    except MessagingError as exc:
        db.rollback()
        error = str(exc)
    return _panel_response(request, db, current, ro, error)


@router.post("/ro/{ro_id}/simulate-reply")
async def simulate_reply_route(ro_id: int, request: Request, current: CurrentUser = Depends(require_user), db: Session = Depends(get_db)):
    ro = get_owned(db, RepairOrder, ro_id, current.shop_id)
    shop_settings = settings_for(db, current.shop_id)
    if shop_settings.messaging_mode != MessagingMode.DEMO:
        raise HTTPException(status_code=404, detail="Not found.")
    form = await request.form()
    body = (form.get("body") or "").strip()
    if not body:
        return _panel_response(request, db, current, ro, "Type the customer's reply.")
    handle_inbound(
        db,
        current.shop,
        from_e164=ro.customer.phone_e164,
        to_e164=sender_number(current.shop, shop_settings),
        body=body,
        now=utcnow(),
        app_settings=request.app.state.settings,
    )
    db.commit()
    return _panel_response(request, db, current, ro)


@router.post("/ro/{ro_id}/mark-handled")
def mark_handled_route(ro_id: int, request: Request, current: CurrentUser = Depends(require_user), db: Session = Depends(get_db)):
    ro = get_owned(db, RepairOrder, ro_id, current.shop_id)
    ro.needs_reply = False
    db.commit()
    return _panel_response(request, db, current, ro)


@router.post("/messages/{message_id}/retry")
def retry_route(message_id: int, request: Request, current: CurrentUser = Depends(require_user), db: Session = Depends(get_db)):
    message = get_owned(db, Message, message_id, current.shop_id)
    error = None
    try:
        retry_message(message, utcnow())
        db.commit()
    except MessagingError as exc:
        error = str(exc)
    return _message_action_response(request, db, current, message, error)


@router.post("/messages/{message_id}/cancel")
def cancel_route(message_id: int, request: Request, current: CurrentUser = Depends(require_user), db: Session = Depends(get_db)):
    message = get_owned(db, Message, message_id, current.shop_id)
    error = None
    try:
        cancel_message(message)
        db.commit()
    except MessagingError as exc:
        error = str(exc)
    return _message_action_response(request, db, current, message, error)


def _message_action_response(request: Request, db: Session, current: CurrentUser, message: Message, error: str | None):
    if message.repair_order_id is not None:
        ro = get_owned(db, RepairOrder, message.repair_order_id, current.shop_id)
        return _panel_response(request, db, current, ro, error)
    if error:
        request.session["flash"] = {"kind": "error", "message": error}
    return RedirectResponse("/messages", status_code=303)


@router.post("/demo/send-now")
def demo_send_now(request: Request, current: CurrentUser = Depends(require_user), db: Session = Depends(get_db)):
    """DEMO only: run the sender on every SCHEDULED message as if it were due."""
    if settings_for(db, current.shop_id).messaging_mode != MessagingMode.DEMO:
        raise HTTPException(status_code=404, detail="Not found.")
    count = run_sender(utcnow(), db, request.app.state.settings, force=True, shop_id=current.shop_id)
    request.session["flash"] = {"kind": "notice", "message": f"Ran the sender on {count} scheduled text{'s' if count != 1 else ''}."}
    referer = request.headers.get("referer") or "/"
    target = referer if referer.startswith(str(request.base_url)) else "/"
    return RedirectResponse(target, status_code=303)


def _parse_date(text: str | None) -> dt.date | None:
    try:
        return dt.date.fromisoformat(text) if text else None
    except ValueError:
        return None


@router.get("/messages")
def message_log(
    request: Request,
    direction: str = "",
    status: str = "",
    date_from: str = "",
    date_to: str = "",
    page: int = 1,
    current: CurrentUser = Depends(require_user),
    db: Session = Depends(get_db),
):
    tz = current.shop.timezone
    query = select(Message).where(Message.shop_id == current.shop_id)
    if direction in MessageDirection.__members__:
        query = query.where(Message.direction == MessageDirection(direction))
    if status in MessageStatus.__members__:
        query = query.where(Message.status == MessageStatus(status))
    start = _parse_date(date_from)
    end = _parse_date(date_to)
    if start:
        query = query.where(Message.created_at >= local_datetime_at(start, dt.time(0, 0), tz))
    if end:
        query = query.where(Message.created_at < local_datetime_at(end + dt.timedelta(days=1), dt.time(0, 0), tz))
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    page = max(1, page)
    rows = db.scalars(query.order_by(Message.created_at.desc(), Message.id.desc()).offset((page - 1) * PAGE_SIZE).limit(PAGE_SIZE)).all()
    ro_numbers = dict(
        db.execute(select(RepairOrder.id, RepairOrder.ro_number).where(RepairOrder.shop_id == current.shop_id)).all()
    )
    return render(
        request,
        "messages.html",
        db,
        current,
        rows=rows,
        ro_numbers=ro_numbers,
        filters={"direction": direction, "status": status, "date_from": date_from, "date_to": date_to},
        page=page,
        pages=max(1, -(-total // PAGE_SIZE)),
        total=total,
        directions=list(MessageDirection),
        statuses=list(MessageStatus),
    )
