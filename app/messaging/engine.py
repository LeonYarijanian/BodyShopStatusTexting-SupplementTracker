"""Messaging engine: consent, quiet hours, cool-off, daily cap (Section 7)."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.enums import ConsentStatus
from app.models import Consent


def current_consent(db: Session, shop_id: int, phone_e164: str) -> Consent | None:
    """The row with the latest recorded_at for (shop_id, phone_e164); ties go to the highest id."""
    return db.scalar(
        select(Consent)
        .where(Consent.shop_id == shop_id, Consent.phone_e164 == phone_e164)
        .order_by(Consent.recorded_at.desc(), Consent.id.desc())
        .limit(1)
    )


def consent_statuses(db: Session, shop_id: int) -> dict[str, ConsentStatus]:
    """Current consent status for every phone in a shop that has any consent row."""
    rows = db.scalars(select(Consent).where(Consent.shop_id == shop_id).order_by(Consent.recorded_at, Consent.id)).all()
    return {row.phone_e164: row.status for row in rows}


def maybe_schedule_stage_text(db: Session, ro, stage, now) -> None:
    """Section 6 rule 6. Built in Phase 2."""
    return None
