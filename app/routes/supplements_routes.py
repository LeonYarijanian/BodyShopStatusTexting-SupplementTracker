"""Supplements page and supplement actions."""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import PlainTextResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import CurrentUser, get_db, get_owned, require_user
from app.business_days import utcnow
from app.enums import FOLLOW_UP_METHOD_LABELS, AgingBucket, PayerType, SupplementStatus
from app.models import RepairOrder, Supplement
from app.money import dollars_to_cents
from app.routes import render
from app.routes.repair_orders import parse_local_datetime
from app.supplements import (
    DECIDED_STATUSES,
    SupplementError,
    aging_bucket,
    create_supplement,
    days_open,
    dollars_waiting,
    edit_draft,
    follow_up_email,
    follow_ups_due_count,
    is_follow_up_due,
    log_follow_up,
    transition,
)

router = APIRouter()

TABS = {
    "open": ("Open", (SupplementStatus.SUBMITTED,)),
    "draft": ("Draft", (SupplementStatus.DRAFT,)),
    "decided": ("Decided", DECIDED_STATUSES),
    "all": ("All", tuple(SupplementStatus)),
}


def supplement_row(supplement: Supplement, now, tz: str) -> dict:
    days = days_open(supplement, now, tz)
    bucket = aging_bucket(days).value if days is not None and supplement.status == SupplementStatus.SUBMITTED else None
    return {
        "s": supplement,
        "days_open": days,
        "bucket": bucket,
        "due": is_follow_up_due(supplement, now),
    }


def ro_supplements_context(db: Session, current: CurrentUser, ro: RepairOrder) -> dict:
    now = utcnow()
    tz = current.shop.timezone
    supplements = db.scalars(select(Supplement).where(Supplement.repair_order_id == ro.id).order_by(Supplement.sequence_number)).all()
    return {
        "show_supplements": ro.payer_type == PayerType.INSURANCE,
        "supplement_rows": [supplement_row(s, now, tz) for s in supplements],
        "follow_up_methods": FOLLOW_UP_METHOD_LABELS,
    }


def _back(request: Request, form, default: str) -> RedirectResponse:
    target = (form.get("next") or "").strip()
    if not target.startswith("/") or target.startswith("//"):
        target = default
    return RedirectResponse(target, status_code=303)


def _flash_error(request: Request, message: str) -> None:
    request.session["flash"] = {"kind": "error", "message": message}


@router.get("/supplements")
def supplements_page(request: Request, tab: str = "open", current: CurrentUser = Depends(require_user), db: Session = Depends(get_db)):
    if tab not in TABS:
        tab = "open"
    now = utcnow()
    tz = current.shop.timezone
    statuses = TABS[tab][1]
    supplements = db.scalars(
        select(Supplement).where(Supplement.shop_id == current.shop_id, Supplement.status.in_(statuses))
    ).all()
    rows = [supplement_row(s, now, tz) for s in supplements]
    rows.sort(key=lambda r: (r["days_open"] if r["days_open"] is not None else -1, r["s"].requested_cents), reverse=True)

    open_rows = [
        supplement_row(s, now, tz)
        for s in db.scalars(select(Supplement).where(Supplement.shop_id == current.shop_id, Supplement.status == SupplementStatus.SUBMITTED)).all()
    ]
    bucket_counts = {bucket.value: 0 for bucket in AgingBucket}
    for row in open_rows:
        bucket_counts[row["bucket"]] += 1
    summary = {
        "open_count": len(open_rows),
        "dollars_waiting": dollars_waiting(db, current.shop_id),
        "bucket_counts": bucket_counts,
        "follow_ups_due": follow_ups_due_count(db, current.shop_id, now),
    }
    return render(
        request,
        "supplements.html",
        db,
        current,
        tab=tab,
        tabs=TABS,
        rows=rows,
        summary=summary,
        follow_up_methods=FOLLOW_UP_METHOD_LABELS,
    )


@router.post("/ro/{ro_id}/supplements")
async def create_supplement_route(ro_id: int, request: Request, current: CurrentUser = Depends(require_user), db: Session = Depends(get_db)):
    ro = get_owned(db, RepairOrder, ro_id, current.shop_id)
    form = await request.form()
    try:
        requested = dollars_to_cents(form.get("requested") or "")
    except ValueError:
        _flash_error(request, "Enter the requested amount in dollars, with at most 2 decimals.")
        return RedirectResponse(f"/ro/{ro.id}", status_code=303)
    try:
        create_supplement(db, ro, form.get("description") or "", requested, current.id, utcnow())
        db.commit()
    except SupplementError as exc:
        db.rollback()
        _flash_error(request, str(exc))
    return RedirectResponse(f"/ro/{ro.id}", status_code=303)


@router.post("/supplements/{supplement_id}/edit")
async def edit_supplement_route(supplement_id: int, request: Request, current: CurrentUser = Depends(require_user), db: Session = Depends(get_db)):
    supplement = get_owned(db, Supplement, supplement_id, current.shop_id)
    form = await request.form()
    try:
        edit_draft(supplement, form.get("description") or "", dollars_to_cents(form.get("requested") or ""))
        db.commit()
    except ValueError as exc:
        db.rollback()
        _flash_error(request, str(exc) if isinstance(exc, SupplementError) else "Enter the requested amount in dollars, with at most 2 decimals.")
    return _back(request, form, f"/ro/{supplement.repair_order_id}")


@router.post("/supplements/{supplement_id}/transition")
async def transition_route(supplement_id: int, request: Request, current: CurrentUser = Depends(require_user), db: Session = Depends(get_db)):
    supplement = get_owned(db, Supplement, supplement_id, current.shop_id)
    form = await request.form()
    to_status = (form.get("to_status") or "").strip()
    now = utcnow()
    try:
        if to_status not in SupplementStatus.__members__:
            raise SupplementError("Choose a status.")
        at_text = (form.get("at") or "").strip()
        at = parse_local_datetime(at_text, current.shop.timezone) if at_text else None
        approved_text = (form.get("approved") or "").strip()
        approved = dollars_to_cents(approved_text) if approved_text else None
        transition(db, supplement, SupplementStatus(to_status), current.id, now, at=at, approved_cents=approved)
        db.commit()
    except SupplementError as exc:
        db.rollback()
        _flash_error(request, str(exc))
    except ValueError:
        db.rollback()
        _flash_error(request, "Check the date and amount you entered.")
    return _back(request, form, f"/ro/{supplement.repair_order_id}")


@router.post("/supplements/{supplement_id}/follow-up")
async def follow_up_route(supplement_id: int, request: Request, current: CurrentUser = Depends(require_user), db: Session = Depends(get_db)):
    supplement = get_owned(db, Supplement, supplement_id, current.shop_id)
    form = await request.form()
    try:
        log_follow_up(db, supplement, (form.get("method") or "").strip(), form.get("note"), current.id, utcnow())
        db.commit()
    except SupplementError as exc:
        db.rollback()
        _flash_error(request, str(exc))
    return _back(request, form, f"/ro/{supplement.repair_order_id}")


@router.get("/supplements/{supplement_id}/email")
def follow_up_email_route(supplement_id: int, current: CurrentUser = Depends(require_user), db: Session = Depends(get_db)):
    supplement = get_owned(db, Supplement, supplement_id, current.shop_id)
    try:
        text = follow_up_email(db, supplement, current.user, utcnow())
    except SupplementError as exc:
        return PlainTextResponse(str(exc), status_code=400)
    return PlainTextResponse(text)

