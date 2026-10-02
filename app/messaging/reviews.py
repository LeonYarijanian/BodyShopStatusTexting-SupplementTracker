"""Review requests after delivery (Section 16 item 6).

Separate consent: a review request goes only to a customer who said yes to review requests, not just to
repair updates. Separate content: while review requests are on, the review link appears only here, and the
Delivered text stays informational. 1 request per RO, `review_request_delay_days` after delivery at 10:00
local, never twice to the same phone within 365 days. STOP stops it like every other text.
"""

import datetime as dt

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.business_days import local_datetime_at, to_local
from app.enums import MessageDirection, MessageKind, MessageStatus
from app.locations import review_url_for
from app.messaging.templates import REVIEW_VARIABLES, replace_variables
from app.models import Location, Message, RepairOrder, Shop, ShopSettings

SEND_AT = dt.time(10, 0)
ASK_AGAIN_AFTER = dt.timedelta(days=365)
SENT_OK = (MessageStatus.SENT, MessageStatus.DELIVERED)


def review_variables(shop: Shop, settings: ShopSettings, first_name: str, vehicle: str, location: Location | None = None) -> dict[str, str]:
    variables = {"first_name": first_name, "shop_name": shop.name, "vehicle": vehicle, "review_url": review_url_for(settings, location)}
    assert set(variables) == set(REVIEW_VARIABLES)
    return variables


def render_review_request(db: Session, shop: Shop, settings: ShopSettings, ro: RepairOrder) -> str:
    from app.messaging.engine import finish_text

    text = replace_variables(settings.review_request_template, review_variables(shop, settings, ro.customer.first_name, ro.vehicle, ro.location))
    return finish_text(db, shop, ro.customer.phone_e164, text)


def schedule_review_request(db: Session, ro: RepairOrder, now: dt.datetime) -> Message | None:
    """Called when an RO moves to DELIVERED."""
    from app.messaging.engine import sender_number, settings_for

    settings = settings_for(db, ro.shop_id)
    if not settings.review_request_enabled or not review_url_for(settings, ro.location) or ro.customer.anonymized_at is not None:
        return None
    existing = db.scalar(select(Message.id).where(Message.repair_order_id == ro.id, Message.kind == MessageKind.REVIEW_REQUEST).limit(1))
    if existing is not None:
        return None
    shop = db.get(Shop, ro.shop_id)
    send_day = to_local(now, shop.timezone).date() + dt.timedelta(days=settings.review_request_delay_days)
    message = Message(
        shop_id=ro.shop_id,
        repair_order_id=ro.id,
        customer_id=ro.customer_id,
        direction=MessageDirection.OUTBOUND,
        kind=MessageKind.REVIEW_REQUEST,
        status=MessageStatus.SCHEDULED,
        to_e164=ro.customer.phone_e164,
        from_e164=sender_number(shop, settings, ro.location),
        body=render_review_request(db, shop, settings, ro),
        scheduled_send_at=local_datetime_at(send_day, SEND_AT, shop.timezone),
    )
    db.add(message)
    db.flush()
    return message


def cancel_review_request(db: Session, ro: RepairOrder) -> None:
    """Called when an admin moves an RO out of DELIVERED before the request went out."""
    for message in db.scalars(
        select(Message).where(
            Message.repair_order_id == ro.id, Message.kind == MessageKind.REVIEW_REQUEST, Message.status == MessageStatus.SCHEDULED
        )
    ).all():
        message.status = MessageStatus.CANCELLED_SUPERSEDED
        message.error_text = "RO_REOPENED"


def already_asked(db: Session, message: Message, now: dt.datetime) -> bool:
    """True when this phone got a review request from this shop in the last 365 days."""
    return (
        db.scalar(
            select(Message.id)
            .where(
                Message.shop_id == message.shop_id,
                Message.kind == MessageKind.REVIEW_REQUEST,
                Message.to_e164 == message.to_e164,
                Message.status.in_(SENT_OK),
                Message.sent_at >= now - ASK_AGAIN_AFTER,
                Message.id != message.id,
            )
            .limit(1)
        )
        is not None
    )
