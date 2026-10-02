"""Phase 2: messaging engine, DEMO provider.

All times are local on Monday 2026-10-05 unless stated, using RO 24-1187.
"""

from freezegun import freeze_time
from sqlalchemy import func, select

from app.enums import ConsentStatus, MessageDirection, MessageKind, MessageStatus, Stage
from app.messaging.engine import current_consent, render_stage_text, run_sender, send_manual_text, settings_for
from app.messaging.inbound import handle_inbound
from app.messaging.providers import DemoProvider
from app.models import Consent, Customer, Message, RepairOrder, Shop, User
from app.routes.repair_orders import change_stage
from tests.conftest import login_as
from tests.fixtures import local


def at(hhmm: str, day: str = "2026-10-05"):
    return local(f"{day} {hhmm}")


def move(db, base, stage: str, when, ro_id=None):
    ro = db.get(RepairOrder, ro_id or base.ro1187_id)
    admin = db.get(User, base.admin_id)
    with freeze_time(when):
        change_stage(db, ro, Stage(stage), admin, when)
        db.commit()


def send(db, settings, when):
    with freeze_time(when):
        run_sender(when, db, settings)


def stage_messages(db, ro_id, stage=None):
    query = select(Message).where(Message.repair_order_id == ro_id, Message.kind == MessageKind.STAGE_UPDATE)
    if stage:
        query = query.where(Message.stage == Stage(stage))
    return db.scalars(query.order_by(Message.id)).all()


def new_stage_messages(db, base):
    """Stage messages other than the base fixture's delivered check-in text."""
    return [m for m in stage_messages(db, base.ro1187_id) if m.id != base.checkin_message_id]


def inbound(db, base, body: str, when, phone="+18185550142", settings=None):
    shop = db.get(Shop, base.shop_id)
    with freeze_time(when):
        handle_inbound(db, shop, phone, shop.phone_e164, body, when, settings)
        db.commit()


def test_t2_1_cool_off(db, base, settings):
    move(db, base, "PARTS_ORDERED", at("13:00"))
    messages = new_stage_messages(db, base)
    assert len(messages) == 1
    message = messages[0]
    assert message.scheduled_send_at.isoformat() == "2026-10-05T20:10:00+00:00"

    send(db, settings, at("13:09"))
    db.refresh(message)
    assert message.status == MessageStatus.SCHEDULED

    send(db, settings, at("13:10"))
    db.refresh(message)
    assert message.status == MessageStatus.DELIVERED
    assert message.sent_at.isoformat() == "2026-10-05T20:10:00+00:00"


def test_t2_2_newer_stage_supersedes(db, base, settings):
    move(db, base, "PARTS_ORDERED", at("13:00"))
    move(db, base, "BODY_REPAIR", at("13:04"))
    send(db, settings, at("13:14"))
    parts = stage_messages(db, base.ro1187_id, "PARTS_ORDERED")
    body = stage_messages(db, base.ro1187_id, "BODY_REPAIR")
    assert [m.status for m in parts] == [MessageStatus.CANCELLED_SUPERSEDED]
    assert len(body) == 1
    assert body[0].status == MessageStatus.DELIVERED


def test_t2_3_quiet_hours_evening(db, base, settings):
    move(db, base, "PARTS_ORDERED", at("19:55"))
    send(db, settings, at("20:05"))
    message = stage_messages(db, base.ro1187_id, "PARTS_ORDERED")[0]
    assert message.status == MessageStatus.SCHEDULED
    assert message.scheduled_send_at.isoformat() == "2026-10-06T15:00:00+00:00"
    send(db, settings, at("08:00", "2026-10-06"))
    db.refresh(message)
    assert message.status == MessageStatus.DELIVERED


def test_t2_4_quiet_hours_morning(db, base, settings):
    move(db, base, "PARTS_ORDERED", at("07:30"))
    send(db, settings, at("07:40"))
    message = stage_messages(db, base.ro1187_id, "PARTS_ORDERED")[0]
    assert message.status == MessageStatus.SCHEDULED
    assert message.scheduled_send_at.isoformat() == "2026-10-05T15:00:00+00:00"
    send(db, settings, at("08:00"))
    db.refresh(message)
    assert message.status == MessageStatus.DELIVERED


def test_t2_5_no_consent_blocks_without_sending(db, base, settings, monkeypatch):
    calls = []
    original = DemoProvider.send

    def spy(self, to, body, from_=None):
        calls.append(to)
        return original(self, to, body, from_)

    monkeypatch.setattr(DemoProvider, "send", spy)
    move(db, base, "PAINT", at("13:00"), ro_id=base.ro1188_id)
    send(db, settings, at("13:10"))
    message = stage_messages(db, base.ro1188_id, "PAINT")[0]
    assert message.status == MessageStatus.BLOCKED_NO_CONSENT
    assert calls == []


def _opt_out_by_simulated_reply(app, db, base):
    move(db, base, "PARTS_ORDERED", at("12:00"))
    with freeze_time(at("12:01")):
        client = login_as(app, "staff@test.local")
        response = client.post(
            f"/ro/{base.ro1187_id}/simulate-reply", data={"csrf_token": client.csrf, "body": " stop "}, follow_redirects=False
        )
        assert response.status_code == 303


def test_t2_6_stop_keyword(app, db, base):
    _opt_out_by_simulated_reply(app, db, base)
    db.expire_all()
    assert current_consent(db, base.shop_id, "+18185550142").status == ConsentStatus.OPTED_OUT
    assert stage_messages(db, base.ro1187_id, "PARTS_ORDERED")[0].status == MessageStatus.BLOCKED_OPTED_OUT
    replies = db.scalars(select(Message).where(Message.kind == MessageKind.OPT_OUT_CONFIRMATION)).all()
    assert len(replies) == 1
    assert replies[0].status == MessageStatus.DELIVERED
    keyword = db.scalars(select(Message).where(Message.kind == MessageKind.INBOUND_KEYWORD)).all()
    assert len(keyword) == 1 and keyword[0].status == MessageStatus.RECEIVED


def test_t2_7_start_keyword(app, db, base, settings):
    _opt_out_by_simulated_reply(app, db, base)
    inbound(db, base, "Start", at("12:30"), settings=settings)
    db.expire_all()
    assert current_consent(db, base.shop_id, "+18185550142").status == ConsentStatus.OPTED_IN
    assert len(db.scalars(select(Message).where(Message.kind == MessageKind.OPT_IN_CONFIRMATION)).all()) == 1
    move(db, base, "PAINT", at("13:00"))
    send(db, settings, at("13:10"))
    assert stage_messages(db, base.ro1187_id, "PAINT")[0].status == MessageStatus.DELIVERED


def test_t2_8_help_keyword(db, base, settings):
    before = db.scalar(select(func.count(Consent.id)))
    inbound(db, base, "help", at("13:00"), settings=settings)
    replies = db.scalars(select(Message).where(Message.kind == MessageKind.HELP_REPLY)).all()
    assert len(replies) == 1
    assert replies[0].body == "Test Collision repair updates. Questions? Call (818) 555-0100. Msg & data rates may apply. Reply STOP to opt out."
    assert db.scalar(select(func.count(Consent.id))) == before


def test_t2_9_daily_cap(db, base, settings):
    for hhmm, stage in (("09:00", "PARTS_ORDERED"), ("10:00", "BODY_REPAIR"), ("11:00", "PAINT"), ("12:00", "READY_FOR_PICKUP")):
        move(db, base, stage, at(hhmm))
        hour, minute = hhmm.split(":")
        send(db, settings, at(f"{hour}:{int(minute) + 10:02d}"))
    messages = new_stage_messages(db, base)
    delivered = [m for m in messages if m.status == MessageStatus.DELIVERED]
    assert len(delivered) == 3
    fourth = stage_messages(db, base.ro1187_id, "READY_FOR_PICKUP")[0]
    assert fourth.status == MessageStatus.SCHEDULED
    assert fourth.scheduled_send_at.isoformat() == "2026-10-06T15:00:00+00:00"


def test_t2_10_each_stage_texts_once(db, base, settings):
    for hhmm, stage in (("13:00", "PAINT"), ("14:00", "BODY_REPAIR"), ("15:00", "PAINT")):
        move(db, base, stage, at(hhmm))
        send(db, settings, at(hhmm.replace(":00", ":10")))
    assert len(stage_messages(db, base.ro1187_id, "PAINT")) == 1
    assert len(stage_messages(db, base.ro1187_id, "BODY_REPAIR")) == 1


def test_t2_11_stage_with_texting_off(db, base):
    before = db.scalar(select(func.count(Message.id)))
    move(db, base, "TEARDOWN", at("13:00"))
    assert db.scalar(select(func.count(Message.id))) == before


def test_t2_12_first_message_gets_stop_suffix(app, db, base, settings):
    shop_settings = settings_for(db, base.shop_id)
    shop_settings.stage_templates = {**shop_settings.stage_templates, "CHECKED_IN": "{shop_name}: Your {vehicle} is checked in."}
    db.commit()
    with freeze_time(at("13:00")):
        client = login_as(app, "admin@test.local")
        response = client.post(
            "/ro/new",
            data={
                "csrf_token": client.csrf,
                "phone": "+18185550144",
                "first_name": "Ani",
                "last_name": "Petrosyan",
                "ro_number": "24-1189",
                "vehicle_year": "2023",
                "vehicle_make": "BMW",
                "vehicle_model": "M3",
                "payer_type": "CUSTOMER_PAY",
                "original_estimate": "9120",
                "checked_in_at": "2026-10-05T13:00",
                "consent": "yes",
                "consent_method": "SIGNED_FORM",
            },
            follow_redirects=False,
        )
        assert response.status_code == 303
    ro = db.scalar(select(RepairOrder).where(RepairOrder.ro_number == "24-1189"))
    send(db, settings, at("13:10"))
    move(db, base, "PARTS_ORDERED", at("13:20"), ro_id=ro.id)
    send(db, settings, at("13:30"))
    first, second = stage_messages(db, ro.id)
    assert first.body == "Test Collision: Your 2023 BMW M3 is checked in. Reply STOP to opt out."
    assert first.status == MessageStatus.DELIVERED
    assert second.status == MessageStatus.DELIVERED
    assert "Reply STOP to opt out." not in second.body


def test_t2_13_manual_text_too_long(app, db, base):
    with freeze_time(at("13:00")):
        client = login_as(app, "staff@test.local")
        response = client.post(f"/ro/{base.ro1187_id}/messages", data={"csrf_token": client.csrf, "body": "x" * 320}, follow_redirects=False)
        assert response.status_code == 303
    message = db.scalar(select(Message).where(Message.kind == MessageKind.MANUAL))
    assert message.body == "Test Collision: " + "x" * 320
    assert len(message.body) == 336
    assert message.status == MessageStatus.FAILED
    assert message.error_text == "BODY_TOO_LONG"


def test_t2_14_inbound_reply(db, base, settings):
    outbound_before = db.scalar(select(func.count(Message.id)).where(Message.direction == MessageDirection.OUTBOUND))
    inbound(db, base, "When will it be ready?", at("13:00"), settings=settings)
    replies = db.scalars(select(Message).where(Message.kind == MessageKind.INBOUND_REPLY)).all()
    assert len(replies) == 1
    assert replies[0].status == MessageStatus.RECEIVED
    assert replies[0].repair_order_id == base.ro1187_id
    db.expire_all()
    assert db.get(RepairOrder, base.ro1187_id).needs_reply is True
    assert db.scalar(select(func.count(Message.id)).where(Message.direction == MessageDirection.OUTBOUND)) == outbound_before


def test_t2_15_manual_text_in_quiet_hours(db, base, settings):
    ro = db.get(RepairOrder, base.ro1187_id)
    with freeze_time(at("21:00")):
        message = send_manual_text(db, ro, "Your car is almost ready.", base.staff_id, at("21:00"), settings)
        db.commit()
    assert message.status == MessageStatus.SCHEDULED
    assert message.scheduled_send_at.isoformat() == "2026-10-06T15:00:00+00:00"


def test_t2_16_render(db, base):
    ro = db.get(RepairOrder, base.ro1187_id)
    shop = db.get(Shop, base.shop_id)
    text = render_stage_text(db, shop, settings_for(db, base.shop_id), ro, "{shop_name}: Your {vehicle} is in paint.")
    assert text == "Test Collision: Your 2021 Honda Accord is in paint."


def test_t2_17_quiet_hours_across_daylight_saving_end(db, base, settings):
    move(db, base, "PARTS_ORDERED", local("2026-10-31 19:55"))
    send(db, settings, local("2026-10-31 20:05"))
    message = stage_messages(db, base.ro1187_id, "PARTS_ORDERED")[0]
    assert message.status == MessageStatus.SCHEDULED
    assert message.scheduled_send_at.isoformat() == "2026-11-01T16:00:00+00:00"


def test_t2_18_demo_console_masks_phone(capsys):
    DemoProvider().send("+18185550142", "Test Collision: Your car is ready.")
    out = capsys.readouterr().out
    line = next(line for line in out.splitlines() if line.startswith("[DEMO SMS]"))
    assert "+1818***0142" in line
    assert "+18185550142" not in line


def test_customer_lookup_and_message_log_pages(app, db, base):
    """Smoke test: the lookup fragment, RO page panel and message log render."""
    with freeze_time(at("13:00")):
        client = login_as(app, "staff@test.local")
        fragment = client.get("/customers/lookup", params={"phone": "(818) 555-0142"}).text
        assert 'value="Maria"' in fragment and 'value="Lopez"' in fragment
        page = client.get(f"/ro/{base.ro1187_id}").text
        assert "Simulate customer reply" in page and "Opted in" in page
        assert "Hi Maria, this is Test Collision." in client.get("/messages").text
    assert db.scalar(select(func.count(Customer.id))) == 2
