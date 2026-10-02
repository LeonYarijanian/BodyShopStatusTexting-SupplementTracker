"""DemoProvider and TwilioProvider (Section 7)."""

import uuid

from app.config import Settings
from app.enums import MessagingMode
from app.models import ShopSettings


def mask_phone(phone: str) -> str:
    """+18185550123 -> +1818***0123 (first 5 and last 4 characters around ***)."""
    if len(phone) <= 9:
        return "***"
    return f"{phone[:5]}***{phone[-4:]}"


class ProviderError(RuntimeError):
    pass


class DemoProvider:
    """DEMO mode: nothing leaves the computer."""

    def send(self, to: str, body: str, from_: str | None = None) -> str:
        message_id = f"demo-{uuid.uuid4()}"
        print(f"[DEMO SMS] to={mask_phone(to)} body={body}", flush=True)
        return message_id


def make_twilio_client(account_sid: str, auth_token: str):
    """Imported lazily so DEMO mode and tests never import twilio."""
    from twilio.rest import Client

    return Client(account_sid, auth_token)


class TwilioProvider:
    """LIVE mode: real texts through Twilio."""

    def __init__(self, shop_settings: ShopSettings, app_settings: Settings):
        self.shop_settings = shop_settings
        self.app_settings = app_settings

    def send(self, to: str, body: str, from_: str | None = None) -> str:
        if self.shop_settings.messaging_mode != MessagingMode.LIVE or not self.app_settings.ALLOW_LIVE_SMS:
            raise ProviderError("TwilioProvider can send only in LIVE mode with ALLOW_LIVE_SMS=true.")
        client = make_twilio_client(self.app_settings.TWILIO_ACCOUNT_SID, self.app_settings.TWILIO_AUTH_TOKEN)
        kwargs = {
            "to": to,
            "body": body,
            "status_callback": self.app_settings.PUBLIC_BASE_URL.rstrip("/") + "/webhooks/twilio/status",
        }
        if self.shop_settings.twilio_messaging_service_sid:
            kwargs["messaging_service_sid"] = self.shop_settings.twilio_messaging_service_sid
        else:
            kwargs["from_"] = self.shop_settings.twilio_from_e164
        message = client.messages.create(**kwargs)
        return message.sid


def get_provider(shop_settings: ShopSettings, app_settings: Settings):
    if shop_settings.messaging_mode == MessagingMode.LIVE:
        return TwilioProvider(shop_settings, app_settings)
    return DemoProvider()
