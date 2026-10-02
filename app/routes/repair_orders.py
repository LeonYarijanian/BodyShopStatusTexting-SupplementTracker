"""Repair orders: new RO form, edit, RO detail, stage changes, customer lookup, adjusters."""

import datetime as dt
import math
import re

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth import CurrentUser, get_db, get_owned, require_user
from app.business_days import local_to_utc, to_local, utcnow
from app.enums import (
    ACTIVE_STAGES,
    STAGE_LABELS,
    TERMINAL_STAGES,
    ConsentMethod,
    ConsentStatus,
    MessageDirection,
    PayerType,
    Role,
    Stage,
)
from app.messaging.templates import parse_us_phone
from app.models import Adjuster, Consent, Customer, Insurer, Message, RepairOrder, StageEvent, Supplement, User
from app.money import dollars_to_cents
from app.routes import is_htmx, render

router = APIRouter()

VIN_RE = re.compile(r"^[A-HJ-NPR-Z0-9]{17}$")
CHECKIN_CONSENT_METHODS = (ConsentMethod.IN_PERSON_VERBAL, ConsentMethod.SIGNED_FORM)
CONSENT_SCRIPT = (
    "Can we text you updates about your repair at this number? We'll only send repair updates. "
    "Message and data rates may apply. You can reply STOP anytime to opt out."
)


class ROError(ValueError):
    """A business-rule error shown to the user."""


# ---------------------------------------------------------------- queries


def needs_reply_count(db: Session, shop_id: int) -> int:
    """Active ROs with needs_reply = true."""
    return int(
        db.scalar(
            select(func.count(RepairOrder.id)).where(
                RepairOrder.shop_id == shop_id,
                RepairOrder.needs_reply.is_(True),
                RepairOrder.current_stage.in_(ACTIVE_STAGES),
            )
        )
        or 0
    )


def days_in_shop(ro: RepairOrder, now: dt.datetime) -> int:
    """Whole days from checked_in_at to now (to delivered_at once delivered), rounded down."""
    end = ro.delivered_at if ro.delivered_at is not None else now
    return max(0, math.floor((end - ro.checked_in_at).total_seconds() / 86400))


def payer_label(ro: RepairOrder) -> str:
    if ro.payer_type == PayerType.CUSTOMER_PAY:
        return "Customer pay"
    return ro.insurer.name if ro.insurer else "Insurance"


def insurers_for_shop(db: Session, shop_id: int) -> list[Insurer]:
    return list(db.scalars(select(Insurer).where(Insurer.shop_id == shop_id).order_by(func.lower(Insurer.name))).all())


def adjusters_for_insurer(db: Session, shop_id: int, insurer_id: int | None) -> list[Adjuster]:
    if not insurer_id:
        return []
    return list(
        db.scalars(
            select(Adjuster).where(Adjuster.shop_id == shop_id, Adjuster.insurer_id == insurer_id).order_by(Adjuster.full_name)
        ).all()
    )


# ---------------------------------------------------------------- validation


def _text(form, name: str) -> str:
    value = form.get(name)
    return value.strip() if isinstance(value, str) else ""


def _int_or_none(text: str) -> int | None:
    try:
        return int(text) if text else None
    except ValueError:
        return None


def parse_local_datetime(text: str, tz: str) -> dt.datetime:
    """A datetime-local value ("2026-10-05T13:00") in the shop's time zone -> UTC."""
    return local_to_utc(dt.datetime.fromisoformat(text.strip()), tz)


def validate_vin(text: str) -> str | None:
    vin = (text or "").strip().upper()
    if not vin:
        return None
    if not VIN_RE.match(vin):
        raise ROError("VIN must be exactly 17 characters, A to Z and 0 to 9, with no I, O or Q.")
    return vin


def validate_vehicle_year(text: str, now: dt.datetime, tz: str) -> int:
    max_year = to_local(now, tz).year + 1
    try:
        year = int(str(text).strip())
    except ValueError:
        raise ROError("Vehicle year must be a number.") from None
    if not 1950 <= year <= max_year:
        raise ROError(f"Vehicle year must be from 1950 to {max_year}.")
    return year


def validate_ro_form(db: Session, current: CurrentUser, form, now: dt.datetime, ro: RepairOrder | None = None) -> tuple[dict, dict]:
    """Returns (clean values, errors by field name)."""
    tz = current.shop.timezone
    shop_id = current.shop_id
    errors: dict[str, str] = {}
    clean: dict = {}

    try:
        clean["phone_e164"] = parse_us_phone(_text(form, "phone"))
    except ValueError:
        errors["phone"] = "Enter a valid US phone number."

    def required(name: str, label: str, max_len: int) -> None:
        value = _text(form, name)
        if not value:
            errors[name] = f"{label} is required."
        elif len(value) > max_len:
            errors[name] = f"{label} must be at most {max_len} characters."
        clean[name] = value

    def optional(name: str, label: str, max_len: int) -> None:
        value = _text(form, name)
        if len(value) > max_len:
            errors[name] = f"{label} must be at most {max_len} characters."
        clean[name] = value or None

    required("first_name", "First name", 60)
    optional("last_name", "Last name", 60)
    optional("email", "Email", 254)
    if clean.get("email") and "@" not in clean["email"]:
        errors["email"] = "Enter a valid email address."
    required("ro_number", "RO number", 20)
    required("vehicle_make", "Vehicle make", 40)
    required("vehicle_model", "Vehicle model", 60)
    optional("vehicle_color", "Vehicle color", 30)
    optional("claim_number", "Claim number", 40)

    if clean.get("ro_number") and "ro_number" not in errors:
        query = select(RepairOrder.id).where(RepairOrder.shop_id == shop_id, RepairOrder.ro_number == clean["ro_number"])
        if ro is not None:
            query = query.where(RepairOrder.id != ro.id)
        if db.scalar(query) is not None:
            errors["ro_number"] = "This RO number already exists in this shop."

    try:
        clean["vehicle_year"] = validate_vehicle_year(_text(form, "vehicle_year"), now, tz)
    except ROError as exc:
        errors["vehicle_year"] = str(exc)

    try:
        clean["vin"] = validate_vin(_text(form, "vin"))
    except ROError as exc:
        errors["vin"] = str(exc)

    payer = _text(form, "payer_type")
    if payer not in PayerType.__members__:
        errors["payer_type"] = "Choose a payer type."
    else:
        clean["payer_type"] = PayerType(payer)

    insurer_id = _int_or_none(_text(form, "insurer_id"))
    adjuster_id = _int_or_none(_text(form, "adjuster_id"))
    clean["insurer_id"] = None
    clean["adjuster_id"] = None
    if clean.get("payer_type") == PayerType.INSURANCE:
        insurer = db.get(Insurer, insurer_id) if insurer_id else None
        if insurer is None or insurer.shop_id != shop_id:
            errors["insurer_id"] = "Choose the insurer."
        else:
            clean["insurer_id"] = insurer.id
            if adjuster_id:
                adjuster = db.get(Adjuster, adjuster_id)
                if adjuster is None or adjuster.shop_id != shop_id or adjuster.insurer_id != insurer.id:
                    errors["adjuster_id"] = "The adjuster must belong to the chosen insurer."
                else:
                    clean["adjuster_id"] = adjuster.id
    elif clean.get("payer_type") == PayerType.CUSTOMER_PAY:
        if insurer_id or adjuster_id or clean.get("claim_number"):
            errors["payer_type"] = "A customer-pay RO cannot have an insurer, adjuster or claim number."
        if ro is not None and db.scalar(select(func.count(Supplement.id)).where(Supplement.repair_order_id == ro.id)):
            errors["payer_type"] = "This RO has supplements, so it must stay an insurance RO."

    try:
        clean["original_estimate_cents"] = dollars_to_cents(_text(form, "original_estimate"))
    except ValueError:
        errors["original_estimate"] = "Enter the original estimate in dollars, 0 or more, with at most 2 decimals."

    checked_in_text = _text(form, "checked_in_at")
    if not checked_in_text:
        clean["checked_in_at"] = now
    else:
        try:
            clean["checked_in_at"] = parse_local_datetime(checked_in_text, tz)
        except ValueError:
            errors["checked_in_at"] = "Enter the check-in date and time."
    if clean.get("checked_in_at") and clean["checked_in_at"] > now:
        errors["checked_in_at"] = "Check-in time cannot be in the future."
    if ro is not None and ro.delivered_at is not None and clean.get("checked_in_at") and clean["checked_in_at"] > ro.delivered_at:
        errors["checked_in_at"] = "Check-in time must be on or before the delivery time."

    clean["consent"] = form.get("consent") in ("yes", "on", "true", "1")
    method = _text(form, "consent_method")
    if clean["consent"]:
        if method not in [m.value for m in CHECKIN_CONSENT_METHODS]:
            errors["consent_method"] = "Choose how the customer gave consent."
        else:
            clean["consent_method"] = ConsentMethod(method)
    return clean, errors


# ---------------------------------------------------------------- domain actions


def find_or_create_customer(db: Session, shop_id: int, phone_e164: str, first_name: str, last_name: str | None, email: str | None, update: bool = True) -> Customer:
    customer = db.scalar(select(Customer).where(Customer.shop_id == shop_id, Customer.phone_e164 == phone_e164))
    if customer is None:
        customer = Customer(shop_id=shop_id, phone_e164=phone_e164, first_name=first_name, last_name=last_name, email=email)
        db.add(customer)
        db.flush()
    elif update:
        customer.first_name = first_name
        customer.last_name = last_name
        customer.email = email
    return customer


def record_consent(db: Session, shop_id: int, customer: Customer, status: ConsentStatus, method: ConsentMethod, user_id: int | None, now: dt.datetime) -> Consent:
    consent = Consent(
        shop_id=shop_id,
        customer_id=customer.id,
        phone_e164=customer.phone_e164,
        status=status,
        method=method,
        recorded_by_user_id=user_id,
        recorded_at=now,
    )
    db.add(consent)
    db.flush()
    return consent


def create_repair_order(db: Session, current: CurrentUser, clean: dict, now: dt.datetime) -> RepairOrder:
    """Customer if new, the RO in CHECKED_IN, its first stage_events row, and consent if ticked."""
    customer = find_or_create_customer(
        db, current.shop_id, clean["phone_e164"], clean["first_name"], clean["last_name"], clean["email"]
    )
    ro = RepairOrder(
        shop_id=current.shop_id,
        ro_number=clean["ro_number"],
        customer_id=customer.id,
        vehicle_year=clean["vehicle_year"],
        vehicle_make=clean["vehicle_make"],
        vehicle_model=clean["vehicle_model"],
        vehicle_color=clean["vehicle_color"],
        vin=clean["vin"],
        payer_type=clean["payer_type"],
        insurer_id=clean["insurer_id"],
        adjuster_id=clean["adjuster_id"],
        claim_number=clean["claim_number"] if clean["payer_type"] == PayerType.INSURANCE else None,
        original_estimate_cents=clean["original_estimate_cents"],
        current_stage=Stage.CHECKED_IN,
        checked_in_at=clean["checked_in_at"],
    )
    db.add(ro)
    db.flush()
    if clean.get("consent"):
        record_consent(db, current.shop_id, customer, ConsentStatus.OPTED_IN, clean["consent_method"], current.id, now)
    db.add(
        StageEvent(
            shop_id=current.shop_id,
            repair_order_id=ro.id,
            from_stage=None,
            to_stage=Stage.CHECKED_IN,
            changed_by_user_id=current.id,
            changed_at=now,
        )
    )
    db.flush()
    after_stage_change(db, ro, Stage.CHECKED_IN, now)
    return ro


def update_repair_order(db: Session, current: CurrentUser, ro: RepairOrder, clean: dict) -> RepairOrder:
    customer = find_or_create_customer(
        db, current.shop_id, clean["phone_e164"], clean["first_name"], clean["last_name"], clean["email"]
    )
    ro.customer_id = customer.id
    for field in (
        "ro_number",
        "vehicle_year",
        "vehicle_make",
        "vehicle_model",
        "vehicle_color",
        "vin",
        "payer_type",
        "insurer_id",
        "adjuster_id",
        "original_estimate_cents",
        "checked_in_at",
    ):
        setattr(ro, field, clean[field])
    ro.claim_number = clean["claim_number"] if clean["payer_type"] == PayerType.INSURANCE else None
    db.flush()
    return ro


def change_stage(db: Session, ro: RepairOrder, to_stage: Stage, user: User, now: dt.datetime, final_invoice_cents: int | None = None) -> bool:
    """Section 6 stage change rules. Returns False when the RO is already in that stage."""
    to_stage = Stage(to_stage)
    if to_stage == ro.current_stage:
        return False
    from_stage = ro.current_stage
    if from_stage in TERMINAL_STAGES and user.role != Role.ADMIN:
        raise PermissionError(f"Only an admin can move an RO out of {STAGE_LABELS[from_stage]}.")
    if to_stage == Stage.DELIVERED:
        invoice = final_invoice_cents if final_invoice_cents is not None else ro.final_invoice_cents
        if invoice is None:
            raise ROError("Enter the final invoice before moving to Delivered.")
        ro.final_invoice_cents = invoice
        ro.delivered_at = now
    elif from_stage in TERMINAL_STAGES:
        ro.delivered_at = None
    ro.current_stage = to_stage
    db.add(
        StageEvent(
            shop_id=ro.shop_id,
            repair_order_id=ro.id,
            from_stage=from_stage,
            to_stage=to_stage,
            changed_by_user_id=user.id,
            changed_at=now,
        )
    )
    db.flush()
    after_stage_change(db, ro, to_stage, now)
    return True


def after_stage_change(db: Session, ro: RepairOrder, stage: Stage, now: dt.datetime) -> None:
    """Section 6 rule 6: hand over to the messaging engine (Section 7)."""
    from app.messaging.engine import maybe_schedule_stage_text

    maybe_schedule_stage_text(db, ro, stage, now)


# ---------------------------------------------------------------- pages


def _flash(request: Request, message: str, kind: str = "error") -> None:
    request.session["flash"] = {"kind": kind, "message": message}


def form_context(db: Session, current: CurrentUser, values: dict, errors: dict, ro: RepairOrder | None) -> dict:
    insurer_id = _int_or_none(str(values.get("insurer_id") or ""))
    return {
        "values": values,
        "errors": errors,
        "ro": ro,
        "insurers": insurers_for_shop(db, current.shop_id),
        "adjusters": adjusters_for_insurer(db, current.shop_id, insurer_id),
        "consent_script": CONSENT_SCRIPT,
        "consent_methods": CHECKIN_CONSENT_METHODS,
    }


def _now_local_input(current: CurrentUser) -> str:
    return to_local(utcnow(), current.shop.timezone).strftime("%Y-%m-%dT%H:%M")


@router.get("/ro/new")
def new_ro_page(request: Request, current: CurrentUser = Depends(require_user), db: Session = Depends(get_db)):
    values = {"payer_type": PayerType.INSURANCE.value, "checked_in_at": _now_local_input(current)}
    return render(request, "ro_form.html", db, current, **form_context(db, current, values, {}, None))


@router.post("/ro/new")
async def new_ro_submit(request: Request, current: CurrentUser = Depends(require_user), db: Session = Depends(get_db)):
    form = await request.form()
    now = utcnow()
    clean, errors = validate_ro_form(db, current, form, now)
    if errors:
        return render(request, "ro_form.html", db, current, status_code=422, **form_context(db, current, dict(form), errors, None))
    ro = create_repair_order(db, current, clean, now)
    db.commit()
    return RedirectResponse(f"/ro/{ro.id}", status_code=303)


@router.get("/ro/{ro_id}/edit")
def edit_ro_page(ro_id: int, request: Request, current: CurrentUser = Depends(require_user), db: Session = Depends(get_db)):
    ro = get_owned(db, RepairOrder, ro_id, current.shop_id)
    tz = current.shop.timezone
    values = {
        "phone": ro.customer.phone_e164,
        "first_name": ro.customer.first_name,
        "last_name": ro.customer.last_name or "",
        "email": ro.customer.email or "",
        "ro_number": ro.ro_number,
        "vehicle_year": ro.vehicle_year,
        "vehicle_make": ro.vehicle_make,
        "vehicle_model": ro.vehicle_model,
        "vehicle_color": ro.vehicle_color or "",
        "vin": ro.vin or "",
        "payer_type": ro.payer_type.value,
        "insurer_id": ro.insurer_id or "",
        "adjuster_id": ro.adjuster_id or "",
        "claim_number": ro.claim_number or "",
        "original_estimate": f"{ro.original_estimate_cents // 100}.{ro.original_estimate_cents % 100:02d}",
        "checked_in_at": to_local(ro.checked_in_at, tz).strftime("%Y-%m-%dT%H:%M"),
    }
    return render(request, "ro_form.html", db, current, **form_context(db, current, values, {}, ro))


@router.post("/ro/{ro_id}/edit")
async def edit_ro_submit(ro_id: int, request: Request, current: CurrentUser = Depends(require_user), db: Session = Depends(get_db)):
    ro = get_owned(db, RepairOrder, ro_id, current.shop_id)
    form = await request.form()
    clean, errors = validate_ro_form(db, current, form, utcnow(), ro=ro)
    if errors:
        return render(request, "ro_form.html", db, current, status_code=422, **form_context(db, current, dict(form), errors, ro))
    update_repair_order(db, current, ro, clean)
    db.commit()
    return RedirectResponse(f"/ro/{ro.id}", status_code=303)


@router.get("/customers/lookup")
def customer_lookup(request: Request, phone: str = "", first_name: str = "", last_name: str = "", email: str = "", current: CurrentUser = Depends(require_user), db: Session = Depends(get_db)):
    values = {"first_name": first_name, "last_name": last_name, "email": email}
    try:
        phone_e164 = parse_us_phone(phone)
    except ValueError:
        phone_e164 = None
    if phone_e164:
        customer = db.scalar(select(Customer).where(Customer.shop_id == current.shop_id, Customer.phone_e164 == phone_e164))
        if customer is not None:
            values = {"first_name": customer.first_name, "last_name": customer.last_name or "", "email": customer.email or ""}
    return render(request, "_customer_fields.html", values=values, errors={})


@router.get("/adjusters/options")
def adjuster_options(request: Request, insurer_id: str = "", current: CurrentUser = Depends(require_user), db: Session = Depends(get_db)):
    adjusters = adjusters_for_insurer(db, current.shop_id, _int_or_none(insurer_id))
    return render(request, "_adjuster_select.html", adjusters=adjusters, selected=None, error=None)


@router.post("/adjusters")
async def add_adjuster(request: Request, current: CurrentUser = Depends(require_user), db: Session = Depends(get_db)):
    """The inline Add adjuster on the RO form. Returns the adjuster dropdown with the new adjuster selected."""
    form = await request.form()
    insurer_id = _int_or_none(_text(form, "insurer_id"))
    insurer = db.get(Insurer, insurer_id) if insurer_id else None
    name = _text(form, "new_adjuster_name")
    email = _text(form, "new_adjuster_email") or None
    phone_text = _text(form, "new_adjuster_phone")
    error = None
    if insurer is None or insurer.shop_id != current.shop_id:
        error = "Choose the insurer first."
    elif not name or len(name) > 120:
        error = "Adjuster name is required (at most 120 characters)."
    elif email and (len(email) > 254 or "@" not in email):
        error = "Enter a valid adjuster email."
    phone = None
    if error is None and phone_text:
        try:
            phone = parse_us_phone(phone_text)
        except ValueError:
            error = "Enter a valid adjuster phone number."
    selected = None
    if error is None:
        adjuster = Adjuster(shop_id=current.shop_id, insurer_id=insurer.id, full_name=name, email=email, phone_e164=phone)
        db.add(adjuster)
        db.commit()
        selected = adjuster.id
    adjusters = adjusters_for_insurer(db, current.shop_id, insurer.id if insurer and insurer.shop_id == current.shop_id else None)
    return render(request, "_adjuster_select.html", adjusters=adjusters, selected=selected, error=error)


def timeline(db: Session, ro: RepairOrder) -> list[dict]:
    """Stage changes, supplement events and messages merged, newest first."""
    items = []
    for event in db.scalars(select(StageEvent).where(StageEvent.repair_order_id == ro.id)).all():
        label = STAGE_LABELS[event.to_stage] if event.from_stage is None else f"{STAGE_LABELS[event.from_stage]} → {STAGE_LABELS[event.to_stage]}"
        detail = f"by {event.changed_by.full_name}" + (f" ({event.note})" if event.note else "")
        items.append({"at": event.changed_at, "id": event.id, "kind": "Stage", "text": label, "detail": detail})
    for message in db.scalars(select(Message).where(Message.repair_order_id == ro.id)).all():
        direction = "Text in" if message.direction == MessageDirection.INBOUND else "Text out"
        items.append({"at": message.created_at, "id": message.id, "kind": direction, "text": message.body, "detail": message.status.value})
    return sorted(items, key=lambda item: (item["at"], item["id"]), reverse=True)


def ro_detail_context(db: Session, current: CurrentUser, ro: RepairOrder) -> dict:
    from app.routes.messages_routes import messages_panel_context

    now = utcnow()
    context = {
        "customer": ro.customer,
        "days_in_shop": days_in_shop(ro, now),
        "payer": payer_label(ro),
        "stages": list(Stage),
        "timeline": timeline(db, ro),
    }
    context.update(messages_panel_context(db, current, ro))
    return context


@router.get("/ro/{ro_id}")
def ro_detail(ro_id: int, request: Request, current: CurrentUser = Depends(require_user), db: Session = Depends(get_db)):
    ro = get_owned(db, RepairOrder, ro_id, current.shop_id)
    return render(request, "ro_detail.html", db, current, **ro_detail_context(db, current, ro))


@router.post("/ro/{ro_id}/stage")
async def ro_stage(ro_id: int, request: Request, current: CurrentUser = Depends(require_user), db: Session = Depends(get_db)):
    ro = get_owned(db, RepairOrder, ro_id, current.shop_id)
    form = await request.form()
    stage_text = _text(form, "stage")
    error = None
    if stage_text not in Stage.__members__:
        error = "Choose a stage."
    final_invoice_cents = None
    invoice_text = _text(form, "final_invoice")
    if error is None and invoice_text:
        try:
            final_invoice_cents = dollars_to_cents(invoice_text)
        except ValueError:
            error = "Enter the final invoice in dollars, with at most 2 decimals."
    if error is None:
        try:
            change_stage(db, ro, Stage(stage_text), current.user, utcnow(), final_invoice_cents)
            db.commit()
        except PermissionError as exc:
            db.rollback()
            raise HTTPException(status_code=403, detail=str(exc)) from None
        except ROError as exc:
            db.rollback()
            error = str(exc)

    if _text(form, "source") == "board" and is_htmx(request):
        from app.routes.board import board_context

        return render(request, "_board.html", db, current, status_code=422 if error else 200, error=error, **board_context(db, current))
    if error:
        _flash(request, error)
    return RedirectResponse("/" if _text(form, "source") == "board" else f"/ro/{ro.id}", status_code=303)

