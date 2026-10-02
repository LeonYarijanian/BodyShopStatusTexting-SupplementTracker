"""Supplement transitions, aging and follow-ups (Section 8)."""

import datetime as dt

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.business_days import add_business_days, business_days_between, local_date, local_datetime_at, to_local
from app.enums import AgingBucket, FollowUpMethod, PayerType, SupplementEventType, SupplementStatus
from app.messaging.templates import format_us_phone
from app.models import Insurer, RepairOrder, Shop, ShopSettings, Supplement, SupplementEvent, User
from app.money import format_cents

S = SupplementStatus
ALLOWED_TRANSITIONS = {
    (None, S.DRAFT),
    (S.DRAFT, S.SUBMITTED),
    (S.DRAFT, S.WITHDRAWN),
    (S.SUBMITTED, S.APPROVED),
    (S.SUBMITTED, S.PARTIALLY_APPROVED),
    (S.SUBMITTED, S.DENIED),
    (S.SUBMITTED, S.WITHDRAWN),
}
DECIDED_STATUSES = (S.APPROVED, S.PARTIALLY_APPROVED, S.DENIED)
FINAL_STATUSES = (S.APPROVED, S.PARTIALLY_APPROVED, S.DENIED, S.WITHDRAWN)

BUCKET_COLORS = {
    AgingBucket.FRESH: "#2e7d32",
    AgingBucket.WATCH: "#f9a825",
    AgingBucket.LATE: "#ef6c00",
    AgingBucket.CRITICAL: "#c62828",
}


class SupplementError(ValueError):
    pass


# ---------------------------------------------------------------- aging


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


def is_follow_up_due(supplement: Supplement, now: dt.datetime) -> bool:
    return (
        supplement.status == S.SUBMITTED
        and supplement.next_follow_up_due_at is not None
        and now >= supplement.next_follow_up_due_at
    )


def dollars_waiting(db: Session, shop_id: int) -> int:
    """Sum of requested_cents over all SUBMITTED supplements."""
    total = db.scalar(
        select(func.coalesce(func.sum(Supplement.requested_cents), 0)).where(
            Supplement.shop_id == shop_id, Supplement.status == S.SUBMITTED
        )
    )
    return int(total or 0)


def follow_ups_due_count(db: Session, shop_id: int, now: dt.datetime) -> int:
    """SUBMITTED supplements whose next_follow_up_due_at is at or before now."""
    return int(
        db.scalar(
            select(func.count(Supplement.id)).where(
                Supplement.shop_id == shop_id,
                Supplement.status == S.SUBMITTED,
                Supplement.next_follow_up_due_at.is_not(None),
                Supplement.next_follow_up_due_at <= now,
            )
        )
        or 0
    )


# ---------------------------------------------------------------- follow-up schedule


def _shop_and_settings(db: Session, shop_id: int) -> tuple[Shop, ShopSettings]:
    shop = db.get(Shop, shop_id)
    settings = db.scalar(select(ShopSettings).where(ShopSettings.shop_id == shop_id))
    return shop, settings


def follow_up_interval(db: Session, supplement: Supplement) -> int:
    """The insurer's follow_up_interval_business_days if set, otherwise the shop default."""
    ro = db.get(RepairOrder, supplement.repair_order_id)
    insurer = db.get(Insurer, ro.insurer_id) if ro.insurer_id else None
    if insurer is not None and insurer.follow_up_interval_business_days:
        return insurer.follow_up_interval_business_days
    _, settings = _shop_and_settings(db, supplement.shop_id)
    return settings.default_follow_up_interval_business_days


def next_due_after(db: Session, supplement: Supplement, start: dt.datetime) -> dt.datetime:
    """add_business_days(local date of start, interval) at follow_up_due_time local, stored in UTC."""
    shop, settings = _shop_and_settings(db, supplement.shop_id)
    due_day = add_business_days(local_date(start, shop.timezone), follow_up_interval(db, supplement))
    return local_datetime_at(due_day, settings.follow_up_due_time, shop.timezone)


# ---------------------------------------------------------------- actions


def _event(db: Session, supplement: Supplement, event_type: SupplementEventType, user_id: int, now: dt.datetime, **fields) -> None:
    db.add(
        SupplementEvent(
            shop_id=supplement.shop_id,
            supplement_id=supplement.id,
            event_type=event_type,
            user_id=user_id,
            occurred_at=now,
            **fields,
        )
    )


def _validate_description(description: str) -> str:
    description = (description or "").strip()
    if not description:
        raise SupplementError("Description is required.")
    if len(description) > 500:
        raise SupplementError("Description must be at most 500 characters.")
    return description


def _validate_requested(requested_cents: int) -> int:
    if requested_cents is None or requested_cents <= 0:
        raise SupplementError("Requested amount must be greater than $0.00.")
    return requested_cents


def create_supplement(db: Session, ro: RepairOrder, description: str, requested_cents: int, user_id: int, now: dt.datetime) -> Supplement:
    if ro.payer_type != PayerType.INSURANCE:
        raise SupplementError("Supplements need an insurance RO.")
    description = _validate_description(description)
    requested_cents = _validate_requested(requested_cents)
    highest = db.scalar(select(func.max(Supplement.sequence_number)).where(Supplement.repair_order_id == ro.id)) or 0
    supplement = Supplement(
        shop_id=ro.shop_id,
        repair_order_id=ro.id,
        sequence_number=highest + 1,
        status=S.DRAFT,
        description=description,
        requested_cents=requested_cents,
        approved_cents=0,
        adjuster_id=ro.adjuster_id,
        follow_up_count=0,
    )
    db.add(supplement)
    db.flush()
    _event(db, supplement, SupplementEventType.STATUS_CHANGE, user_id, now, from_status=None, to_status=S.DRAFT)
    db.flush()
    return supplement


def edit_draft(supplement: Supplement, description: str, requested_cents: int) -> None:
    if supplement.status != S.DRAFT:
        raise SupplementError("Description and requested amount can be edited only while the status is DRAFT.")
    supplement.description = _validate_description(description)
    supplement.requested_cents = _validate_requested(requested_cents)


def transition(
    db: Session,
    supplement: Supplement,
    to_status: SupplementStatus,
    user_id: int,
    now: dt.datetime,
    *,
    at: dt.datetime | None = None,
    approved_cents: int | None = None,
) -> None:
    """Section 8 allowed status changes. `at` is the submitted or decided time (default now)."""
    to_status = SupplementStatus(to_status)
    from_status = supplement.status
    if (from_status, to_status) not in ALLOWED_TRANSITIONS:
        raise SupplementError(f"Not allowed: {from_status.value} -> {to_status.value}")
    when = at or now

    if to_status == S.SUBMITTED:
        ro = db.get(RepairOrder, supplement.repair_order_id)
        if when < ro.checked_in_at:
            raise SupplementError("Submitted time cannot be before the RO's check-in.")
        if when > now:
            raise SupplementError("Submitted time cannot be in the future.")
        supplement.submitted_at = when
        supplement.next_follow_up_due_at = next_due_after(db, supplement, when)
    elif to_status in DECIDED_STATUSES:
        if when < supplement.submitted_at:
            raise SupplementError("Decided time cannot be before the submitted time.")
        if when > now:
            raise SupplementError("Decided time cannot be in the future.")
        if to_status == S.APPROVED:
            supplement.approved_cents = supplement.requested_cents
        elif to_status == S.PARTIALLY_APPROVED:
            if approved_cents is None or not 0 < approved_cents < supplement.requested_cents:
                raise SupplementError(
                    f"Approved amount must be more than $0.00 and less than the requested {format_cents(supplement.requested_cents)}."
                )
            supplement.approved_cents = approved_cents
        else:
            supplement.approved_cents = 0
        supplement.decided_at = when
        supplement.next_follow_up_due_at = None
    elif to_status == S.WITHDRAWN and from_status == S.SUBMITTED:
        supplement.next_follow_up_due_at = None

    supplement.status = to_status
    _event(db, supplement, SupplementEventType.STATUS_CHANGE, user_id, now, from_status=from_status, to_status=to_status)
    db.flush()


def log_follow_up(db: Session, supplement: Supplement, method: FollowUpMethod | str | None, note: str | None, user_id: int, now: dt.datetime) -> None:
    if supplement.status != S.SUBMITTED:
        raise SupplementError("Follow-ups can be logged only while the supplement is SUBMITTED.")
    if not method or method not in FollowUpMethod.__members__:
        raise SupplementError("Choose how you followed up.")
    note = (note or "").strip() or None
    if note and len(note) > 500:
        raise SupplementError("Note must be at most 500 characters.")
    supplement.follow_up_count += 1
    supplement.last_follow_up_at = now
    supplement.next_follow_up_due_at = next_due_after(db, supplement, now)
    _event(db, supplement, SupplementEventType.FOLLOW_UP, user_id, now, follow_up_method=FollowUpMethod(method), note=note)
    db.flush()


# ---------------------------------------------------------------- follow-up email (copied, never sent)


def days_open_phrase(days: int) -> str:
    return "1 business day" if days == 1 else f"{days} business days"


def submitted_date_text(value: dt.datetime, tz: str) -> str:
    """Weekday abbreviation, month abbreviation, day without a leading zero: "Mon, Oct 5". Built by hand."""
    local = to_local(value, tz)
    return f"{local.strftime('%a')}, {local.strftime('%b')} {local.day}"


def follow_up_email(db: Session, supplement: Supplement, user: User, now: dt.datetime) -> str:
    ro = db.get(RepairOrder, supplement.repair_order_id)
    shop = db.get(Shop, supplement.shop_id)
    if supplement.submitted_at is None:
        raise SupplementError("Submit the supplement before following up.")
    days = days_open(supplement, now, shop.timezone)
    phrase = days_open_phrase(days)
    adjuster_first_name = supplement.adjuster.full_name.split()[0] if supplement.adjuster and supplement.adjuster.full_name.strip() else "there"
    claim = ro.claim_number or "(no claim number)"
    seq = supplement.sequence_number
    return (
        f"Subject: Supplement S{seq} for claim {claim} (RO {ro.ro_number}) - {phrase}\n"
        f"\n"
        f"Hi {adjuster_first_name},\n"
        f"\n"
        f"Following up on supplement S{seq} for claim {claim}: the {ro.vehicle}, RO {ro.ro_number}. "
        f"We submitted it on {submitted_date_text(supplement.submitted_at, shop.timezone)} for {format_cents(supplement.requested_cents)}, "
        f"and it has been waiting {phrase}.\n"
        f"\n"
        f"Could you let us know its status, or anything else you need from us?\n"
        f"\n"
        f"Thank you,\n"
        f"{user.full_name}\n"
        f"{shop.name}\n"
        f"{format_us_phone(shop.phone_e164)}\n"
    )
