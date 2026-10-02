"""Public photo URLs for picture messages. Twilio fetches these when it sends an MMS."""

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import get_db
from app.media import TOKEN_RE, media_path
from app.models import Message

router = APIRouter()


@router.get("/media/{token}")
def media(token: str, db: Session = Depends(get_db)):
    if not TOKEN_RE.match(token):
        raise HTTPException(status_code=404, detail="Not found.")
    message = db.scalar(select(Message).where(Message.media_token == token))
    if message is None or not message.media_content_type:
        raise HTTPException(status_code=404, detail="Not found.")
    path = media_path(token, message.media_content_type)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Not found.")
    return FileResponse(
        path,
        media_type=message.media_content_type,
        headers={"Cache-Control": "private, max-age=3600", "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer"},
    )
