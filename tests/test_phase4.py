"""Phase 4: reports. Now is frozen at 2026-10-20 12:00 local with the default 90-day range."""

import datetime as dt
from types import SimpleNamespace

import pytest
from freezegun import freeze_time

from app.business_days import utcnow
from app.enums import MessageDirection, MessageKind, MessageStatus, PayerType, Stage, SupplementStatus
from app.models import Message, RepairOrder, Supplement
from app.reports import all_reports, default_range, fmt, median
from tests.conftest import login_as
from tests.fixtures import TZ, local

NOW = "2026-10-20 12:00"


def _ro(db, base, number, payer, insurer_id, adjuster_id, checked_in, delivered, invoice):
    ro = RepairOrder(
        shop_id=base.shop_id,
        ro_number=number,
        customer_id=base.maria_id,
        vehicle_year=2020,
        vehicle_make="Ford",
        vehicle_model="Focus",
        payer_type=payer,
        insurer_id=insurer_id,
        adjuster_id=adjuster_id,
        original_estimate_cents=invoice,
        final_invoice_cents=invoice,
        current_stage=Stage.DELIVERED,
        checked_in_at=local(checked_in),
        delivered_at=local(delivered),
    )
    db.add(ro)
    db.flush()
    return ro


def _supplement(db, base, ro, seq, adjuster_id, submitted, decided, status, requested, approved):
    supplement = Supplement(
        shop_id=base.shop_id,
        repair_order_id=ro.id,
        sequence_number=seq,
        status=status,
        description="Hidden damage",
        requested_cents=requested,
        approved_cents=approved,
        adjuster_id=adjuster_id,
        submitted_at=local(submitted),
        decided_at=local(decided) if decided else None,
    )
    db.add(supplement)
    db.flush()
    return supplement


@pytest.fixture
def phase4(db, base):
    r1 = _ro(db, base, "R-1", PayerType.INSURANCE, base.alpha_id, base.dana_id, "2026-10-01 08:00", "2026-10-12 20:00", 500000)
    r2 = _ro(db, base, "R-2", PayerType.CUSTOMER_PAY, None, None, "2026-10-02 09:00", "2026-10-05 09:00", 200000)
    r3 = _ro(db, base, "R-3", PayerType.INSURANCE, base.beta_id, base.sam_id, "2026-10-01 12:00", "2026-10-15 12:00", 300000)
    _supplement(db, base, r1, 1, base.dana_id, "2026-10-05 10:00", "2026-10-07 10:00", SupplementStatus.APPROVED, 100000, 100000)
    _supplement(db, base, r1, 2, base.dana_id, "2026-10-06 10:00", "2026-10-09 10:00", SupplementStatus.PARTIALLY_APPROVED, 50000, 30000)
    _supplement(db, base, r3, 1, base.sam_id, "2026-10-06 10:00", "2026-10-13 10:00", SupplementStatus.DENIED, 80000, 0)
    db.commit()
    return SimpleNamespace(r1=r1, r2=r2, r3=r3)


def reports_now(db, base, date_from=None, date_to=None):
    with freeze_time(local(NOW)):
        now = utcnow()
        default_from, default_to = default_range(now, TZ)
        return all_reports(db, base.shop_id, date_from or default_from, date_to or default_to, now)


def test_t4_1_cycle_time(db, base, phase4):
    overall = reports_now(db, base)["cycle_time"]["overall"]
    assert overall["count"] == 3
    assert (fmt(overall["average"]), fmt(overall["median"]), fmt(overall["max"])) == ("9.5", "11.5", "14.0")


def test_t4_2_alpha_decision_speed(db, base, phase4):
    alpha = next(r for r in reports_now(db, base)["decision_speed"]["by_insurer"] if r["name"] == "Alpha Insurance")
    assert alpha["decided"] == 2
    assert fmt(alpha["average"]) == "2.5"
    assert fmt(alpha["median"]) == "2.5"
    assert fmt(alpha["full_approval_rate"]) == "50.0"
    assert fmt(alpha["dollar_approval_rate"]) == "86.7"


def test_t4_3_beta_decision_speed_is_slowest_first(db, base, phase4):
    rows = reports_now(db, base)["decision_speed"]["by_insurer"]
    beta = rows[0]
    assert beta["name"] == "Beta Insurance"
    assert fmt(beta["average"]) == "5.0"
    assert fmt(beta["full_approval_rate"]) == "0.0"
    assert fmt(beta["dollar_approval_rate"]) == "0.0"


def test_t4_4_by_adjuster(db, base, phase4):
    rows = {r["name"]: r for r in reports_now(db, base)["decision_speed"]["by_adjuster"]}
    assert fmt(rows["Dana Reyes"]["average"]) == "2.5"
    assert fmt(rows["Sam Ortiz"]["average"]) == "5.0"


def test_t4_5_revenue_by_payer(db, base, phase4):
    revenue = reports_now(db, base)["revenue"]
    shares = {r["name"]: fmt(r["share"]) for r in revenue["rows"]}
    assert shares == {"Alpha Insurance": "50.0", "Beta Insurance": "30.0", "Customer pay": "20.0"}
    assert [r["name"] for r in revenue["rows"]] == ["Alpha Insurance", "Beta Insurance", "Customer pay"]
    assert revenue["warnings"] == ["Concentration warning: Alpha Insurance is 50.0% of delivered revenue."]


def test_t4_6_days_waiting(db, base, phase4):
    waiting = reports_now(db, base)["days_waiting"]
    per_ro = {row["ro"].ro_number: fmt(row["days"]) for row in waiting["top"]}
    assert per_ro == {"R-1": "4.0", "R-3": "7.0"}
    assert waiting["count"] == 2
    assert fmt(waiting["total_days"]) == "11.0"
    assert fmt(waiting["average_days"]) == "5.5"


def test_t4_7_open_aging(db, base, phase4):
    ro = db.get(RepairOrder, base.ro1187_id)
    _supplement(db, base, ro, 1, base.dana_id, "2026-10-08 10:00", None, SupplementStatus.SUBMITTED, 60000, 0)
    db.commit()
    aging = reports_now(db, base)["open_aging"]
    counts = {b["bucket"]: b["count"] for b in aging["buckets"]}
    assert counts == {"FRESH": 0, "WATCH": 0, "LATE": 0, "CRITICAL": 1}
    assert aging["total"] == {"count": 1, "cents": 60000}


def test_t4_8_texting(db, base, phase4):
    statuses = [MessageStatus.DELIVERED] * 8 + [MessageStatus.SENT, MessageStatus.FAILED]
    with freeze_time(local("2026-10-19 10:00")):
        for status in statuses:
            db.add(
                Message(
                    shop_id=base.shop_id,
                    repair_order_id=base.ro1187_id,
                    customer_id=base.maria_id,
                    direction=MessageDirection.OUTBOUND,
                    kind=MessageKind.MANUAL,
                    status=status,
                    to_e164="+18185550142",
                    from_e164="+18185550100",
                    body="Test Collision: hello",
                )
            )
        db.commit()
    texting = reports_now(db, base, dt.date(2026, 10, 19), dt.date(2026, 10, 20))["texting"]
    assert texting["attempted"] == 10
    assert texting["delivered"] == 8
    assert fmt(texting["delivery_rate"]) == "80.0"


def test_t4_9_empty_range_shows_no_data(app, db, base, phase4):
    with freeze_time(local(NOW)):
        response = login_as(app, "staff@test.local").get("/reports", params={"date_from": "2026-01-01", "date_to": "2026-01-31"})
    assert response.status_code == 200
    for section in range(1, 7):
        start = response.text.index(f'id="report-{section}"')
        end = response.text.index("</section>", start)
        assert "No data" in response.text[start:end], f"report {section}"


def test_t4_10_median_helper():
    assert median([2, 3]) == 2.5
    assert median([2, 3, 5]) == 3


def test_reports_page_default_range(app, db, base, phase4):
    with freeze_time(local(NOW)):
        html = login_as(app, "admin@test.local").get("/reports").text
    assert 'value="2026-07-23"' in html and 'value="2026-10-20"' in html
    assert "Concentration warning: Alpha Insurance is 50.0% of delivered revenue." in html
    assert "86.7%" in html
