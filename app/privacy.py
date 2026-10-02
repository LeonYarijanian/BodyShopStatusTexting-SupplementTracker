"""Delete a customer's personal data on request (Section 16 item 2).

Personal data goes: name, phone, email, consent history, message text and photos, VIN and claim number.
What stays: the repair orders themselves (vehicle year, make and model, amounts, stages and dates),
so the shop's reports and cycle times stay correct.
"""

import datetime as dt

from sqlalchemy import delete, or_, select
from sqlalchemy.orm import Session

from app.enums import MessageStatus
from app.media import delete_photo
from app.models import Consent, Customer, Message, RepairOrder

DELETED_NAME = "Deleted"
REMOVED_BODY = "[removed at the customer's request]"
CUSTOMER_DATA_DELETED = "CUSTOMER_DATA_DELETED"


def placeholder_phone(customer_id: int) -> str:
    """Fits phone_e164 (16 characters), is never a real number, and stays unique per shop."""
    return f"deleted-{customer_id}"[:16]


def anonymize_customer(db: Session, customer: Customer, now: dt.datetime) -> dict:
    """Remove the customer's personal data. Returns counts of what changed. Running it twice changes nothing."""
    if customer.anonymized_at is not None:
        return {"already": True}
    phone = customer.phone_e164
    placeholder = placeholder_phone(customer.id)

    messages = db.scalars(
        select(Message).where(
            Message.shop_id == customer.shop_id,
            or_(Message.customer_id == customer.id, Message.to_e164 == phone, Message.from_e164 == phone),
        )
    ).all()
    cancelled = 0
    for message in messages:
        message.body = REMOVED_BODY
        delete_photo(message)
        if message.to_e164 == phone:
            message.to_e164 = placeholder
        if message.from_e164 == phone:
            message.from_e164 = placeholder
        if message.status == MessageStatus.SCHEDULED:
            message.status = MessageStatus.CANCELLED_SUPERSEDED
            message.error_text = CUSTOMER_DATA_DELETED
            cancelled += 1

    consents = db.execute(
        delete(Consent).where(Consent.shop_id == customer.shop_id, or_(Consent.customer_id == customer.id, Consent.phone_e164 == phone))
    ).rowcount

    ros = db.scalars(select(RepairOrder).where(RepairOrder.customer_id == customer.id)).all()
    for ro in ros:
        ro.vin = None
        ro.claim_number = None
        ro.needs_reply = False
        ro.status_token = None  # the status page link stops working

    customer.first_name = DELETED_NAME
    customer.last_name = None
    customer.email = None
    customer.phone_e164 = placeholder
    customer.anonymized_at = now
    db.flush()
    return {"messages": len(messages), "cancelled": cancelled, "consents": consents, "repair_orders": len(ros)}
