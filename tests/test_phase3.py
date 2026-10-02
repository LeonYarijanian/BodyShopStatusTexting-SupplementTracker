"""Phase 3: supplement tracker. All supplements are on RO 24-1187 (Alpha, default interval 2) unless stated."""

import datetime as dt

import pytest
from freezegun import freeze_time
from sqlalchemy import select

from app.business_days import add_business_days, business_days_between
from app.enums import AgingBucket, FollowUpMethod, SupplementEventType, SupplementStatus
from app.models import Insurer, RepairOrder, Supplement, SupplementEvent, User
from app.supplements import (
    SupplementError,
    aging_bucket,
    create_supplement,
    days_open,
    dollars_waiting,
    follow_up_email,
    is_follow_up_due,
    log_follow_up,
    transition,
)
from tests.conftest import login_as
from tests.fixtures import TZ, local


def new_supplement(db, base, requested=180000, ro_id=None, now=None):
    ro = db.get(RepairOrder, ro_id or base.ro1187_id)
    now = now or local("2026-10-05 09:30")
    with freeze_time(now):
        supplement = create_supplement(db, ro, "Hidden damage: RF apron, headlamp bracket", requested, base.admin_id, now)
        db.commit()
    return supplement


def submit(db, base, supplement, when):
    with freeze_time(when):
        transition(db, supplement, SupplementStatus.SUBMITTED, base.admin_id, when)
        db.commit()


def test_t3_1_customer_pay_ro_rejected(app, db, base):
    ro = db.get(RepairOrder, base.ro1188_id)
    with pytest.raises(SupplementError, match=r"^Supplements need an insurance RO\.$"):
        create_supplement(db, ro, "Hidden damage", 50000, base.admin_id, local("2026-10-05 10:00"))
    db.rollback()

    with freeze_time(local("2026-10-05 10:00")):
        client = login_as(app, "staff@test.local")
        response = client.post(f"/ro/{base.ro1188_id}/supplements", data={"csrf_token": client.csrf, "description": "x", "requested": "500"})
        assert "Supplements need an insurance RO." in response.text
    assert db.scalar(select(Supplement.id)) is None


def test_t3_2_sequence_numbers(db, base):
    for _ in range(3):
        new_supplement(db, base)
    numbers = db.scalars(select(Supplement.sequence_number).where(Supplement.repair_order_id == base.ro1187_id).order_by(Supplement.id)).all()
    assert numbers == [1, 2, 3]
    first = db.scalars(select(Supplement)).first()
    assert first.status == SupplementStatus.DRAFT
    assert first.adjuster_id == base.dana_id


def test_t3_3_disallowed_transition(db, base):
    supplement = new_supplement(db, base)
    with pytest.raises(SupplementError, match=r"^Not allowed: DRAFT -> APPROVED$"):
        transition(db, supplement, SupplementStatus.APPROVED, base.admin_id, local("2026-10-05 10:00"))


def test_t3_4_business_days_between():
    rows = [
        ("2026-10-05 10:00", "2026-10-08 09:00"),
        ("2026-10-09 16:00", "2026-10-12 08:00"),
        ("2026-10-05 10:00", "2026-10-05 17:00"),
        ("2026-10-10 11:00", "2026-10-12 09:00"),
    ]
    assert [business_days_between(local(a), local(b), TZ) for a, b in rows] == [3, 1, 0, 1]


def test_t3_5_add_business_days():
    assert add_business_days(dt.date(2026, 10, 5), 2) == dt.date(2026, 10, 7)
    assert add_business_days(dt.date(2026, 10, 9), 2) == dt.date(2026, 10, 13)
    assert add_business_days(dt.date(2026, 10, 8), 1) == dt.date(2026, 10, 9)
    assert add_business_days(dt.date(2026, 10, 10), 1) == dt.date(2026, 10, 12)


def test_t3_6_next_follow_up_due(db, base):
    supplement = new_supplement(db, base)
    submit(db, base, supplement, local("2026-10-05 10:00"))
    assert supplement.next_follow_up_due_at.isoformat() == "2026-10-07T16:00:00+00:00"

    db.get(Insurer, base.alpha_id).follow_up_interval_business_days = 3
    db.commit()
    other = new_supplement(db, base)
    submit(db, base, other, local("2026-10-05 10:00"))
    assert other.next_follow_up_due_at.isoformat() == "2026-10-08T16:00:00+00:00"


def test_t3_7_follow_up_due_and_top_bar(app, db, base):
    supplement = new_supplement(db, base)
    submit(db, base, supplement, local("2026-10-05 10:00"))
    assert not is_follow_up_due(supplement, local("2026-10-07 08:59"))
    assert is_follow_up_due(supplement, local("2026-10-07 09:00"))
    with freeze_time(local("2026-10-07 08:59")):
        assert "Follow-ups due: 0" in login_as(app, "staff@test.local").get("/").text
    with freeze_time(local("2026-10-07 09:00")):
        assert "Follow-ups due: 1" in login_as(app, "staff@test.local").get("/").text


def test_t3_8_log_follow_up(app, db, base):
    supplement = new_supplement(db, base)
    submit(db, base, supplement, local("2026-10-05 10:00"))
    with freeze_time(local("2026-10-07 11:00")):
        client = login_as(app, "staff@test.local")
        response = client.post(
            f"/supplements/{supplement.id}/follow-up",
            data={"csrf_token": client.csrf, "method": "PHONE", "note": "Left voicemail", "next": "/supplements"},
            follow_redirects=False,
        )
        assert response.status_code == 303
    db.refresh(supplement)
    assert supplement.follow_up_count == 1
    assert supplement.last_follow_up_at == local("2026-10-07 11:00")
    assert supplement.next_follow_up_due_at.isoformat() == "2026-10-09T16:00:00+00:00"
    assert not is_follow_up_due(supplement, local("2026-10-07 11:00"))
    event = db.scalar(select(SupplementEvent).where(SupplementEvent.event_type == SupplementEventType.FOLLOW_UP))
    assert event.follow_up_method == FollowUpMethod.PHONE and event.note == "Left voicemail"


def test_t3_9_partial_approval_amounts(db, base):
    supplements = [new_supplement(db, base, requested=180000) for _ in range(3)]
    for supplement in supplements:
        submit(db, base, supplement, local("2026-10-05 10:00"))
    now = local("2026-10-06 10:00")
    transition(db, supplements[0], SupplementStatus.PARTIALLY_APPROVED, base.admin_id, now, approved_cents=120000)
    db.commit()
    assert supplements[0].approved_cents == 120000
    for supplement, amount in ((supplements[1], 180000), (supplements[2], 0)):
        with pytest.raises(SupplementError, match="Approved amount must be more than"):
            transition(db, supplement, SupplementStatus.PARTIALLY_APPROVED, base.admin_id, now, approved_cents=amount)
        db.rollback()
        db.refresh(supplement)
        assert supplement.status == SupplementStatus.SUBMITTED


def test_t3_10_approve(app, db, base):
    supplement = new_supplement(db, base, requested=180000)
    submit(db, base, supplement, local("2026-10-05 10:00"))
    with freeze_time(local("2026-10-08 15:00")):
        client = login_as(app, "staff@test.local")
        client.post(
            f"/supplements/{supplement.id}/transition",
            data={"csrf_token": client.csrf, "to_status": "APPROVED", "at": "2026-10-08T14:00"},
            follow_redirects=False,
        )
    db.refresh(supplement)
    assert supplement.status == SupplementStatus.APPROVED
    assert supplement.approved_cents == supplement.requested_cents
    assert supplement.decided_at.isoformat() == "2026-10-08T21:00:00+00:00"
    assert supplement.next_follow_up_due_at is None
    assert days_open(supplement, local("2026-10-20 12:00"), TZ) == 3


def test_t3_11_aging_buckets():
    days = [0, 1, 2, 3, 4, 6, 7, 11]
    expected = ["FRESH", "FRESH", "WATCH", "WATCH", "LATE", "LATE", "CRITICAL", "CRITICAL"]
    assert [aging_bucket(d) for d in days] == [AgingBucket(e) for e in expected]


def test_t3_12_dollars_waiting(app, db, base):
    for amount in (180000, 95050, 42000):
        submit(db, base, new_supplement(db, base, requested=amount), local("2026-10-05 10:00"))
    approved = new_supplement(db, base, requested=50000)
    submit(db, base, approved, local("2026-10-05 10:00"))
    with freeze_time(local("2026-10-06 10:00")):
        transition(db, approved, SupplementStatus.APPROVED, base.admin_id, local("2026-10-06 10:00"))
        db.commit()
        assert dollars_waiting(db, base.shop_id) == 317050
        html = login_as(app, "staff@test.local").get("/supplements").text
    assert "Waiting: $3,170.50" in html


def test_t3_13_follow_up_email(app, db, base):
    supplement = new_supplement(db, base, requested=180000)
    submit(db, base, supplement, local("2026-10-05 10:00"))
    admin = db.get(User, base.admin_id)
    text = follow_up_email(db, supplement, admin, local("2026-10-09 12:00"))
    assert text.splitlines()[0] == "Subject: Supplement S1 for claim CLM-55102 (RO 24-1187) - 4 business days"
    assert "Hi Dana," in text
    assert "We submitted it on Mon, Oct 5 for $1,800.00, and it has been waiting 4 business days." in text
    assert text.rstrip().endswith("Test Admin\nTest Collision\n(818) 555-0100")

    with freeze_time(local("2026-10-09 12:00")):
        client = login_as(app, "admin@test.local")
        assert client.get(f"/supplements/{supplement.id}/email").text == text


def test_t3_14_submitted_time_limits(db, base):
    supplement = new_supplement(db, base)
    now = local("2026-10-05 10:00")
    with pytest.raises(SupplementError, match="before the RO's check-in"):
        transition(db, supplement, SupplementStatus.SUBMITTED, base.admin_id, now, at=local("2026-10-01 08:00"))
    db.rollback()
    with pytest.raises(SupplementError, match="in the future"):
        transition(db, supplement, SupplementStatus.SUBMITTED, base.admin_id, now, at=local("2026-10-05 11:00"))
    db.rollback()
    db.refresh(supplement)
    assert supplement.status == SupplementStatus.DRAFT
    assert supplement.submitted_at is None


def test_supplements_page_and_ro_panel_render(app, db, base):
    """Smoke test: open tab sorts by days open and marks due rows; RO page shows the panel."""
    old = new_supplement(db, base, requested=1000)
    submit(db, base, old, local("2026-10-01 10:00"))
    fresh = new_supplement(db, base, requested=2000)
    submit(db, base, fresh, local("2026-10-05 10:00"))
    with freeze_time(local("2026-10-05 12:00")):
        client = login_as(app, "staff@test.local")
        html = client.get("/supplements").text
        assert html.index(f'data-supplement-id="{old.id}"') < html.index(f'data-supplement-id="{fresh.id}"')
        assert 'class="due"' in html
        ro_page = client.get(f"/ro/{base.ro1187_id}").text
        assert "Copy follow-up email" in ro_page and "Partially approve" in ro_page
        assert "Supplement: 2d" in client.get("/").text
