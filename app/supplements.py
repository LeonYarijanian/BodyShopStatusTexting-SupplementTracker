"""Supplement transitions, aging and follow-ups (Section 8)."""

import datetime as dt

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.enums import SupplementStatus
from app.models import Supplement


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
