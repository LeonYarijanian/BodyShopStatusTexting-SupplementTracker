"""Phase 7: demo seed and pitch walkthrough."""

import datetime as dt
import html
import os
import re
import subprocess
import sys
from decimal import Decimal

import pytest
from freezegun import freeze_time
from sqlalchemy import func, select

from app import seed
from app.business_days import business_days_between
from app.enums import ACTIVE_STAGES, ConsentStatus, MessageStatus, PayerType, Stage, SupplementStatus
from app.messaging.engine import consent_statuses
from app.models import Adjuster, Consent, Customer, Insurer, Message, RepairOrder, Shop, Supplement, User
from app.supplements import days_open, follow_ups_due_count
from tests.conftest import ROOT, login_as
from tests.fixtures import local

TZ = "America/Los_Angeles"
SEED_TIMES = ["2026-10-06 14:00", "2026-10-10 11:00", "2026-10-12 07:30", "2026-11-02 21:30"]


def run_seed(monkeypatch, db_url, when: str) -> int:
    monkeypatch.setenv("DATABASE_URL", db_url)
    with freeze_time(local(when)):
        return seed.main(["--demo"])


def counts(db) -> dict:
    return {model.__tablename__: db.scalar(select(func.count(model.id))) for model in (Shop, User, Insurer, Adjuster, RepairOrder, Supplement, Customer, Consent, Message)}


@pytest.mark.parametrize("when", SEED_TIMES)
def test_t7_1_seed_on_fresh_database(monkeypatch, db_url, db, when):
    assert run_seed(monkeypatch, db_url, when) == 0
    now = local(when)
    c = counts(db)
    assert (c["shops"], c["users"], c["insurers"], c["adjusters"], c["repair_orders"], c["supplements"]) == (1, 3, 5, 10, 45, 18)

    shop = db.scalar(select(Shop))
    assert (shop.name, shop.phone_e164, shop.timezone) == ("Brand Blvd Collision (Demo)", "+18185550100", TZ)
    submitted = db.scalars(select(Supplement).where(Supplement.status == SupplementStatus.SUBMITTED)).all()
    assert sorted(days_open(s, now, TZ) for s in submitted) == [0, 1, 2, 3, 4, 6, 8, 11]
    assert all(s.submitted_at <= now and s.submitted_at >= s.repair_order.checked_in_at for s in submitted)
    assert follow_ups_due_count(db, shop.id, now) == 3

    statuses = sorted(s.status.value for s in db.scalars(select(Supplement)).all())
    assert statuses.count("APPROVED") == 6 and statuses.count("PARTIALLY_APPROVED") == 2
    assert statuses.count("DENIED") == 1 and statuses.count("DRAFT") == 1

    ros = db.scalars(select(RepairOrder)).all()
    active = [ro for ro in ros if ro.current_stage in ACTIVE_STAGES]
    delivered = [ro for ro in ros if ro.current_stage == Stage.DELIVERED]
    assert len(active) == 30 and len(delivered) == 15
    assert {ro.current_stage for ro in active} == set(ACTIVE_STAGES)
    assert sum(1 for ro in active if ro.current_stage == Stage.PAINT) >= 4
    assert all(now - dt.timedelta(days=60) <= ro.delivered_at <= now for ro in delivered)
    assert sum(1 for ro in ros if ro.payer_type == PayerType.CUSTOMER_PAY) == 11
    assert all(ro.checked_in_at <= now for ro in ros)

    current = consent_statuses(db, shop.id)
    assert list(current.values()).count(ConsentStatus.OPTED_IN) == 40
    assert list(current.values()).count(ConsentStatus.OPTED_OUT) == 1
    assert c["customers"] - len(current) == 4
    phones = sorted(customer.phone_e164 for customer in db.scalars(select(Customer)).all())
    assert phones[0] >= "+18185550101" and phones[-1] <= "+18185550199"

    scheduled = db.scalars(select(Message).where(Message.status == MessageStatus.SCHEDULED)).all()
    assert len(scheduled) == 1
    assert scheduled[0].scheduled_send_at == now + dt.timedelta(minutes=5)
    replies = {ro.ro_number: ro for ro in ros if ro.needs_reply}
    assert len(replies) == 2
    inbound = sorted(m.body for m in db.scalars(select(Message).where(Message.kind == "INBOUND_REPLY")).all())
    assert inbound == ["Can I pick it up Saturday?", "When will it be ready?"]

    def average_decision(insurer_name):
        days = [
            business_days_between(s.submitted_at, s.decided_at, TZ)
            for s in db.scalars(select(Supplement).where(Supplement.decided_at.is_not(None))).all()
            if s.repair_order.insurer.name == insurer_name
        ]
        return Decimal(sum(days)) / len(days)

    assert 4 <= average_decision("Harbor Mutual (Demo)") <= 5
    assert 1 <= average_decision("Northline Insurance (Demo)") <= 2


def test_t7_2_second_run_changes_nothing(monkeypatch, db_url, db, capsys):
    assert run_seed(monkeypatch, db_url, SEED_TIMES[0]) == 0
    before = counts(db)
    capsys.readouterr()
    assert run_seed(monkeypatch, db_url, SEED_TIMES[0]) == 1
    assert "Demo shop already exists." in capsys.readouterr().out
    assert counts(db) == before


def test_t7_1_and_t7_2_cli_exit_codes(db_url, tmp_path):
    env = {**os.environ, "DATABASE_URL": db_url, "PYTHONPATH": str(ROOT)}
    first = subprocess.run([sys.executable, "-m", "app.seed", "--demo"], cwd=tmp_path, env=env, capture_output=True, text=True, timeout=120)
    assert first.returncode == 0, first.stderr
    assert "demo-password-123" in first.stdout
    second = subprocess.run([sys.executable, "-m", "app.seed", "--demo"], cwd=tmp_path, env=env, capture_output=True, text=True, timeout=120)
    assert second.returncode == 1
    assert second.stdout.strip() == "Demo shop already exists."


def test_t7_3_reports_concentration_warning(monkeypatch, db_url, app):
    assert run_seed(monkeypatch, db_url, SEED_TIMES[0]) == 0
    with freeze_time(local(SEED_TIMES[0])):
        page = login_as(app, "admin@demo.local", "demo-password-123").get("/reports").text
    assert re.search(r"Concentration warning: Northline Insurance \(Demo\) is \d+\.\d% of delivered revenue\.", html.unescape(page))


@pytest.mark.parametrize("when", ["2026-10-06 14:00", "2026-10-12 07:30"])
def test_pitch_walkthrough(monkeypatch, db_url, app, db, when):
    """The Section 14 pitch demo, steps 1 to 6, as one automated run (the manual check covers Wi-Fi off and timing)."""
    assert run_seed(monkeypatch, db_url, when) == 0
    with freeze_time(local(when)) as frozen:
        client = login_as(app, "admin@demo.local", "demo-password-123")

        # 1. Board: follow-ups due and dollars waiting.
        board = client.get("/").text
        assert "Follow-ups due: 3" in board
        waiting = re.search(r"Waiting: \$([\d,]+\.\d\d)", board).group(1)
        assert waiting != "0.00"

        # 2. Supplements page: the top row is CRITICAL at 11 business days.
        page = client.get("/supplements").text
        first_row = page.split("<tbody>")[1].split("</tr>")[0]
        assert 'class="bucket-CRITICAL">11<' in first_row
        supplement_id = int(re.search(r'data-supplement-id="(\d+)"', first_row).group(1))
        assert 'class="due"' in page.split("<tbody>")[1].split("<td")[0]

        # 3. Copy follow-up email, then log a phone follow-up: the row loses its Due tag.
        email = client.get(f"/supplements/{supplement_id}/email")
        assert email.status_code == 200 and email.text.startswith("Subject: Supplement S1 for claim")
        assert "waiting 11 business days" in email.text
        logged = client.post(
            f"/supplements/{supplement_id}/follow-up",
            data={"csrf_token": client.csrf, "method": "PHONE", "next": "/supplements"},
            follow_redirects=True,
        )
        row = logged.text.split(f'data-supplement-id="{supplement_id}"')[0].rsplit("<tr", 1)[1]
        assert 'class="due"' not in row
        assert "Follow-ups due: 2" in logged.text

        # 4. Move an RO from PARTS_RECEIVED to BODY_REPAIR; the thread shows Scheduled for HH:MM; send now.
        ro = db.scalars(select(RepairOrder).where(RepairOrder.current_stage == Stage.PARTS_RECEIVED).order_by(RepairOrder.id)).first()
        client.post(f"/ro/{ro.id}/stage", data={"csrf_token": client.csrf, "stage": "BODY_REPAIR", "source": "ro"})
        thread = client.get(f"/ro/{ro.id}").text
        assert re.search(r"Scheduled for \d\d:\d\d", thread)
        client.post("/demo/send-now", data={"csrf_token": client.csrf}, headers={"referer": "https://testserver/"})
        db.expire_all()
        text = db.scalar(select(Message).where(Message.repair_order_id == ro.id, Message.stage == Stage.BODY_REPAIR))
        assert text.status == MessageStatus.DELIVERED
        assert db.scalar(select(func.count(Message.id)).where(Message.status == MessageStatus.SCHEDULED)) == 0

        # 5. Simulate a customer reply: Needs reply tag, and the top bar count goes up by 1.
        before = int(re.search(r"Needs reply: (\d+)", client.get("/").text).group(1))
        frozen.tick(dt.timedelta(minutes=1))
        client.post(f"/ro/{ro.id}/simulate-reply", data={"csrf_token": client.csrf, "body": "Thanks! What time can I pick it up?"})
        board = client.get("/").text
        assert f"Needs reply: {before + 1}" in board
        card = re.search(rf'data-ro-id="{ro.id}".*?</article>', board, re.S).group(0)
        assert "Needs reply" in card

        # 6. Reports: Harbor slower than Northline, the concentration warning, total days waiting.
        reports = html.unescape(client.get("/reports").text)
    speed = reports.split('id="report-2"')[1].split("By adjuster")[0]
    assert speed.index("Harbor Mutual (Demo)") < speed.index("Northline Insurance (Demo)")
    assert "Concentration warning: Northline Insurance (Demo)" in reports
    assert re.search(r"Total waiting days: <strong>\d+\.\d</strong>", reports)
