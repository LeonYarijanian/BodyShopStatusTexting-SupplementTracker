"""Daily email digest of supplement follow-ups due (Section 16 item 3).

On business days at the shop's digest_send_time, each shop with the digest on gets 1 email listing every
SUBMITTED supplement whose follow-up is due, oldest first. No email goes out when nothing is due.
"""

import datetime as dt

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.business_days import is_business_day, to_local
from app.config import Settings, get_settings
from app.enums import SupplementStatus
from app.mailer import get_mailer
from app.messaging.templates import format_us_phone
from app.models import Shop, ShopSettings, Supplement
from app.money import format_cents
from app.supplements import days_open, days_open_phrase, dollars_waiting, follow_up_interval

MAX_RECIPIENTS = 5


def due_supplements(db: Session, shop_id: int, now: dt.datetime) -> list[Supplement]:
    rows = db.scalars(
        select(Supplement).where(
            Supplement.shop_id == shop_id,
            Supplement.status == SupplementStatus.SUBMITTED,
            Supplement.next_follow_up_due_at.is_not(None),
            Supplement.next_follow_up_due_at <= now,
        )
    ).all()
    return sorted(rows, key=lambda s: (s.submitted_at, s.id))


def build_digest(db: Session, shop: Shop, now: dt.datetime, base_url: str = "") -> tuple[str, str] | None:
    """(subject, plain-text body), or None when no follow-up is due."""
    due = due_supplements(db, shop.id, now)
    if not due:
        return None
    tz = shop.timezone
    today = to_local(now, tz)
    count = len(due)
    subject = f"{shop.name}: {count} supplement follow-up{'s' if count != 1 else ''} due today"
    lines = [
        f"Supplement follow-ups due on {today.strftime('%a')}, {today.strftime('%b')} {today.day}.",
        f"Dollars waiting on insurers: {format_cents(dollars_waiting(db, shop.id))}.",
        "",
    ]
    for number, supplement in enumerate(due, start=1):
        ro = supplement.repair_order
        customer = ro.customer
        adjuster = supplement.adjuster
        contact = []
        if adjuster and adjuster.email:
            contact.append(adjuster.email)
        if adjuster and adjuster.phone_e164:
            contact.append(format_us_phone(adjuster.phone_e164))
        who = adjuster.full_name if adjuster else "no adjuster on file"
        if contact:
            who += f" ({', '.join(contact)})"
        days = days_open(supplement, now, tz)
        lines += [
            f"{number}. RO {ro.ro_number} S{supplement.sequence_number}: {ro.vehicle}, {customer.first_name}",
            f"   {ro.insurer.name if ro.insurer else 'Insurer'}, claim {ro.claim_number or '(no claim number)'}, adjuster {who}",
            f"   {format_cents(supplement.requested_cents)} requested, waiting {days_open_phrase(days)}, "
            f"{supplement.follow_up_count} follow-up{'s' if supplement.follow_up_count != 1 else ''} so far "
            f"(every {follow_up_interval(db, supplement)} business days)",
            "",
        ]
    if base_url:
        lines.append(f"Log each follow-up and copy the email: {base_url.rstrip('/')}/supplements")
    lines.append("You get this because the daily digest is on in Settings > Supplements.")
    return subject, "\n".join(lines) + "\n"


def digest_is_due(settings: ShopSettings, shop: Shop, now: dt.datetime) -> bool:
    local = to_local(now, shop.timezone)
    return (
        settings.digest_enabled
        and bool(settings.digest_recipients)
        and is_business_day(local.date())
        and local.time() >= settings.digest_send_time
        and settings.digest_last_sent_on != local.date()
    )


def run_digests(now: dt.datetime, db: Session, app_settings: Settings | None = None, mailer=None) -> int:
    """Send every digest that is due. Returns how many emails went out. Marks a shop done even when nothing is due."""
    app_settings = app_settings or get_settings()
    mailer = mailer or get_mailer(app_settings)
    sent = 0
    for settings in db.scalars(select(ShopSettings).where(ShopSettings.digest_enabled.is_(True))).all():
        shop = db.get(Shop, settings.shop_id)
        if not digest_is_due(settings, shop, now):
            continue
        digest = build_digest(db, shop, now, app_settings.PUBLIC_BASE_URL)
        if digest is not None:
            subject, body = digest
            mailer.send(to=list(settings.digest_recipients), subject=subject, body=body, sender_name=shop.name)
            sent += 1
        settings.digest_last_sent_on = to_local(now, shop.timezone).date()
        db.commit()
    return sent
