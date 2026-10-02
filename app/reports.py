"""Report formulas (Section 10). Every figure is computed here; templates only format."""

import datetime as dt
from collections import defaultdict
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.business_days import business_days_between, local_datetime_at, to_local
from app.enums import (
    AgingBucket,
    ConsentMethod,
    ConsentPurpose,
    ConsentStatus,
    MessageDirection,
    MessageKind,
    MessageStatus,
    PayerType,
)
from app.models import Adjuster, Consent, Message, RepairOrder, Shop, ShopSettings, Supplement
from app.money import format_decimal
from app.supplements import DECIDED_STATUSES, aging_bucket, days_open

NO_DATA = "No data"
CUSTOMER_PAY_GROUP = "Customer pay"
NO_ADJUSTER_GROUP = "No adjuster"
SECONDS_PER_DAY = Decimal(86400)
DEFAULT_RANGE_DAYS = 90


# ---------------------------------------------------------------- shared helpers


def default_range(now: dt.datetime, tz: str) -> tuple[dt.date, dt.date]:
    """The 90 days ending today (local), today included."""
    today = to_local(now, tz).date()
    return today - dt.timedelta(days=DEFAULT_RANGE_DAYS - 1), today


def range_bounds(date_from: dt.date, date_to: dt.date, tz: str) -> tuple[dt.datetime, dt.datetime]:
    """From 00:00:00 on the "from" date up to the end of the "to" date, in the shop's time zone.

    Returned as [start, end) in UTC, which is the same as up to 23:59:59.999 inclusive.
    """
    start = local_datetime_at(date_from, dt.time(0, 0), tz)
    end = local_datetime_at(date_to + dt.timedelta(days=1), dt.time(0, 0), tz)
    return start, end


def median(values: list) -> Decimal | None:
    """Sort the values. Odd count: the middle one. Even count: the average of the 2 middle ones."""
    if not values:
        return None
    ordered = sorted(Decimal(v) for v in values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2


def average(values: list) -> Decimal | None:
    if not values:
        return None
    return sum((Decimal(v) for v in values), Decimal(0)) / len(values)


def percent(part, whole) -> Decimal | None:
    """part / whole × 100, or None (No data) when whole is 0. Never divides by zero."""
    if not whole:
        return None
    return Decimal(part) * 100 / Decimal(whole)


def fmt(value: Decimal | int | None) -> str:
    """1 decimal place with ROUND_HALF_UP, or "No data"."""
    return NO_DATA if value is None else format_decimal(value, 1)


def stats(values: list) -> dict:
    return {
        "count": len(values),
        "average": average(values),
        "median": median(values),
        "max": max(values) if values else None,
    }


def _payer_group(ro: RepairOrder) -> str:
    if ro.payer_type == PayerType.CUSTOMER_PAY:
        return CUSTOMER_PAY_GROUP
    return ro.insurer.name if ro.insurer else "Insurance"


def _delivered_in_range(db: Session, shop_id: int, start: dt.datetime, end: dt.datetime) -> list[RepairOrder]:
    return list(
        db.scalars(
            select(RepairOrder)
            .where(
                RepairOrder.shop_id == shop_id,
                RepairOrder.delivered_at.is_not(None),
                RepairOrder.delivered_at >= start,
                RepairOrder.delivered_at < end,
            )
            .order_by(RepairOrder.delivered_at, RepairOrder.id)
        ).all()
    )


# ---------------------------------------------------------------- report 1: cycle time


def cycle_days(ro: RepairOrder) -> Decimal:
    return Decimal(int((ro.delivered_at - ro.checked_in_at).total_seconds())) / SECONDS_PER_DAY


def report_cycle_time(db: Session, shop_id: int, start: dt.datetime, end: dt.datetime) -> dict:
    ros = _delivered_in_range(db, shop_id, start, end)
    groups: dict[str, list[Decimal]] = defaultdict(list)
    for ro in ros:
        groups[_payer_group(ro)].append(cycle_days(ro))
    return {
        "overall": stats([cycle_days(ro) for ro in ros]),
        "groups": [{"name": name, **stats(values)} for name, values in sorted(groups.items())],
    }


# ---------------------------------------------------------------- report 2: supplement decision speed


def _speed_rows(groups: dict[str, list[Supplement]], tz: str) -> list[dict]:
    rows = []
    for name, supplements in groups.items():
        days = [business_days_between(s.submitted_at, s.decided_at, tz) for s in supplements]
        approved_count = sum(1 for s in supplements if s.status.value == "APPROVED")
        rows.append(
            {
                "name": name,
                "decided": len(supplements),
                "average": average(days),
                "median": median(days),
                "full_approval_rate": percent(approved_count, len(supplements)),
                "dollar_approval_rate": percent(sum(s.approved_cents for s in supplements), sum(s.requested_cents for s in supplements)),
            }
        )
    rows.sort(key=lambda r: (r["average"] if r["average"] is not None else Decimal(-1), r["name"]), reverse=True)
    return rows


def report_decision_speed(db: Session, shop_id: int, start: dt.datetime, end: dt.datetime, tz: str) -> dict:
    supplements = db.scalars(
        select(Supplement).where(
            Supplement.shop_id == shop_id,
            Supplement.status.in_(DECIDED_STATUSES),
            Supplement.decided_at.is_not(None),
            Supplement.decided_at >= start,
            Supplement.decided_at < end,
        )
    ).all()
    by_insurer: dict[str, list[Supplement]] = defaultdict(list)
    by_adjuster: dict[str, list[Supplement]] = defaultdict(list)
    for supplement in supplements:
        ro = supplement.repair_order
        by_insurer[ro.insurer.name if ro.insurer else "Insurance"].append(supplement)
        adjuster = db.get(Adjuster, supplement.adjuster_id) if supplement.adjuster_id else None
        by_adjuster[adjuster.full_name if adjuster else NO_ADJUSTER_GROUP].append(supplement)
    return {"count": len(supplements), "by_insurer": _speed_rows(by_insurer, tz), "by_adjuster": _speed_rows(by_adjuster, tz)}


# ---------------------------------------------------------------- report 3: open supplement aging


def report_open_aging(db: Session, shop_id: int, now: dt.datetime, tz: str) -> dict:
    """All SUBMITTED supplements now; ignores the date range."""
    buckets = {bucket: {"count": 0, "cents": 0} for bucket in AgingBucket}
    supplements = db.scalars(select(Supplement).where(Supplement.shop_id == shop_id, Supplement.status == "SUBMITTED")).all()
    for supplement in supplements:
        bucket = aging_bucket(days_open(supplement, now, tz))
        buckets[bucket]["count"] += 1
        buckets[bucket]["cents"] += supplement.requested_cents
    return {
        "count": len(supplements),
        "buckets": [{"bucket": b.value, **values} for b, values in buckets.items()],
        "total": {"count": len(supplements), "cents": sum(s.requested_cents for s in supplements)},
    }


# ---------------------------------------------------------------- report 4: revenue by payer


def report_revenue_by_payer(db: Session, shop_id: int, start: dt.datetime, end: dt.datetime, warning_pct: int) -> dict:
    ros = _delivered_in_range(db, shop_id, start, end)
    revenue: dict[str, int] = defaultdict(int)
    is_insurer: dict[str, bool] = {}
    for ro in ros:
        name = _payer_group(ro)
        revenue[name] += ro.final_invoice_cents or 0
        is_insurer[name] = ro.payer_type == PayerType.INSURANCE
    total = sum(revenue.values())
    rows = [{"name": name, "cents": cents, "share": percent(cents, total)} for name, cents in revenue.items()]
    rows.sort(key=lambda r: (-r["cents"], r["name"]))
    warnings = [
        f"Concentration warning: {row['name']} is {fmt(row['share'])}% of delivered revenue."
        for row in rows
        if is_insurer[row["name"]] and row["share"] is not None and row["share"] > warning_pct
    ]
    return {"count": len(ros), "rows": rows, "total_cents": total, "warnings": warnings}


# ---------------------------------------------------------------- report 5: days waiting on supplements


def merge_intervals(intervals: list[tuple[dt.datetime, dt.datetime]]) -> list[tuple[dt.datetime, dt.datetime]]:
    """Sort by start; extend the last merged interval when the next starts at or before its end."""
    merged: list[list[dt.datetime]] = []
    for start, end in sorted(intervals):
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [(start, end) for start, end in merged]


def waiting_days(intervals: list[tuple[dt.datetime, dt.datetime]]) -> Decimal:
    seconds = sum(int((end - start).total_seconds()) for start, end in merge_intervals(intervals))
    return Decimal(seconds) / SECONDS_PER_DAY


def report_days_waiting(db: Session, shop_id: int, start: dt.datetime, end: dt.datetime) -> dict:
    ros = [ro for ro in _delivered_in_range(db, shop_id, start, end) if ro.payer_type == PayerType.INSURANCE]
    per_ro = []
    for ro in ros:
        intervals = [
            (s.submitted_at, s.decided_at)
            for s in db.scalars(select(Supplement).where(Supplement.repair_order_id == ro.id, Supplement.status.in_(DECIDED_STATUSES))).all()
            if s.submitted_at is not None and s.decided_at is not None
        ]
        per_ro.append({"ro": ro, "days": waiting_days(intervals)})
    total = sum((row["days"] for row in per_ro), Decimal(0))
    top = sorted(per_ro, key=lambda r: (-r["days"], r["ro"].ro_number))[:5]
    return {
        "count": len(ros),
        "total_days": total if ros else None,
        "average_days": total / len(ros) if ros else None,
        "top": top,
    }


# ---------------------------------------------------------------- report 6: texting


def report_texting(db: Session, shop_id: int, start: dt.datetime, end: dt.datetime) -> dict:
    messages = db.scalars(
        select(Message).where(Message.shop_id == shop_id, Message.created_at >= start, Message.created_at < end)
    ).all()
    outbound = [m for m in messages if m.direction == MessageDirection.OUTBOUND]
    attempted = [m for m in outbound if m.status in (MessageStatus.SENT, MessageStatus.DELIVERED, MessageStatus.FAILED)]
    delivered = sum(1 for m in outbound if m.status == MessageStatus.DELIVERED)
    opt_outs = db.scalars(
        select(Consent.id).where(
            Consent.shop_id == shop_id,
            Consent.status == ConsentStatus.OPTED_OUT,
            Consent.method == ConsentMethod.KEYWORD,
            Consent.purpose == ConsentPurpose.REPAIR_UPDATES,
            Consent.recorded_at >= start,
            Consent.recorded_at < end,
        )
    ).all()
    return {
        "has_data": bool(messages) or bool(opt_outs),
        "attempted": len(attempted),
        "delivered": delivered,
        "delivery_rate": percent(delivered, len(attempted)),
        "failed": sum(1 for m in outbound if m.status == MessageStatus.FAILED),
        "blocked_no_consent": sum(1 for m in outbound if m.status == MessageStatus.BLOCKED_NO_CONSENT),
        "blocked_opted_out": sum(1 for m in outbound if m.status == MessageStatus.BLOCKED_OPTED_OUT),
        "opt_outs_by_keyword": len(opt_outs),
        "inbound_replies": sum(1 for m in messages if m.kind == MessageKind.INBOUND_REPLY),
    }


# ---------------------------------------------------------------- all six


def all_reports(db: Session, shop_id: int, date_from: dt.date, date_to: dt.date, now: dt.datetime) -> dict:
    shop = db.get(Shop, shop_id)
    settings = db.scalar(select(ShopSettings).where(ShopSettings.shop_id == shop_id))
    tz = shop.timezone
    start, end = range_bounds(date_from, date_to, tz)
    return {
        "cycle_time": report_cycle_time(db, shop_id, start, end),
        "decision_speed": report_decision_speed(db, shop_id, start, end, tz),
        "open_aging": report_open_aging(db, shop_id, now, tz),
        "revenue": report_revenue_by_payer(db, shop_id, start, end, settings.concentration_warning_pct),
        "days_waiting": report_days_waiting(db, shop_id, start, end),
        "texting": report_texting(db, shop_id, start, end),
    }
