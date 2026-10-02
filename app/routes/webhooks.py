"""Twilio inbound and status callbacks (LIVE only). Exempt from CSRF; they check Twilio's signature instead."""

import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import get_db
from app.business_days import utcnow
from app.enums import MessageStatus, MessagingMode
from app.messaging.inbound import handle_inbound
from app.messaging.providers import mask_phone
from app.models import Location, Message, Shop, ShopSettings

router = APIRouter()
log = logging.getLogger(__name__)

EMPTY_TWIML = '<?xml version="1.0" encoding="UTF-8"?><Response></Response>'
STATUS_MAP = {"delivered": MessageStatus.DELIVERED, "failed": MessageStatus.FAILED, "undelivered": MessageStatus.FAILED}


def _any_live_shop(db: Session) -> bool:
    return db.scalar(select(ShopSettings.id).where(ShopSettings.messaging_mode == MessagingMode.LIVE).limit(1)) is not None


def shop_for_number(db: Session, to_e164: str) -> Shop | None:
    """The LIVE shop that owns the texted number: its own Twilio number or one of its locations' numbers."""
    if not to_e164:
        return None
    shop_id = db.scalar(
        select(ShopSettings.shop_id).where(ShopSettings.twilio_from_e164 == to_e164, ShopSettings.messaging_mode == MessagingMode.LIVE)
    )
    if shop_id is None:
        shop_id = db.scalar(
            select(Location.shop_id)
            .join(ShopSettings, ShopSettings.shop_id == Location.shop_id)
            .where(Location.twilio_from_e164 == to_e164, ShopSettings.messaging_mode == MessagingMode.LIVE)
            .limit(1)
        )
    return db.get(Shop, shop_id) if shop_id is not None else None


def signature_is_valid(request: Request, params: dict) -> bool:
    """Validate X-Twilio-Signature with the auth token and the full URL (PUBLIC_BASE_URL + path)."""
    settings = request.app.state.settings
    signature = request.headers.get("X-Twilio-Signature", "")
    if not signature or not settings.TWILIO_AUTH_TOKEN or not settings.PUBLIC_BASE_URL:
        return False
    from twilio.request_validator import RequestValidator

    url = settings.PUBLIC_BASE_URL.rstrip("/") + request.url.path
    return RequestValidator(settings.TWILIO_AUTH_TOKEN).validate(url, params, signature)


def _twiml() -> Response:
    return Response(EMPTY_TWIML, media_type="application/xml")


@router.post("/webhooks/twilio/inbound")
async def twilio_inbound(request: Request, db: Session = Depends(get_db)):
    if not _any_live_shop(db):
        return Response("Not found", status_code=404)
    params = {key: value for key, value in (await request.form()).items()}
    if not signature_is_valid(request, params):
        return Response("Invalid signature", status_code=403)
    to_e164 = params.get("To", "")
    shop = shop_for_number(db, to_e164)
    if shop is None:
        return _twiml()
    handle_inbound(
        db,
        shop,
        from_e164=params.get("From", ""),
        to_e164=to_e164,
        body=params.get("Body", ""),
        now=utcnow(),
        app_settings=request.app.state.settings,
        provider_message_id=params.get("MessageSid") or None,
    )
    db.commit()
    log.info("Inbound text from %s stored", mask_phone(params.get("From", "")))
    return _twiml()


@router.post("/webhooks/twilio/status")
async def twilio_status(request: Request, db: Session = Depends(get_db)):
    if not _any_live_shop(db):
        return Response("Not found", status_code=404)
    params = {key: value for key, value in (await request.form()).items()}
    if not signature_is_valid(request, params):
        return Response("Invalid signature", status_code=403)
    new_status = STATUS_MAP.get((params.get("MessageStatus") or "").lower())
    message_sid = params.get("MessageSid") or ""
    if new_status is None or not message_sid:
        return Response("", status_code=200)
    message = db.scalar(select(Message).where(Message.provider_message_id == message_sid))
    if message is not None:
        message.status = new_status
        if new_status == MessageStatus.FAILED:
            message.error_text = (params.get("ErrorCode") or "")[:200] or None
        db.commit()
    return Response("", status_code=200)
