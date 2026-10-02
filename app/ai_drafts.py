"""AI-drafted adjuster follow-up emails that staff approve before sending (Section 16 item 7).

Claude drafts the email from the supplement's facts only. A person reads, edits and approves every email;
nothing is sent automatically. When there is no ANTHROPIC_API_KEY, when Claude declines or errors, or when the
draft leaves out the exact amount or claim number, the draft comes from the fixed v1 template instead.
"""

import datetime as dt
import json
import logging
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.enums import AdjusterEmailStatus, DraftSource, FollowUpMethod, SupplementStatus
from app.mailer import get_mailer, valid_email
from app.messaging.templates import format_us_phone
from app.models import AdjusterEmail, RepairOrder, Shop, Supplement, User
from app.money import format_cents
from app.supplements import SupplementError, days_open, days_open_phrase, follow_up_email, log_follow_up, submitted_date_text

log = logging.getLogger(__name__)

MODEL = "claude-opus-5-5"
FALLBACK_BETA = "server-side-fallback-2026-07-01"
MAX_SUBJECT = 200
MAX_BODY = 4000
NOT_ALLOWED = ("lawsuit", "attorney", "legal action", "sue", "collections", "small claims", "bad faith", "department of insurance", "complaint")

SYSTEM_PROMPT = """You draft short follow-up emails from a collision repair shop to an insurance adjuster.
The email is about a supplement: extra repair work the shop found after taking the car apart, sent to the insurer for approval and still waiting for a decision.

Write a polite, professional email that asks for the supplement's status and whether the adjuster needs anything else from the shop.
- Use only the facts given. Never invent or change amounts, dates, claim numbers, RO numbers, names, phone numbers, promises or deadlines.
- Include the supplement number, the claim number, the RO number, the vehicle, the requested amount exactly as written, the submitted date and how long it has waited.
- Keep the body under 130 words, with no markdown and no bullet points.
- No threats, legal language, regulators, complaints or pressure. Stay friendly even after several follow-ups.
- Greet the adjuster by first name. Sign off with the staff member's name, the shop name and the shop phone, each on its own line, exactly as given."""

OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {"subject": {"type": "string"}, "body": {"type": "string"}},
    "required": ["subject", "body"],
    "additionalProperties": False,
}


class DraftError(ValueError):
    pass


def make_anthropic_client(api_key: str):
    """Imported lazily so the app runs without the SDK being touched when AI drafts are off. Tests replace this."""
    import anthropic

    return anthropic.Anthropic(api_key=api_key, max_retries=2, timeout=60.0)


def supplement_facts(db: Session, supplement: Supplement, user: User, now: dt.datetime) -> dict:
    ro = db.get(RepairOrder, supplement.repair_order_id)
    shop = db.get(Shop, supplement.shop_id)
    days = days_open(supplement, now, shop.timezone)
    adjuster = supplement.adjuster
    return {
        "supplement_number": f"S{supplement.sequence_number}",
        "claim_number": ro.claim_number or "(no claim number)",
        "ro_number": ro.ro_number,
        "vehicle": ro.vehicle,
        "insurer": ro.insurer.name if ro.insurer else "",
        "description": supplement.description,
        "requested_amount": format_cents(supplement.requested_cents),
        "submitted_date": submitted_date_text(supplement.submitted_at, shop.timezone),
        "waiting": days_open_phrase(days),
        "follow_ups_so_far": supplement.follow_up_count,
        "adjuster_first_name": adjuster.full_name.split()[0] if adjuster and adjuster.full_name.strip() else "there",
        "staff_name": user.full_name,
        "shop_name": shop.name,
        "shop_phone": format_us_phone(shop.phone_e164),
    }


def check_text(subject: str, body: str, facts: dict | None = None) -> None:
    """The same checks for Claude's draft and for a person's edits."""
    if not subject.strip() or len(subject) > MAX_SUBJECT:
        raise DraftError(f"The subject must be 1 to {MAX_SUBJECT} characters.")
    if not body.strip() or len(body) > MAX_BODY:
        raise DraftError(f"The email must be 1 to {MAX_BODY} characters.")
    lowered = f"{subject}\n{body}".lower()
    for phrase in NOT_ALLOWED:
        if re.search(rf"\b{re.escape(phrase)}\b", lowered):
            raise DraftError(f'Remove "{phrase}". Follow-ups stay friendly and never threaten.')
    if facts is not None:
        for key in ("requested_amount", "claim_number", "ro_number"):
            if facts[key] not in f"{subject}\n{body}":
                raise DraftError(f"The draft left out the {key.replace('_', ' ')} {facts[key]}.")


def draft_with_claude(facts: dict, settings: Settings) -> tuple[str, str] | None:
    """(subject, body) from Claude, or None to use the template. Never raises."""
    if not settings.ANTHROPIC_API_KEY:
        return None
    try:
        client = make_anthropic_client(settings.ANTHROPIC_API_KEY)
        response = client.beta.messages.create(
            model=MODEL,
            max_tokens=8000,
            betas=[FALLBACK_BETA],
            fallbacks="default",
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": OUTPUT_SCHEMA}},
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": "Facts:\n" + json.dumps(facts, indent=2)}],
        )
        if response.stop_reason != "end_turn":
            log.warning("AI draft not used: stop_reason=%s", response.stop_reason)
            return None
        text = next(block.text for block in response.content if block.type == "text")
        data = json.loads(text)
        subject, body = str(data["subject"]).strip(), str(data["body"]).strip()
        check_text(subject, body, facts)
        return subject, body
    except Exception as exc:  # any failure falls back to the template; the draft is reviewed by a person anyway
        log.warning("AI draft not used: %s", type(exc).__name__)
        return None


def create_draft(db: Session, supplement: Supplement, user: User, now: dt.datetime, settings: Settings) -> AdjusterEmail:
    if supplement.status != SupplementStatus.SUBMITTED:
        raise DraftError("Follow-up emails are only for SUBMITTED supplements.")
    facts = supplement_facts(db, supplement, user, now)
    drafted = draft_with_claude(facts, settings)
    if drafted is not None:
        subject, body = drafted
        source, model = DraftSource.AI, MODEL
    else:
        text = follow_up_email(db, supplement, user, now)
        first_line, _, rest = text.partition("\n")
        subject, body = first_line.removeprefix("Subject: "), rest.strip()
        source, model = DraftSource.TEMPLATE, None
    adjuster = supplement.adjuster
    email = AdjusterEmail(
        shop_id=supplement.shop_id,
        supplement_id=supplement.id,
        status=AdjusterEmailStatus.DRAFT,
        source=source,
        model=model,
        to_email=(adjuster.email if adjuster and adjuster.email else None),
        subject=subject[:MAX_SUBJECT],
        body=body,
        created_by_user_id=user.id,
    )
    db.add(email)
    db.flush()
    return email


def open_draft(db: Session, supplement_id: int) -> AdjusterEmail | None:
    return db.scalar(
        select(AdjusterEmail)
        .where(AdjusterEmail.supplement_id == supplement_id, AdjusterEmail.status == AdjusterEmailStatus.DRAFT)
        .order_by(AdjusterEmail.id.desc())
        .limit(1)
    )


def save_edits(email: AdjusterEmail, to_email: str, subject: str, body: str) -> None:
    if email.status != AdjusterEmailStatus.DRAFT:
        raise DraftError("Only a draft can be edited.")
    to_email = (to_email or "").strip().lower()
    if to_email and not valid_email(to_email):
        raise DraftError("Enter a valid email address for the adjuster.")
    subject, body = (subject or "").strip(), (body or "").strip()
    check_text(subject, body)
    email.to_email = to_email or None
    email.subject = subject
    email.body = body


def approve_and_send(db: Session, email: AdjusterEmail, user: User, now: dt.datetime, settings: Settings, mailer=None) -> None:
    """A person approved it: send, then log the follow-up (method EMAIL) so the next due date moves."""
    if email.status != AdjusterEmailStatus.DRAFT:
        raise DraftError("This email was already sent or discarded.")
    if not email.to_email or not valid_email(email.to_email):
        raise DraftError("Add the adjuster's email address before sending.")
    supplement = db.get(Supplement, email.supplement_id)
    if supplement.status != SupplementStatus.SUBMITTED:
        raise DraftError("The supplement is no longer waiting for a decision, so there is nothing to follow up.")
    check_text(email.subject, email.body)
    shop = db.get(Shop, email.shop_id)
    mailer = mailer or get_mailer(settings)
    try:
        mailer.send(to=[email.to_email], subject=email.subject, body=email.body, reply_to=user.email, sender_name=f"{user.full_name}, {shop.name}")
    except Exception as exc:
        email.status = AdjusterEmailStatus.FAILED
        email.error_text = str(exc)[:200]
        db.flush()
        raise DraftError(f"The email could not be sent: {email.error_text}") from None
    email.status = AdjusterEmailStatus.SENT
    email.sent_at = now
    email.sent_by_user_id = user.id
    try:
        log_follow_up(db, supplement, FollowUpMethod.EMAIL, f"Emailed: {email.subject}"[:500], user.id, now)
    except SupplementError:
        pass
    db.flush()


def discard(email: AdjusterEmail) -> None:
    if email.status != AdjusterEmailStatus.DRAFT:
        raise DraftError("Only a draft can be discarded.")
    email.status = AdjusterEmailStatus.DISCARDED
