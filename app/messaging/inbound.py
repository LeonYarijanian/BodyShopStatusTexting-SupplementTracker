"""STOP / START / HELP keywords and other inbound replies (Section 7)."""

import datetime as dt

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.enums import (
    ACTIVE_STAGES,
    ConsentMethod,
    ConsentPurpose,
    ConsentStatus,
    MessageDirection,
    MessageKind,
    MessageStatus,
    MessagingMode,
)
from app.messaging.engine import customer_for_phone, deliver, sender_number, settings_for
from app.messaging.templates import replace_variables, template_variables
from app.locations import location_for_number, phone_for
from app.models import Consent, Customer, Location, Message, RepairOrder, Shop

OPT_OUT_WORDS = {"STOP", "STOPALL", "UNSUBSCRIBE", "CANCEL", "END", "QUIT", "REVOKE", "OPTOUT", "OPT OUT"}
OPT_IN_WORDS = {"START", "UNSTOP"}
HELP_WORDS = {"HELP", "INFO"}

OPT_OUT_REPLY = "{shop_name}: You're unsubscribed and won't get more texts from us. Reply START to resubscribe."
OPT_IN_REPLY = "{shop_name}: You're resubscribed to repair updates. Reply STOP to opt out."
HELP_REPLY = "{shop_name} repair updates. Questions? Call {shop_phone}. Msg & data rates may apply. Reply STOP to opt out."


def normalize_keyword(body: str) -> str:
    """Trim, convert to uppercase and collapse inner spaces to 1 space."""
    return " ".join((body or "").strip().upper().split())


def ro_for_phone(db: Session, shop_id: int, phone_e164: str, location_id: int | None = None) -> RepairOrder | None:
    """The most recently checked-in active RO for this phone; else the most recently checked-in RO.

    When the text came to a location's number, that location's ROs come first (Section 16 item 9).
    """
    base = (
        select(RepairOrder)
        .join(Customer, RepairOrder.customer_id == Customer.id)
        .where(RepairOrder.shop_id == shop_id, Customer.phone_e164 == phone_e164)
        .order_by(RepairOrder.checked_in_at.desc(), RepairOrder.id.desc())
    )
    if location_id is not None:
        here = base.where(RepairOrder.location_id == location_id)
        found = db.scalar(here.where(RepairOrder.current_stage.in_(ACTIVE_STAGES)).limit(1)) or db.scalar(here.limit(1))
        if found is not None:
            return found
    active = db.scalar(base.where(RepairOrder.current_stage.in_(ACTIVE_STAGES)).limit(1))
    return active or db.scalar(base.limit(1))


def _reply(
    db: Session,
    shop: Shop,
    kind: MessageKind,
    template: str,
    to_e164: str,
    ro: RepairOrder | None,
    customer_id: int | None,
    now: dt.datetime,
    app_settings: Settings,
    location: Location | None = None,
) -> Message:
    """A keyword reply, sent from the number the customer texted, naming that location's phone."""
    shop_settings = settings_for(db, shop.id)
    location = location or (ro.location if ro is not None else None)
    body = replace_variables(
        template,
        template_variables(first_name="", shop_name=shop.name, shop_phone_e164=phone_for(shop, location), vehicle="", ro_number="", review_url=""),
    )
    message = Message(
        shop_id=shop.id,
        repair_order_id=ro.id if ro else None,
        customer_id=customer_id,
        direction=MessageDirection.OUTBOUND,
        kind=kind,
        status=MessageStatus.SCHEDULED,
        to_e164=to_e164,
        from_e164=sender_number(shop, shop_settings, location),
        body=body,
        scheduled_send_at=now,
    )
    db.add(message)
    db.flush()
    deliver(db, message, shop, shop_settings, now, app_settings, location)
    db.flush()
    return message


def handle_inbound(
    db: Session,
    shop: Shop,
    from_e164: str,
    to_e164: str,
    body: str,
    now: dt.datetime,
    app_settings: Settings | None = None,
    provider_message_id: str | None = None,
) -> Message:
    """Store an inbound text and apply its effect. Never replies automatically to a non-keyword text."""
    app_settings = app_settings or get_settings()
    shop_settings = settings_for(db, shop.id)
    keyword = normalize_keyword(body)
    location = location_for_number(db, shop.id, to_e164)
    ro = ro_for_phone(db, shop.id, from_e164, location.id if location else None)
    customer = customer_for_phone(db, shop.id, from_e164)
    is_keyword = keyword in OPT_OUT_WORDS or keyword in OPT_IN_WORDS or keyword in HELP_WORDS

    inbound = Message(
        shop_id=shop.id,
        repair_order_id=ro.id if ro else None,
        customer_id=customer.id if customer else None,
        direction=MessageDirection.INBOUND,
        kind=MessageKind.INBOUND_KEYWORD if is_keyword else MessageKind.INBOUND_REPLY,
        status=MessageStatus.RECEIVED,
        to_e164=to_e164,
        from_e164=from_e164,
        body=body,
        provider_message_id=provider_message_id,
    )
    db.add(inbound)
    db.flush()

    if not is_keyword:
        if ro is not None:
            ro.needs_reply = True
        db.flush()
        return inbound

    # In LIVE mode Twilio may already answer keywords itself; then only record the keyword and consent change.
    send_reply = not (shop_settings.messaging_mode == MessagingMode.LIVE and shop_settings.twilio_handles_keyword_replies)

    if keyword in OPT_OUT_WORDS:
        if customer is not None:
            # STOP covers every kind of text: repair updates and review requests.
            for purpose in ConsentPurpose:
                db.add(
                    Consent(
                        shop_id=shop.id,
                        customer_id=customer.id,
                        phone_e164=from_e164,
                        status=ConsentStatus.OPTED_OUT,
                        method=ConsentMethod.KEYWORD,
                        purpose=purpose,
                        recorded_by_user_id=None,
                        recorded_at=now,
                    )
                )
        for scheduled in db.scalars(
            select(Message).where(
                Message.shop_id == shop.id,
                Message.to_e164 == from_e164,
                Message.status == MessageStatus.SCHEDULED,
            )
        ).all():
            scheduled.status = MessageStatus.BLOCKED_OPTED_OUT
        db.flush()
        if send_reply:
            _reply(db, shop, MessageKind.OPT_OUT_CONFIRMATION, OPT_OUT_REPLY, from_e164, ro, inbound.customer_id, now, app_settings, location)
    elif keyword in OPT_IN_WORDS:
        if customer is not None:
            db.add(
                Consent(
                    shop_id=shop.id,
                    customer_id=customer.id,
                    phone_e164=from_e164,
                    status=ConsentStatus.OPTED_IN,
                    method=ConsentMethod.KEYWORD,
                    recorded_by_user_id=None,
                    recorded_at=now,
                )
            )
            db.flush()
        if send_reply:
            _reply(db, shop, MessageKind.OPT_IN_CONFIRMATION, OPT_IN_REPLY, from_e164, ro, inbound.customer_id, now, app_settings, location)
    elif send_reply:
        _reply(db, shop, MessageKind.HELP_REPLY, HELP_REPLY, from_e164, ro, inbound.customer_id, now, app_settings, location)
    db.flush()
    return inbound
