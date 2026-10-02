"""Section 16 item 3: daily email digest of follow-ups due."""

import datetime as dt
from email import message_from_bytes
from email.policy import default as default_policy
from pathlib import Path

import pytest
from freezegun import freeze_time

from app import mailer as mailer_module
from app.digest import build_digest, run_digests
from app.enums import SupplementStatus
from app.mailer import DemoMailer, SmtpMailer, get_mailer
from app.messaging.engine import settings_for
from app.models import RepairOrder, Shop
from app.supplements import create_supplement, transition
from tests.conftest import login_as, make_settings
from tests.fixtures import local


@pytest.fixture
def outbox(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return tmp_path / "outbox"


def _due_supplement(db, base):
    """Submitted Mon 2026-10-05 10:00, so its follow-up is due Wed 2026-10-07 09:00."""
    ro = db.get(RepairOrder, base.ro1187_id)
    supplement = create_supplement(db, ro, "Hidden damage: RF apron", 180000, base.admin_id, local("2026-10-05 09:00"))
    transition(db, supplement, SupplementStatus.SUBMITTED, base.admin_id, local("2026-10-05 10:00"))
    db.commit()
    return supplement


def _turn_on(db, base, recipients=("owner@test.local",), at=dt.time(7, 30)):
    settings = settings_for(db, base.shop_id)
    settings.digest_enabled = True
    settings.digest_recipients = list(recipients)
    settings.digest_send_time = at
    db.commit()
    return settings


def _sent(outbox: Path):
    return [message_from_bytes(p.read_bytes(), policy=default_policy) for p in sorted(outbox.glob("*.eml"))] if outbox.exists() else []


def test_digest_lists_due_follow_ups(db, base, settings, outbox):
    _due_supplement(db, base)
    _turn_on(db, base)
    shop = db.get(Shop, base.shop_id)
    assert build_digest(db, shop, local("2026-10-07 08:00")) is None  # not due yet
    subject, body = build_digest(db, shop, local("2026-10-07 09:00"), "https://shop.example.com")
    assert subject == "Test Collision: 1 supplement follow-up due today"
    assert "RO 24-1187 S1: 2021 Honda Accord, Maria" in body
    assert "Alpha Insurance, claim CLM-55102, adjuster Dana Reyes" in body
    assert "$1,800.00 requested, waiting 2 business days, 0 follow-ups so far (every 2 business days)" in body
    assert "https://shop.example.com/supplements" in body


def test_sent_once_on_business_days_after_send_time(db, base, settings, outbox):
    _due_supplement(db, base)
    _turn_on(db, base, at=dt.time(9, 15))
    with freeze_time(local("2026-10-07 09:10")):
        assert run_digests(local("2026-10-07 09:10"), db, settings) == 0  # before send time
    with freeze_time(local("2026-10-07 09:15")):
        assert run_digests(local("2026-10-07 09:15"), db, settings) == 1
    with freeze_time(local("2026-10-07 15:00")):
        assert run_digests(local("2026-10-07 15:00"), db, settings) == 0  # once per day
    with freeze_time(local("2026-10-10 10:00")):
        assert run_digests(local("2026-10-10 10:00"), db, settings) == 0  # Saturday
    with freeze_time(local("2026-10-12 09:30")):
        assert run_digests(local("2026-10-12 09:30"), db, settings) == 1  # next Monday
    emails = _sent(outbox)
    assert len(emails) == 2
    assert emails[0]["To"] == "owner@test.local"
    assert "Test Collision" in emails[0]["From"]
    assert "RO 24-1187 S1" in emails[0].get_content()


def test_nothing_sent_when_off_or_nothing_due(db, base, settings, outbox):
    _turn_on(db, base)
    with freeze_time(local("2026-10-07 09:00")):
        assert run_digests(local("2026-10-07 09:00"), db, settings) == 0  # nothing due
    _due_supplement(db, base)
    settings_for(db, base.shop_id).digest_enabled = False
    db.commit()
    with freeze_time(local("2026-10-08 09:00")):
        assert run_digests(local("2026-10-08 09:00"), db, settings) == 0
    assert _sent(outbox) == []


def test_smtp_mailer_used_only_when_live_email_is_configured(db_url, monkeypatch):
    calls = []

    class FakeSMTP:
        def __init__(self, host, port, timeout):
            calls.append(("connect", host, port))

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def starttls(self):
            calls.append(("starttls",))

        def login(self, user, password):
            calls.append(("login", user))

        def send_message(self, message):
            calls.append(("send", message["To"], message["Subject"], message["From"]))

    monkeypatch.setattr(mailer_module.smtplib, "SMTP", FakeSMTP)
    assert isinstance(get_mailer(make_settings(db_url)), DemoMailer)
    live = make_settings(db_url, ALLOW_LIVE_EMAIL=True, SMTP_HOST="smtp.example.com", SMTP_USERNAME="u", SMTP_PASSWORD="p", EMAIL_FROM="updates@shop.example")
    mailer = get_mailer(live)
    assert isinstance(mailer, SmtpMailer)
    mailer.send(to=["owner@test.local"], subject="Hi", body="Body", sender_name="Test Collision")
    assert calls[0] == ("connect", "smtp.example.com", 587)
    assert ("starttls",) in calls and ("login", "u") in calls
    assert calls[-1] == ("send", "owner@test.local", "Hi", "Test Collision <updates@shop.example>")


def test_settings_validation(app, db, base):
    def save(**fields):
        form = {
            "csrf_token": client.csrf,
            "tab": "supplements",
            "default_follow_up_interval_business_days": "2",
            "follow_up_due_time": "09:00",
            "concentration_warning_pct": "40",
            "digest_send_time": "07:30",
            **fields,
        }
        return client.post("/settings", data=form, follow_redirects=False)

    with freeze_time(local("2026-10-05 12:00")):
        client = login_as(app, "admin@test.local")
        assert save(digest_enabled="yes").status_code == 422  # no recipients
        assert save(digest_recipients="a@x.com, b@x.com, c@x.com, d@x.com, e@x.com, f@x.com").status_code == 422
        assert save(digest_recipients="not-an-email").status_code == 422
        assert save(digest_recipients="a@x.com", digest_send_time="11:00").status_code == 422
        assert save(digest_enabled="yes", digest_recipients="Owner@Test.local; manager@test.local", digest_send_time="06:45").status_code == 303
    db.expire_all()
    settings = settings_for(db, base.shop_id)
    assert settings.digest_enabled is True
    assert settings.digest_recipients == ["owner@test.local", "manager@test.local"]
    assert settings.digest_send_time == dt.time(6, 45)
