"""Email delivery (Section 16 roadmap): DemoMailer writes to ./outbox, SmtpMailer sends for real.

Real email needs ALLOW_LIVE_EMAIL=true, SMTP_HOST and EMAIL_FROM in .env. Any provider with SMTP works
(Postmark, SendGrid, Amazon SES, Google Workspace).
"""

import re
import smtplib
import uuid
from email.message import EmailMessage
from email.utils import formataddr, make_msgid
from pathlib import Path

from app.business_days import utcnow
from app.config import Settings, get_settings

OUTBOX_DIR = Path("outbox")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def valid_email(value: str) -> bool:
    return bool(value) and len(value) <= 254 and EMAIL_RE.match(value) is not None


def mask_email(value: str) -> str:
    """ana@example.com -> a***@example.com (never log full addresses)."""
    local, _, domain = value.partition("@")
    return f"{local[:1]}***@{domain}" if domain else "***"


def build_message(*, sender: str, to: list[str], subject: str, body: str, reply_to: str | None = None, html: str | None = None) -> EmailMessage:
    message = EmailMessage()
    message["From"] = sender
    message["To"] = ", ".join(to)
    message["Subject"] = subject
    if reply_to:
        message["Reply-To"] = reply_to
    message["Message-ID"] = make_msgid(domain=sender.rsplit("@", 1)[-1].rstrip(">") if "@" in sender else None)
    message.set_content(body)
    if html:
        message.add_alternative(html, subtype="html")
    return message


class DemoMailer:
    """Nothing leaves the computer: each email is written to ./outbox as a .eml file."""

    def send(self, *, to: list[str], subject: str, body: str, reply_to: str | None = None, sender_name: str = "", html: str | None = None) -> str:
        sender = formataddr((sender_name, "no-reply@demo.local"))
        message = build_message(sender=sender, to=to, subject=subject, body=body, reply_to=reply_to, html=html)
        OUTBOX_DIR.mkdir(parents=True, exist_ok=True)
        stamp = utcnow().strftime("%Y%m%dT%H%M%SZ")
        path = OUTBOX_DIR / f"{stamp}-{uuid.uuid4().hex[:8]}.eml"
        path.write_bytes(bytes(message))
        print(f"[DEMO EMAIL] to={', '.join(mask_email(t) for t in to)} subject={subject}", flush=True)
        return str(path)


class SmtpMailer:
    def __init__(self, settings: Settings):
        self.settings = settings

    def send(self, *, to: list[str], subject: str, body: str, reply_to: str | None = None, sender_name: str = "", html: str | None = None) -> str:
        s = self.settings
        if not s.live_email:
            raise RuntimeError("SmtpMailer needs ALLOW_LIVE_EMAIL=true, SMTP_HOST and EMAIL_FROM.")
        message = build_message(sender=formataddr((sender_name, s.EMAIL_FROM)), to=to, subject=subject, body=body, reply_to=reply_to, html=html)
        with smtplib.SMTP(s.SMTP_HOST, s.SMTP_PORT, timeout=30) as smtp:
            if s.SMTP_STARTTLS:
                smtp.starttls()
            if s.SMTP_USERNAME:
                smtp.login(s.SMTP_USERNAME, s.SMTP_PASSWORD)
            smtp.send_message(message)
        return message["Message-ID"]


def get_mailer(settings: Settings | None = None):
    settings = settings or get_settings()
    return SmtpMailer(settings) if settings.live_email else DemoMailer()
