"""Photos sent as picture messages (Section 16 item 5).

Each photo is stored once under ./media/<token>.<ext>, where the token is long and random. Twilio fetches
it from PUBLIC_BASE_URL/media/<token> when sending. Photos are deleted 30 days after the text, and right
away when the customer's data is deleted.
"""

import datetime as dt
import re
import secrets
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Message

MEDIA_DIR = Path("media")
MAX_PHOTO_BYTES = 5 * 1024 * 1024  # Twilio's limit for a picture message
RETENTION_DAYS = 30
TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{32}$")
EXTENSIONS = {"image/jpeg": "jpg", "image/png": "png", "image/gif": "gif"}


class PhotoError(ValueError):
    pass


def detect_image_type(data: bytes) -> str:
    """The content type from the file's first bytes; the browser's claimed type is not trusted."""
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    raise PhotoError("The photo must be a JPEG, PNG or GIF image.")


def media_path(token: str, content_type: str) -> Path:
    return MEDIA_DIR / f"{token}.{EXTENSIONS[content_type]}"


def save_photo(data: bytes) -> tuple[str, str]:
    """Validate and store a photo. Returns (token, content_type)."""
    if not data:
        raise PhotoError("The photo is empty.")
    if len(data) > MAX_PHOTO_BYTES:
        raise PhotoError("The photo must be 5 MB or smaller.")
    content_type = detect_image_type(data)
    token = secrets.token_urlsafe(24)  # 32 characters
    MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    media_path(token, content_type).write_bytes(data)
    return token, content_type


def public_media_url(token: str, base_url: str) -> str:
    return f"{base_url.rstrip('/')}/media/{token}"


def delete_photo(message: Message) -> None:
    if message.media_token and message.media_content_type:
        media_path(message.media_token, message.media_content_type).unlink(missing_ok=True)
    message.media_token = None
    message.media_content_type = None


def delete_old_media(now: dt.datetime, db: Session) -> int:
    """Delete photos whose text is older than RETENTION_DAYS."""
    cutoff = now - dt.timedelta(days=RETENTION_DAYS)
    old = db.scalars(select(Message).where(Message.media_token.is_not(None), Message.created_at < cutoff)).all()
    for message in old:
        delete_photo(message)
    db.commit()
    return len(old)
