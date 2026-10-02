"""Settings (ADMIN only): Shop, Texting, Supplements, Insurers and adjusters, Users, Mode."""

import datetime as dt
import html
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth import CurrentUser, get_db, get_owned, hash_password, require_admin, validate_new_password
from app.enums import STAGE_LABELS, MessagingMode, Role, Stage
from app.messaging.providers import live_preconditions, switch_mode
from app.messaging.templates import (
    DEFAULT_TEMPLATES,
    PREVIEW_FIRST_NAME,
    PREVIEW_RO_NUMBER,
    PREVIEW_VEHICLE,
    REVIEW_VARIABLES,
    TEXTABLE_STAGES,
    TemplateError,
    add_identification,
    parse_us_phone,
    replace_variables,
    template_variables,
    validate_template,
)
from app.digest import MAX_RECIPIENTS as MAX_DIGEST_RECIPIENTS
from app.mailer import valid_email
from app.models import Adjuster, Insurer, Shop, ShopSettings, User
from app.routes import render, shop_settings
from app.status_page import PREVIEW_STATUS_LINK_TOKEN, status_url

router = APIRouter()

TABS = {
    "shop": "Shop",
    "texting": "Texting",
    "supplements": "Supplements",
    "insurers": "Insurers and adjusters",
    "users": "Users",
    "mode": "Mode",
}


class SettingsError(ValueError):
    pass


# ---------------------------------------------------------------- validation helpers


def _text(form, name: str) -> str:
    value = form.get(name)
    return value.strip() if isinstance(value, str) else ""


def _checked(form, name: str) -> bool:
    return form.get(name) in ("yes", "on", "true", "1")


def parse_hhmm(text: str, field: str, low: str, high: str) -> dt.time:
    """An HH:MM time within [low, high], or an error naming the field and its range."""
    message = f"{field} must be from {low} to {high}."
    try:
        hour, minute = text.split(":")
        if len(hour) != 2 or len(minute) != 2:
            raise ValueError
        value = dt.time(int(hour), int(minute))
    except ValueError:
        raise SettingsError(message) from None
    if not dt.time.fromisoformat(low) <= value <= dt.time.fromisoformat(high):
        raise SettingsError(message)
    return value


def parse_int(text: str, field: str, low: int, high: int) -> int:
    message = f"{field} must be from {low} to {high}."
    try:
        value = int(text)
    except (TypeError, ValueError):
        raise SettingsError(message) from None
    if not low <= value <= high:
        raise SettingsError(message)
    return value


def parse_optional_phone(text: str, field: str) -> str | None:
    if not text:
        return None
    try:
        return parse_us_phone(text)
    except ValueError:
        raise SettingsError(f"{field} must be a valid phone number.") from None


def preview_template(shop: Shop, settings: ShopSettings, template: str) -> str:
    """Live preview: first name Maria, vehicle 2021 Honda Accord, RO 24-1187 (rendering rules 1 and 2)."""
    variables = template_variables(
        first_name=PREVIEW_FIRST_NAME,
        shop_name=shop.name,
        shop_phone_e164=shop.phone_e164,
        vehicle=PREVIEW_VEHICLE,
        ro_number=PREVIEW_RO_NUMBER,
        review_url=settings.review_url,
        status_link=status_url(PREVIEW_STATUS_LINK_TOKEN),
    )
    return add_identification(replace_variables(template, variables), shop.name)


# ---------------------------------------------------------------- saving each tab


def save_shop(db: Session, shop: Shop, form) -> None:
    name = _text(form, "name")
    if not 1 <= len(name) <= 120:
        raise SettingsError("name must be 1 to 120 characters.")
    phone = parse_optional_phone(_text(form, "phone"), "phone")
    if phone is None:
        raise SettingsError("phone is required.")
    timezone = _text(form, "timezone")
    try:
        ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError):
        raise SettingsError("timezone must be a valid IANA time zone name, such as America/Los_Angeles.") from None
    address = _text(form, "address")
    if len(address) > 200:
        raise SettingsError("address must be at most 200 characters.")
    shop.name, shop.phone_e164, shop.timezone, shop.address = name, phone, timezone, address or None


def save_texting(settings: ShopSettings, form) -> None:
    """Validate everything first; a value outside its range rejects the whole save."""
    quiet_start = parse_hhmm(_text(form, "quiet_start"), "quiet_start", "12:00", "21:00")
    quiet_end = parse_hhmm(_text(form, "quiet_end"), "quiet_end", "08:00", "11:00")
    cool_off = parse_int(_text(form, "cool_off_minutes"), "cool_off_minutes", 0, 60)
    daily_cap = parse_int(_text(form, "daily_cap"), "daily_cap", 1, 10)
    review_url = _text(form, "review_url")
    if review_url and not review_url.startswith("https://"):
        raise SettingsError("review_url must be empty or start with https://.")
    if len(review_url) > 500:
        raise SettingsError("review_url must be at most 500 characters.")
    enabled = {stage.value: _checked(form, f"enabled_{stage.value}") for stage in TEXTABLE_STAGES}
    enabled[Stage.CANCELLED.value] = False
    templates = {}
    for stage in TEXTABLE_STAGES:
        text = form.get(f"template_{stage.value}")
        text = text if isinstance(text, str) else (settings.stage_templates or {}).get(stage.value, DEFAULT_TEMPLATES[stage])
        text = text.strip()
        try:
            validate_template(text)
        except TemplateError as exc:
            raise SettingsError(f"{STAGE_LABELS[stage]} template: {exc}") from None
        if not text:
            raise SettingsError(f"{STAGE_LABELS[stage]} template cannot be empty.")
        templates[stage.value] = text

    review_enabled = _checked(form, "review_request_enabled")
    delay_text = _text(form, "review_request_delay_days") or str(settings.review_request_delay_days)
    review_delay = parse_int(delay_text, "review_request_delay_days", 1, 14)
    review_template = form.get("review_request_template")
    review_template = (review_template if isinstance(review_template, str) else settings.review_request_template).strip()
    try:
        validate_template(review_template, REVIEW_VARIABLES)
    except TemplateError as exc:
        raise SettingsError(f"Review request template: {exc}") from None
    if "{review_url}" not in review_template:
        raise SettingsError("Review request template must include {review_url}.")
    if review_enabled and not review_url:
        raise SettingsError("Set the review URL before turning on review requests.")

    settings.quiet_start = quiet_start
    settings.quiet_end = quiet_end
    settings.cool_off_minutes = cool_off
    settings.daily_cap = daily_cap
    settings.review_url = review_url
    settings.stage_text_enabled = enabled
    settings.stage_templates = templates
    settings.review_request_enabled = review_enabled
    settings.review_request_delay_days = review_delay
    settings.review_request_template = review_template


def parse_email_list(text: str, field: str, limit: int) -> list[str]:
    emails = [part.strip().lower() for part in text.replace("\n", ",").replace(";", ",").split(",") if part.strip()]
    for email in emails:
        if not valid_email(email):
            raise SettingsError(f"{field}: {email} is not a valid email address.")
    if len(emails) > limit:
        raise SettingsError(f"{field} can have at most {limit} emails.")
    return list(dict.fromkeys(emails))


def save_supplements(settings: ShopSettings, form) -> None:
    interval = parse_int(_text(form, "default_follow_up_interval_business_days"), "default_follow_up_interval_business_days", 1, 10)
    due_time = parse_hhmm(_text(form, "follow_up_due_time"), "follow_up_due_time", "06:00", "17:00")
    warning = parse_int(_text(form, "concentration_warning_pct"), "concentration_warning_pct", 10, 90)
    digest_enabled = _checked(form, "digest_enabled")
    recipients = parse_email_list(_text(form, "digest_recipients"), "digest_recipients", MAX_DIGEST_RECIPIENTS)
    digest_time = parse_hhmm(_text(form, "digest_send_time") or "07:30", "digest_send_time", "06:00", "10:00")
    if digest_enabled and not recipients:
        raise SettingsError("digest_recipients needs at least 1 email to turn the daily digest on.")
    settings.default_follow_up_interval_business_days = interval
    settings.follow_up_due_time = due_time
    settings.concentration_warning_pct = warning
    settings.digest_enabled = digest_enabled
    settings.digest_recipients = recipients
    settings.digest_send_time = digest_time


def save_insurer(db: Session, current: CurrentUser, form) -> None:
    insurer_id = _text(form, "insurer_id")
    insurer = get_owned(db, Insurer, int(insurer_id), current.shop_id) if insurer_id else None
    name = _text(form, "name")
    if not 1 <= len(name) <= 120:
        raise SettingsError("Insurer name must be 1 to 120 characters.")
    clash = select(Insurer.id).where(Insurer.shop_id == current.shop_id, func.lower(Insurer.name) == name.lower())
    if insurer is not None:
        clash = clash.where(Insurer.id != insurer.id)
    if db.scalar(clash) is not None:
        raise SettingsError(f"An insurer named {name} already exists.")
    interval_text = _text(form, "follow_up_interval_business_days")
    interval = parse_int(interval_text, "follow_up_interval_business_days", 1, 10) if interval_text else None
    claims_email = _text(form, "claims_email") or None
    if claims_email and ("@" not in claims_email or len(claims_email) > 254):
        raise SettingsError("claims_email must be a valid email address.")
    claims_phone = parse_optional_phone(_text(form, "claims_phone"), "claims_phone")
    if insurer is None:
        insurer = Insurer(shop_id=current.shop_id, name=name)
        db.add(insurer)
    insurer.name = name
    insurer.is_drp = _checked(form, "is_drp")
    insurer.follow_up_interval_business_days = interval
    insurer.claims_email = claims_email
    insurer.claims_phone_e164 = claims_phone


def save_adjuster(db: Session, current: CurrentUser, form) -> None:
    adjuster_id = _text(form, "adjuster_id")
    adjuster = get_owned(db, Adjuster, int(adjuster_id), current.shop_id) if adjuster_id else None
    insurer = get_owned(db, Insurer, int(_text(form, "insurer_id") or 0), current.shop_id)
    full_name = _text(form, "full_name")
    if not 1 <= len(full_name) <= 120:
        raise SettingsError("Adjuster name must be 1 to 120 characters.")
    email = _text(form, "email") or None
    if email and ("@" not in email or len(email) > 254):
        raise SettingsError("Adjuster email must be a valid email address.")
    phone = parse_optional_phone(_text(form, "phone"), "Adjuster phone")
    if adjuster is None:
        adjuster = Adjuster(shop_id=current.shop_id, insurer_id=insurer.id, full_name=full_name)
        db.add(adjuster)
    adjuster.insurer_id = insurer.id
    adjuster.full_name = full_name
    adjuster.email = email
    adjuster.phone_e164 = phone


def save_user(db: Session, current: CurrentUser, form, action: str) -> str:
    if action == "add_user":
        email = _text(form, "email").lower()
        if not email or "@" not in email or len(email) > 254:
            raise SettingsError("Enter a valid email address.")
        if db.scalar(select(User.id).where(User.email == email)) is not None:
            raise SettingsError("A user with this email already exists.")
        full_name = _text(form, "full_name")
        if not 1 <= len(full_name) <= 120:
            raise SettingsError("Full name must be 1 to 120 characters.")
        role = _text(form, "role")
        if role not in Role.__members__:
            raise SettingsError("Choose a role.")
        password = form.get("password") or ""
        try:
            validate_new_password(password)
        except ValueError as exc:
            raise SettingsError(str(exc)) from None
        db.add(User(shop_id=current.shop_id, email=email, password_hash=hash_password(password), full_name=full_name, role=Role(role), is_active=True))
        return f"Added {full_name}."
    user = get_owned(db, User, int(_text(form, "user_id") or 0), current.shop_id)
    if action == "deactivate":
        if user.id == current.id:
            raise SettingsError("You cannot deactivate yourself.")
        user.is_active = False
        return f"Deactivated {user.full_name}."
    if action == "reset_password":
        password = form.get("password") or ""
        try:
            validate_new_password(password)
        except ValueError as exc:
            raise SettingsError(str(exc)) from None
        user.password_hash = hash_password(password)
        return f"Reset the password for {user.full_name}."
    raise SettingsError("Unknown action.")


def save_mode(settings: ShopSettings, form, app_settings) -> list[str]:
    """Save the Twilio fields and checkboxes, or switch the mode. Returns failed LIVE preconditions."""
    action = _text(form, "action")
    if action == "switch":
        mode = _text(form, "mode")
        if mode not in MessagingMode.__members__:
            raise SettingsError("Choose DEMO or LIVE.")
        return switch_mode(settings, MessagingMode(mode), app_settings)
    from_e164 = _text(form, "twilio_from_e164")
    if from_e164:
        try:
            if parse_us_phone(from_e164) != from_e164:
                raise ValueError
        except ValueError:
            raise SettingsError("twilio_from_e164 must be empty or a valid E.164 number, such as +18185550188.") from None
    service_sid = _text(form, "twilio_messaging_service_sid")
    if service_sid and not service_sid.startswith("MG"):
        raise SettingsError("twilio_messaging_service_sid must be empty or start with MG.")
    settings.twilio_from_e164 = from_e164
    settings.twilio_messaging_service_sid = service_sid
    settings.a2p_10dlc_approved = _checked(form, "a2p_10dlc_approved")
    settings.consent_script_confirmed = _checked(form, "consent_script_confirmed")
    settings.twilio_handles_keyword_replies = _checked(form, "twilio_handles_keyword_replies")
    return []


# ---------------------------------------------------------------- pages


def settings_context(db: Session, current: CurrentUser, request: Request, tab: str) -> dict:
    settings = shop_settings(db, current.shop_id)
    insurers = db.scalars(select(Insurer).where(Insurer.shop_id == current.shop_id).order_by(func.lower(Insurer.name))).all()
    users = db.scalars(select(User).where(User.shop_id == current.shop_id).order_by(User.full_name)).all()
    stage_rows = []
    for stage in TEXTABLE_STAGES:
        template = (settings.stage_templates or {}).get(stage.value) or DEFAULT_TEMPLATES[stage]
        try:
            preview = preview_template(current.shop, settings, template)
        except TemplateError as exc:
            preview = str(exc)
        stage_rows.append(
            {
                "stage": stage,
                "label": STAGE_LABELS[stage],
                "enabled": bool((settings.stage_text_enabled or {}).get(stage.value)),
                "template": template,
                "preview": preview,
            }
        )
    return {
        "tab": tab,
        "tabs": TABS,
        "settings": settings,
        "shop": current.shop,
        "insurers": insurers,
        "users": users,
        "stage_rows": stage_rows,
        "preconditions": live_preconditions(settings, request.app.state.settings),
        "roles": list(Role),
    }


@router.get("/settings")
def settings_page(request: Request, tab: str = "shop", current: CurrentUser = Depends(require_admin), db: Session = Depends(get_db)):
    if tab not in TABS:
        tab = "shop"
    return render(request, "settings.html", db, current, error=None, failed=[], **settings_context(db, current, request, tab))


@router.post("/settings")
async def settings_save(request: Request, current: CurrentUser = Depends(require_admin), db: Session = Depends(get_db)):
    form = await request.form()
    tab = _text(form, "tab")
    if tab not in TABS:
        tab = "shop"
    settings = shop_settings(db, current.shop_id)
    failed: list[str] = []
    notice = "Saved."
    try:
        if tab == "shop":
            save_shop(db, current.shop, form)
        elif tab == "texting":
            save_texting(settings, form)
        elif tab == "supplements":
            save_supplements(settings, form)
        elif tab == "insurers":
            if _text(form, "action") == "adjuster":
                save_adjuster(db, current, form)
            else:
                save_insurer(db, current, form)
        elif tab == "users":
            notice = save_user(db, current, form, _text(form, "action"))
        elif tab == "mode":
            failed = save_mode(settings, form, request.app.state.settings)
            if failed:
                raise SettingsError("LIVE mode refused. These conditions are not met:")
            notice = f"Mode is {settings.messaging_mode.value}." if _text(form, "action") == "switch" else "Saved."
    except SettingsError as exc:
        db.rollback()
        return render(
            request, "settings.html", db, current, status_code=422, error=str(exc), failed=failed, **settings_context(db, current, request, tab)
        )
    db.commit()
    request.session["flash"] = {"kind": "notice", "message": notice}
    return RedirectResponse(f"/settings?tab={tab}", status_code=303)


@router.post("/settings/preview")
async def settings_preview(request: Request, current: CurrentUser = Depends(require_admin), db: Session = Depends(get_db)):
    """HTMX live preview of a template while it is edited."""
    form = await request.form()
    stage = _text(form, "stage")
    template = form.get(f"template_{stage}") if stage else None
    if not isinstance(template, str):
        return HTMLResponse("")
    try:
        validate_template(template.strip())
        text = preview_template(current.shop, shop_settings(db, current.shop_id), template.strip())
    except TemplateError as exc:
        text = str(exc)
    return HTMLResponse(html.escape(text))
