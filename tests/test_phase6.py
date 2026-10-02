"""Phase 6: settings and LIVE mode. Tests never call the real Twilio API; the Twilio client is mocked."""

import base64
import hashlib
import hmac
import html
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from freezegun import freeze_time
from sqlalchemy import func, select

from app.enums import ConsentStatus, MessageDirection, MessageKind, MessageStatus, MessagingMode, Stage
from app.main import create_app
from app.messaging import providers
from app.messaging.engine import current_consent, run_sender, settings_for
from app.messaging.providers import ProviderError, TwilioProvider
from app.messaging.templates import DEFAULT_TEMPLATES, TEXTABLE_STAGES
from app.models import Message, RepairOrder, Shop, User
from app.routes.repair_orders import change_stage
from app.routes.settings_routes import preview_template
from tests.conftest import login_as, make_settings
from tests.fixtures import local

AUTH_TOKEN = "test-twilio-auth-token"
PUBLIC_BASE_URL = "https://shop.example.com"
TWILIO_NUMBER = "+18185550188"
NOW = local("2026-10-05 12:00")


@pytest.fixture
def live_app(db_url):
    return create_app(
        make_settings(
            db_url,
            ALLOW_LIVE_SMS=True,
            TWILIO_ACCOUNT_SID="AC" + "0" * 32,
            TWILIO_AUTH_TOKEN=AUTH_TOKEN,
            PUBLIC_BASE_URL=PUBLIC_BASE_URL,
        )
    )


@pytest.fixture
def twilio_mock(monkeypatch):
    client = MagicMock()
    client.messages.create.return_value = SimpleNamespace(sid="SM" + "1" * 32)
    monkeypatch.setattr(providers, "make_twilio_client", lambda sid, token: client)
    return client


def twilio_signature(url: str, params: dict) -> str:
    """Twilio's algorithm, computed here with the standard library: URL + sorted key/value pairs, HMAC-SHA1, base64."""
    data = url + "".join(key + params[key] for key in sorted(params))
    return base64.b64encode(hmac.new(AUTH_TOKEN.encode(), data.encode(), hashlib.sha1).digest()).decode()


def make_live(db, base, handles_keywords=True):
    settings = settings_for(db, base.shop_id)
    settings.messaging_mode = MessagingMode.LIVE
    settings.twilio_from_e164 = TWILIO_NUMBER
    settings.a2p_10dlc_approved = True
    settings.consent_script_confirmed = True
    settings.twilio_handles_keyword_replies = handles_keywords
    db.commit()


def post_mode(client, **fields):
    return client.post("/settings", data={"csrf_token": client.csrf, "tab": "mode", **fields}, follow_redirects=False)


def test_t6_1_live_refused_without_allow_live_sms(app, db, base):
    with freeze_time(NOW):
        client = login_as(app, "admin@test.local")
        post_mode(client, action="save", twilio_from_e164=TWILIO_NUMBER, a2p_10dlc_approved="yes", consent_script_confirmed="yes")
        response = post_mode(client, action="switch", mode="LIVE")
    assert response.status_code == 422
    assert "LIVE mode refused" in response.text
    failed = response.text.split('class="failed-preconditions"')[1].split("</ul>")[0]
    assert "ALLOW_LIVE_SMS=true in .env" in failed
    db.expire_all()
    assert settings_for(db, base.shop_id).messaging_mode == MessagingMode.DEMO


def test_t6_2_live_stage_text_goes_through_twilio(live_app, db, base, twilio_mock):
    with freeze_time(NOW):
        client = login_as(live_app, "admin@test.local")
        saved = post_mode(client, action="save", twilio_from_e164=TWILIO_NUMBER, a2p_10dlc_approved="yes", consent_script_confirmed="yes", twilio_handles_keyword_replies="yes")
        assert saved.status_code == 303
        switched = post_mode(client, action="switch", mode="LIVE")
        assert switched.status_code == 303
    db.expire_all()
    assert settings_for(db, base.shop_id).messaging_mode == MessagingMode.LIVE

    ro = db.get(RepairOrder, base.ro1187_id)
    with freeze_time(local("2026-10-05 13:00")):
        change_stage(db, ro, Stage.PARTS_ORDERED, db.get(User, base.admin_id), local("2026-10-05 13:00"))
        db.commit()
    with freeze_time(local("2026-10-05 13:10")):
        run_sender(local("2026-10-05 13:10"), db, live_app.state.settings)

    twilio_mock.messages.create.assert_called_once()
    kwargs = twilio_mock.messages.create.call_args.kwargs
    assert kwargs["to"] == "+18185550142"
    assert kwargs["from_"] == TWILIO_NUMBER
    assert kwargs["status_callback"] == "https://shop.example.com/webhooks/twilio/status"
    message = db.scalar(select(Message).where(Message.stage == Stage.PARTS_ORDERED))
    assert message.status == MessageStatus.SENT
    assert message.provider_message_id == "SM" + "1" * 32
    assert message.from_e164 == TWILIO_NUMBER


def test_t6_3_twilio_provider_refuses_in_demo(live_app, db, base, twilio_mock):
    provider = TwilioProvider(settings_for(db, base.shop_id), live_app.state.settings)
    with pytest.raises(ProviderError):
        provider.send("+18185550142", "Test Collision: hello", TWILIO_NUMBER)
    twilio_mock.messages.create.assert_not_called()


def test_t6_4_inbound_webhook_signature(live_app, db, base, twilio_mock):
    make_live(db, base, handles_keywords=True)
    client = TestClient(live_app)
    params = {"From": "+18185550142", "To": TWILIO_NUMBER, "Body": "STOP", "MessageSid": "SM" + "2" * 32}
    url = PUBLIC_BASE_URL + "/webhooks/twilio/inbound"
    before = db.scalar(select(func.count(Message.id)))

    with freeze_time(NOW):
        bad = client.post("/webhooks/twilio/inbound", data=params, headers={"X-Twilio-Signature": "not-valid"})
    assert bad.status_code == 403
    assert db.scalar(select(func.count(Message.id))) == before

    with freeze_time(NOW):
        good = client.post("/webhooks/twilio/inbound", data=params, headers={"X-Twilio-Signature": twilio_signature(url, params)})
    assert good.status_code == 200
    db.expire_all()
    assert current_consent(db, base.shop_id, "+18185550142").status == ConsentStatus.OPTED_OUT
    replies = db.scalar(
        select(func.count(Message.id)).where(Message.direction == MessageDirection.OUTBOUND, Message.kind == MessageKind.OPT_OUT_CONFIRMATION)
    )
    assert replies == 0
    assert db.scalar(select(func.count(Message.id))) == before + 1  # just the stored keyword
    twilio_mock.messages.create.assert_not_called()


def test_t6_5_status_callbacks(live_app, db, base):
    make_live(db, base)
    ids = []
    for sid in ("SMaaaa", "SMbbbb"):
        message = Message(
            shop_id=base.shop_id,
            repair_order_id=base.ro1187_id,
            customer_id=base.maria_id,
            direction=MessageDirection.OUTBOUND,
            kind=MessageKind.MANUAL,
            status=MessageStatus.SENT,
            to_e164="+18185550142",
            from_e164=TWILIO_NUMBER,
            body="Test Collision: hello",
            sent_at=NOW,
            provider_message_id=sid,
        )
        db.add(message)
        db.commit()
        ids.append(message.id)

    client = TestClient(live_app)
    url = PUBLIC_BASE_URL + "/webhooks/twilio/status"
    first = {"MessageSid": "SMaaaa", "MessageStatus": "delivered"}
    second = {"MessageSid": "SMbbbb", "MessageStatus": "undelivered", "ErrorCode": "30003"}
    for params in (first, second):
        response = client.post("/webhooks/twilio/status", data=params, headers={"X-Twilio-Signature": twilio_signature(url, params)})
        assert response.status_code == 200
    db.expire_all()
    assert db.get(Message, ids[0]).status == MessageStatus.DELIVERED
    failed = db.get(Message, ids[1])
    assert failed.status == MessageStatus.FAILED
    assert failed.error_text == "30003"


def _texting_form(client, settings, **overrides) -> dict:
    form = {
        "csrf_token": client.csrf,
        "tab": "texting",
        "quiet_start": "20:00",
        "quiet_end": "08:00",
        "cool_off_minutes": "10",
        "daily_cap": "3",
        "review_url": "",
    }
    for stage in TEXTABLE_STAGES:
        form[f"template_{stage.value}"] = settings.stage_templates.get(stage.value, DEFAULT_TEMPLATES[stage])
        if settings.stage_text_enabled.get(stage.value):
            form[f"enabled_{stage.value}"] = "yes"
    form.update(overrides)
    return form


def test_t6_6_settings_ranges_and_templates(app, db, base):
    settings = settings_for(db, base.shop_id)
    attempts = [
        ({"quiet_start": "22:00"}, "quiet_start must be from 12:00 to 21:00."),
        ({"quiet_end": "07:00"}, "quiet_end must be from 08:00 to 11:00."),
        ({"cool_off_minutes": "61"}, "cool_off_minutes must be from 0 to 60."),
        ({"daily_cap": "0"}, "daily_cap must be from 1 to 10."),
        ({"template_PAINT": "x" * 251}, "longer than 250 characters"),
        ({"template_PAINT": "{shop_name}: Hi {nickname}, your car is in paint."}, "Unknown variable {nickname}"),
    ]
    with freeze_time(NOW):
        client = login_as(app, "admin@test.local")
        responses = [client.post("/settings", data=_texting_form(client, settings, **change)) for change, _ in attempts]
    for response, (_, message) in zip(responses, attempts):
        assert response.status_code == 422
        assert message in html.unescape(response.text)
    assert "Unknown variable {nickname}" in html.unescape(responses[-1].text)
    db.expire_all()
    unchanged = settings_for(db, base.shop_id)
    assert (unchanged.quiet_start.strftime("%H:%M"), unchanged.quiet_end.strftime("%H:%M")) == ("20:00", "08:00")
    assert (unchanged.cool_off_minutes, unchanged.daily_cap) == (10, 3)
    assert unchanged.stage_templates["PAINT"] == DEFAULT_TEMPLATES[Stage.PAINT]

    with freeze_time(NOW):
        ok = client.post("/settings", data=_texting_form(client, settings, daily_cap="5"), follow_redirects=False)
    assert ok.status_code == 303
    db.expire_all()
    assert settings_for(db, base.shop_id).daily_cap == 5


def test_t6_7_webhooks_404_in_demo(app, base):
    client = TestClient(app)
    assert client.post("/webhooks/twilio/inbound", data={"From": "+18185550142", "To": TWILIO_NUMBER, "Body": "hi"}).status_code == 404
    assert client.post("/webhooks/twilio/status", data={"MessageSid": "SMx", "MessageStatus": "delivered"}).status_code == 404


def test_t6_8_live_preview(app, db, base):
    expected = (
        "Hi Maria, this is Test Collision. Your 2021 Honda Accord is checked in (RO 24-1187). "
        "We'll text you as the repair moves along. Reply STOP to opt out, HELP for help."
    )
    shop = db.get(Shop, base.shop_id)
    assert preview_template(shop, settings_for(db, base.shop_id), DEFAULT_TEMPLATES[Stage.CHECKED_IN]) == expected
    with freeze_time(NOW):
        page = login_as(app, "admin@test.local").get("/settings", params={"tab": "texting"})
    assert f'<div class="preview" id="preview-CHECKED_IN">{expected}</div>' in html.unescape(page.text)


def test_settings_users_and_insurers(app, db, base):
    """Smoke test: add a user, an admin cannot deactivate themselves, add an insurer, staff gets 403."""
    with freeze_time(NOW):
        client = login_as(app, "admin@test.local")
        added = client.post(
            "/settings",
            data={"csrf_token": client.csrf, "tab": "users", "action": "add_user", "full_name": "New Tech", "email": "Tech@Test.local", "role": "STAFF", "password": "long-enough-pass"},
            follow_redirects=False,
        )
        assert added.status_code == 303
        own = client.post("/settings", data={"csrf_token": client.csrf, "tab": "users", "action": "deactivate", "user_id": str(base.admin_id)})
        assert own.status_code == 422 and "cannot deactivate yourself" in own.text
        insurer = client.post(
            "/settings",
            data={"csrf_token": client.csrf, "tab": "insurers", "action": "insurer", "name": "alpha insurance"},
        )
        assert insurer.status_code == 422 and "already exists" in insurer.text
        assert login_as(app, "tech@test.local", "long-enough-pass").get("/settings").status_code == 403
