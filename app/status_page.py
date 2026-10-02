"""Customer status page linked from texts (Section 16 item 8).

Each RO gets a long random token; https://<PUBLIC_BASE_URL>/s/<token> shows the customer where their car is.
The page shows only what the customer already knows from their texts: shop, vehicle, stage and dates. No
prices, insurer, claim number, last name or phone number. Links stop working 30 days after delivery, when the
RO is cancelled, and when the customer's data is deleted.
"""

import datetime as dt
import re
import secrets

from app.config import get_settings
from app.enums import Stage
from app.models import RepairOrder

TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{32}$")
EXPIRES_AFTER_DELIVERY = dt.timedelta(days=30)
PREVIEW_STATUS_LINK_TOKEN = "EXAMPLE-LINK-0000000000000000000"


def ensure_status_token(ro: RepairOrder) -> str:
    if not ro.status_token:
        ro.status_token = secrets.token_urlsafe(24)  # 32 characters
    return ro.status_token


def status_url(token: str, base_url: str | None = None) -> str:
    base = get_settings().PUBLIC_BASE_URL if base_url is None else base_url
    return f"{base.rstrip('/')}/s/{token}"


def link_is_active(ro: RepairOrder, now: dt.datetime) -> bool:
    if ro.customer.anonymized_at is not None or ro.current_stage == Stage.CANCELLED:
        return False
    if ro.delivered_at is not None and now - ro.delivered_at > EXPIRES_AFTER_DELIVERY:
        return False
    return True
