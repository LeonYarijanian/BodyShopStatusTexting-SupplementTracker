"""Messaging engine: consent, quiet hours, cool-off, daily cap (Section 7)."""

import datetime as dt

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.business_days import local_datetime_at, to_local
from app.config import Settings, get_settings
from app.enums import (
    ConsentPurpose,
    ConsentStatus,
    MessageDirection,
    MessageKind,
    MessageStatus,
    MessagingMode,
    Stage,
)
from app.messaging.providers import get_provider
from app.messaging.templates import (
    DEFAULT_TEMPLATES,
    MAX_TEXT_LENGTH,
    add_identification,
    add_stop_suffix,
    replace_variables,
    template_variables,
)
from app.models import Consent, Customer, Message, RepairOrder, Shop, ShopSettings, StageEvent

SENT_OK = (MessageStatus.SENT, MessageStatus.DELIVERED)
# Automatic keyword replies skip the consent, quiet-hours, cool-off and cap checks.
KEYWORD_REPLY_KINDS = (MessageKind.OPT_OUT_CONFIRMATION, MessageKind.OPT_IN_CONFIRMATION, MessageKind.HELP_REPLY)
BODY_TOO_LONG = "BODY_TOO_LONG"
CANCELLED_BY_STAFF = "CANCELLED_BY_STAFF"


class MessagingError(ValueError):
    pass


# ---------------------------------------------------------------- lookups


def current_consent(db: Session, shop_id: int, phone_e164: str, purpose: ConsentPurpose = ConsentPurpose.REPAIR_UPDATES) -> Consent | None:
    """The row with the latest recorded_at for (shop_id, phone_e164) and this purpose; ties go to the highest id."""
    return db.scalar(
        select(Consent)
        .where(Consent.shop_id == shop_id, Consent.phone_e164 == phone_e164, Consent.purpose == purpose)
        .order_by(Consent.recorded_at.desc(), Consent.id.desc())
        .limit(1)
    )


def consent_statuses(db: Session, shop_id: int) -> dict[str, ConsentStatus]:
    """Current repair-update consent status for every phone in a shop that has any consent row."""
    rows = db.scalars(
        select(Consent)
        .where(Consent.shop_id == shop_id, Consent.purpose == ConsentPurpose.REPAIR_UPDATES)
        .order_by(Consent.recorded_at, Consent.id)
    ).all()
    return {row.phone_e164: row.status for row in rows}


def settings_for(db: Session, shop_id: int) -> ShopSettings:
    return db.scalar(select(ShopSettings).where(ShopSettings.shop_id == shop_id))


def sender_number(shop: Shop, shop_settings: ShopSettings) -> str:
    """Shop phone in DEMO mode, the Twilio number in LIVE mode."""
    if shop_settings.messaging_mode == MessagingMode.LIVE and shop_settings.twilio_from_e164:
        return shop_settings.twilio_from_e164
    return shop.phone_e164


def has_successful_outbound(db: Session, shop_id: int, phone_e164: str) -> bool:
    return (
        db.scalar(
            select(Message.id)
            .where(
                Message.shop_id == shop_id,
                Message.direction == MessageDirection.OUTBOUND,
                Message.to_e164 == phone_e164,
                Message.status.in_(SENT_OK),
            )
            .limit(1)
        )
        is not None
    )


# ---------------------------------------------------------------- rendering


def stage_template(shop_settings: ShopSettings, stage: Stage) -> str | None:
    if stage == Stage.CANCELLED:
        return None
    return (shop_settings.stage_templates or {}).get(stage.value) or DEFAULT_TEMPLATES[stage]


def ro_variables(shop: Shop, shop_settings: ShopSettings, ro: RepairOrder) -> dict[str, str]:
    return template_variables(
        first_name=ro.customer.first_name,
        shop_name=shop.name,
        shop_phone_e164=shop.phone_e164,
        vehicle=ro.vehicle,
        ro_number=ro.ro_number,
        # With review requests on, repair updates stay informational: the review link goes only in the review request.
        review_url="" if shop_settings.review_request_enabled else shop_settings.review_url,
    )


def finish_text(db: Session, shop: Shop, phone_e164: str, text: str) -> str:
    """Rendering rules 2 (identification) and 3 (first-message STOP suffix)."""
    text = add_identification(text, shop.name)
    if not has_successful_outbound(db, shop.id, phone_e164):
        text = add_stop_suffix(text)
    return text


def render_stage_text(db: Session, shop: Shop, shop_settings: ShopSettings, ro: RepairOrder, template: str) -> str:
    """Rendering rules 1 to 3."""
    text = replace_variables(template, ro_variables(shop, shop_settings, ro))
    return finish_text(db, shop, ro.customer.phone_e164, text)


# ---------------------------------------------------------------- scheduling


def maybe_schedule_stage_text(db: Session, ro: RepairOrder, stage: Stage, now: dt.datetime) -> Message | None:
    """Section 6 rule 6: the stage text is on, and the RO has no earlier stage_events row for this stage."""
    stage = Stage(stage)
    shop_settings = settings_for(db, ro.shop_id)
    if stage == Stage.CANCELLED or not (shop_settings.stage_text_enabled or {}).get(stage.value, False):
        return None
    if ro.customer.anonymized_at is not None:
        return None
    reached = db.scalar(
        select(func.count(StageEvent.id)).where(StageEvent.repair_order_id == ro.id, StageEvent.to_stage == stage)
    )
    if reached > 1:
        return None
    return schedule_stage_text(db, ro, stage, now)


def schedule_stage_text(db: Session, ro: RepairOrder, stage: Stage, now: dt.datetime) -> Message | None:
    """Section 7, scheduling a stage text at time T = now."""
    shop = db.get(Shop, ro.shop_id)
    shop_settings = settings_for(db, ro.shop_id)
    template = stage_template(shop_settings, stage)
    if template is None:
        return None
    for old in db.scalars(
        select(Message).where(
            Message.repair_order_id == ro.id,
            Message.kind == MessageKind.STAGE_UPDATE,
            Message.status == MessageStatus.SCHEDULED,
        )
    ).all():
        old.status = MessageStatus.CANCELLED_SUPERSEDED
    message = Message(
        shop_id=ro.shop_id,
        repair_order_id=ro.id,
        customer_id=ro.customer_id,
        direction=MessageDirection.OUTBOUND,
        kind=MessageKind.STAGE_UPDATE,
        status=MessageStatus.SCHEDULED,
        to_e164=ro.customer.phone_e164,
        from_e164=sender_number(shop, shop_settings),
        body=render_stage_text(db, shop, shop_settings, ro, template),
        stage=stage,
        scheduled_send_at=now + dt.timedelta(minutes=shop_settings.cool_off_minutes),
    )
    db.add(message)
    db.flush()
    return message


# ---------------------------------------------------------------- the sender job


def _next_quiet_end(local_now: dt.datetime, quiet_end: dt.time, tz: str, next_day: bool) -> dt.datetime:
    day = local_now.date() + dt.timedelta(days=1 if next_day else 0)
    return local_datetime_at(day, quiet_end, tz)


def sent_today_count(db: Session, shop_id: int, phone_e164: str, now: dt.datetime, tz: str) -> int:
    """OUTBOUND STAGE_UPDATE texts to this phone sent successfully on the local calendar date of now."""
    day = to_local(now, tz).date()
    start = local_datetime_at(day, dt.time(0, 0), tz)
    end = local_datetime_at(day + dt.timedelta(days=1), dt.time(0, 0), tz)
    return int(
        db.scalar(
            select(func.count(Message.id)).where(
                Message.shop_id == shop_id,
                Message.direction == MessageDirection.OUTBOUND,
                Message.kind == MessageKind.STAGE_UPDATE,
                Message.to_e164 == phone_e164,
                Message.status.in_(SENT_OK),
                Message.sent_at >= start,
                Message.sent_at < end,
            )
        )
        or 0
    )


def deliver(db: Session, message: Message, shop: Shop, shop_settings: ShopSettings, now: dt.datetime, app_settings: Settings) -> None:
    """Checks 4 and 5: length, then send through the active provider."""
    if len(message.body) > MAX_TEXT_LENGTH:
        message.status = MessageStatus.FAILED
        message.error_text = BODY_TOO_LONG
        return
    message.from_e164 = sender_number(shop, shop_settings)
    provider = get_provider(shop_settings, app_settings)
    extra = {}
    if message.media_token:
        from app.media import public_media_url

        extra["media_url"] = public_media_url(message.media_token, app_settings.PUBLIC_BASE_URL)
    try:
        provider_id = provider.send(message.to_e164, message.body, message.from_e164, **extra)
    except Exception as exc:  # any provider error marks the message FAILED; no automatic retry
        message.status = MessageStatus.FAILED
        message.error_text = str(exc)[:200]
        return
    message.sent_at = now
    message.provider_message_id = provider_id
    message.error_text = None
    message.status = MessageStatus.DELIVERED if shop_settings.messaging_mode == MessagingMode.DEMO else MessageStatus.SENT


def process_message(db: Session, message: Message, now: dt.datetime, app_settings: Settings, force: bool = False) -> None:
    """Apply the sender checks in order. The first check that fails decides the outcome."""
    shop = db.get(Shop, message.shop_id)
    shop_settings = settings_for(db, message.shop_id)
    tz = shop.timezone
    skip_checks = message.kind in KEYWORD_REPLY_KINDS

    if not skip_checks:
        purpose = ConsentPurpose.REVIEW_REQUESTS if message.kind == MessageKind.REVIEW_REQUEST else ConsentPurpose.REPAIR_UPDATES
        consent = current_consent(db, message.shop_id, message.to_e164, purpose)
        if consent is None:
            message.status = MessageStatus.BLOCKED_NO_CONSENT
            return
        if consent.status == ConsentStatus.OPTED_OUT:
            message.status = MessageStatus.BLOCKED_OPTED_OUT
            return

        if not force:
            local_now = to_local(now, tz)
            local_time = local_now.time()
            if not (shop_settings.quiet_end <= local_time < shop_settings.quiet_start):
                message.scheduled_send_at = _next_quiet_end(local_now, shop_settings.quiet_end, tz, next_day=local_time >= shop_settings.quiet_end)
                return

        if message.kind == MessageKind.REVIEW_REQUEST:
            from app.messaging.reviews import already_asked

            if already_asked(db, message, now):
                message.status = MessageStatus.CANCELLED_SUPERSEDED
                message.error_text = "ALREADY_ASKED"
                return

        if message.kind == MessageKind.STAGE_UPDATE:
            if sent_today_count(db, message.shop_id, message.to_e164, now, tz) >= shop_settings.daily_cap:
                message.scheduled_send_at = _next_quiet_end(to_local(now, tz), shop_settings.quiet_end, tz, next_day=True)
                return

    deliver(db, message, shop, shop_settings, now, app_settings)


def run_sender(now: dt.datetime, db: Session, app_settings: Settings | None = None, *, force: bool = False, shop_id: int | None = None) -> int:
    """Send every SCHEDULED message due at or before now, oldest first. Returns how many were processed.

    `force=True` is the DEMO-only "Send scheduled texts now" button: every SCHEDULED message is treated
    as due, ignoring the cool-off window and quiet hours (consent, the daily cap and length still apply).
    """
    app_settings = app_settings or get_settings()
    query = select(Message).where(Message.status == MessageStatus.SCHEDULED)
    if not force:
        query = query.where(Message.scheduled_send_at <= now)
    if shop_id is not None:
        query = query.where(Message.shop_id == shop_id)
    messages = db.scalars(query.order_by(Message.scheduled_send_at, Message.id)).all()
    for message in messages:
        process_message(db, message, now, app_settings, force=force)
        db.flush()
    db.commit()
    return len(messages)


# ---------------------------------------------------------------- manual texts, retry, cancel


def send_manual_text(
    db: Session,
    ro: RepairOrder,
    body: str,
    user_id: int,
    now: dt.datetime,
    app_settings: Settings | None = None,
    photo: bytes | None = None,
) -> Message:
    """Insert a MANUAL message due now and run the sender on it immediately.

    Manual texts skip the cool-off window and the daily cap, and still obey consent, quiet hours
    and rendering rules 2 and 3. With `photo`, the text goes out as a picture message.
    """
    body = (body or "").strip()
    if ro.customer.anonymized_at is not None:
        raise MessagingError("This customer's data was deleted at their request, so texting is off.")
    if not 1 <= len(body) <= MAX_TEXT_LENGTH:
        raise MessagingError(f"A text must be 1 to {MAX_TEXT_LENGTH} characters.")
    media_token = media_content_type = None
    if photo:
        from app.media import PhotoError, save_photo

        try:
            media_token, media_content_type = save_photo(photo)
        except PhotoError as exc:
            raise MessagingError(str(exc)) from None
    shop = db.get(Shop, ro.shop_id)
    shop_settings = settings_for(db, ro.shop_id)
    message = Message(
        shop_id=ro.shop_id,
        repair_order_id=ro.id,
        customer_id=ro.customer_id,
        direction=MessageDirection.OUTBOUND,
        kind=MessageKind.MANUAL,
        status=MessageStatus.SCHEDULED,
        to_e164=ro.customer.phone_e164,
        from_e164=sender_number(shop, shop_settings),
        body=finish_text(db, shop, ro.customer.phone_e164, body),
        scheduled_send_at=now,
        created_by_user_id=user_id,
        media_token=media_token,
        media_content_type=media_content_type,
    )
    db.add(message)
    db.flush()
    process_message(db, message, now, app_settings or get_settings())
    db.flush()
    return message


def retry_message(message: Message, now: dt.datetime) -> None:
    if message.status != MessageStatus.FAILED:
        raise MessagingError("Only a failed message can be retried.")
    message.status = MessageStatus.SCHEDULED
    message.scheduled_send_at = now
    message.error_text = None


def cancel_message(message: Message) -> None:
    if message.status != MessageStatus.SCHEDULED:
        raise MessagingError("Only a scheduled message can be cancelled.")
    message.status = MessageStatus.CANCELLED_SUPERSEDED
    message.error_text = CANCELLED_BY_STAFF


def customer_for_phone(db: Session, shop_id: int, phone_e164: str) -> Customer | None:
    return db.scalar(select(Customer).where(Customer.shop_id == shop_id, Customer.phone_e164 == phone_e164))
