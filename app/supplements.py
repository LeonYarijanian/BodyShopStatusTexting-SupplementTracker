"""Supplement transitions, aging and follow-ups (Section 8)."""

import datetime as dt

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.business_days import business_days_between
from app.enums import AgingBucket, SupplementStatus
from app.models import Supplement


def days_open(supplement: Supplement, now: dt.datetime, tz: str) -> int | None:
    """Business days waited for a decision. Defined only when submitted_at is set."""
    if supplement.submitted_at is None:
        return None
    end = supplement.decided_at if supplement.decided_at is not None else now
    return business_days_between(supplement.submitted_at, end, tz)


def aging_bucket(days: int) -> AgingBucket:
    if days <= 1:
        return AgingBucket.FRESH
    if days <= 3:
        return AgingBucket.WATCH
    if days <= 6:
        return AgingBucket.LATE
    return AgingBucket.CRITICAL


def dollars_waiting(db: Session, shop_id: int) -> int:
    """Sum of requested_cents over all SUBMITTED supplements."""
    total = db.scalar(
        select(func.coalesce(func.sum(Supplement.requested_cents), 0)).where(
            Supplement.shop_id == shop_id, Supplement.status == SupplementStatus.SUBMITTED
        )
    )
    return int(total or 0)


def follow_ups_due_count(db: Session, shop_id: int, now: dt.datetime) -> int:
    """SUBMITTED supplements whose next_follow_up_due_at is at or before now."""
    return int(
        db.scalar(
            select(func.count(Supplement.id)).where(
                Supplement.shop_id == shop_id,
                Supplement.status == SupplementStatus.SUBMITTED,
                Supplement.next_follow_up_due_at.is_not(None),
                Supplement.next_follow_up_due_at <= now,
            )
        )
        or 0
    )
