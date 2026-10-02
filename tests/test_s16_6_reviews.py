"""Section 16 item 6: review request after delivery, with separate consent and content rules."""

import html

from freezegun import freeze_time
from sqlalchemy import select

from app.enums import ConsentMethod, ConsentPurpose, ConsentStatus, MessageKind, MessageStatus, Stage
from app.messaging.engine import current_consent, run_sender, settings_for
from app.messaging.inbound import handle_inbound
from app.models import Customer, Message, RepairOrder, Shop, User
from app.routes.repair_orders import change_stage, record_consent
from tests.conftest import login_as
from tests.fixtures import local

REVIEW_URL = "https://g.page/r/test-collision"


def turn_on(db, base, delay=2):
    settings = settings_for(db, base.shop_id)
    settings.review_url = REVIEW_URL
    settings.review_request_enabled = True
    settings.review_request_delay_days = delay
    db.commit()


def review_consent(db, base, customer_id=None):
    customer = db.get(Customer, customer_id or base.maria_id)
    record_consent(db, base.shop_id, customer, ConsentStatus.OPTED_IN, ConsentMethod.IN_PERSON_VERBAL, base.admin_id, local("2026-10-01 09:00"), ConsentPurpose.REVIEW_REQUESTS)
    db.commit()


def deliver(db, base, when="2026-10-05 15:00", ro_id=None):
    ro = db.get(RepairOrder, ro_id or base.ro1187_id)
    with freeze_time(local(when)):
        change_stage(db, ro, Stage.DELIVERED, db.get(User, base.admin_id), local(when), final_invoice_cents=500000)
        db.commit()
    return ro


def review_messages(db, ro_id):
    return db.scalars(select(Message).where(Message.repair_order_id == ro_id, Message.kind == MessageKind.REVIEW_REQUEST)).all()


def send(db, settings, when):
    with freeze_time(local(when)):
        run_sender(local(when), db, settings)


def test_scheduled_after_delivery_and_sent_with_review_consent(db, base, settings):
    turn_on(db, base)
    review_consent(db, base)
    ro = deliver(db, base)
    (request,) = review_messages(db, ro.id)
    assert request.status == MessageStatus.SCHEDULED
    assert request.scheduled_send_at == local("2026-10-07 10:00")
    assert request.body == (
        "Test Collision: Thanks again for trusting us with your 2021 Honda Accord, Maria. "
        f"If you have a minute, a review helps us a lot: {REVIEW_URL} Reply STOP to opt out."
    )
    send(db, settings, "2026-10-07 09:59")
    assert request.status == MessageStatus.SCHEDULED
    send(db, settings, "2026-10-07 10:00")
    db.refresh(request)
    assert request.status == MessageStatus.DELIVERED


def test_delivered_text_has_no_review_link_while_review_requests_are_on(db, base, settings):
    turn_on(db, base)
    ro = deliver(db, base)
    delivered_text = db.scalar(select(Message).where(Message.repair_order_id == ro.id, Message.stage == Stage.DELIVERED))
    assert delivered_text.body == "Test Collision: Thanks for trusting us with your 2021 Honda Accord."
    assert REVIEW_URL not in delivered_text.body


def test_repair_consent_alone_is_not_enough(db, base, settings):
    turn_on(db, base)
    ro = deliver(db, base)  # Maria has repair-update consent only
    send(db, settings, "2026-10-07 10:00")
    (request,) = review_messages(db, ro.id)
    assert request.status == MessageStatus.BLOCKED_NO_CONSENT


def test_off_by_default_and_needs_a_review_url(db, base, settings):
    ro = deliver(db, base)
    assert review_messages(db, ro.id) == []
    settings_row = settings_for(db, base.shop_id)
    settings_row.review_request_enabled = True
    settings_row.review_url = ""
    db.commit()
    other = deliver(db, base, ro_id=base.ro1188_id)
    assert review_messages(db, other.id) == []


def test_once_per_ro_and_cancelled_when_reopened(db, base, settings):
    turn_on(db, base)
    review_consent(db, base)
    ro = deliver(db, base)
    admin = db.get(User, base.admin_id)
    with freeze_time(local("2026-10-06 09:00")):
        change_stage(db, ro, Stage.PAINT, admin, local("2026-10-06 09:00"))
        db.commit()
    (first,) = review_messages(db, ro.id)
    assert first.status == MessageStatus.CANCELLED_SUPERSEDED and first.error_text == "RO_REOPENED"
    deliver(db, base, when="2026-10-08 15:00")
    assert len(review_messages(db, ro.id)) == 1  # never a second request for the same RO


def test_stop_covers_review_requests_and_start_does_not_restore_them(db, base, settings):
    turn_on(db, base)
    review_consent(db, base)
    ro = deliver(db, base)
    shop = db.get(Shop, base.shop_id)
    with freeze_time(local("2026-10-06 12:00")):
        handle_inbound(db, shop, "+18185550142", shop.phone_e164, "STOP", local("2026-10-06 12:00"), settings)
        db.commit()
    (request,) = review_messages(db, ro.id)
    assert request.status == MessageStatus.BLOCKED_OPTED_OUT
    assert current_consent(db, base.shop_id, "+18185550142", ConsentPurpose.REVIEW_REQUESTS).status == ConsentStatus.OPTED_OUT
    with freeze_time(local("2026-10-06 13:00")):
        handle_inbound(db, shop, "+18185550142", shop.phone_e164, "START", local("2026-10-06 13:00"), settings)
        db.commit()
    assert current_consent(db, base.shop_id, "+18185550142").status == ConsentStatus.OPTED_IN
    assert current_consent(db, base.shop_id, "+18185550142", ConsentPurpose.REVIEW_REQUESTS).status == ConsentStatus.OPTED_OUT


def test_not_asked_twice_within_a_year(db, base, settings):
    turn_on(db, base)
    review_consent(db, base)
    first = deliver(db, base)
    send(db, settings, "2026-10-07 10:00")
    second_ro = RepairOrder(
        shop_id=base.shop_id,
        ro_number="24-1300",
        customer_id=base.maria_id,
        vehicle_year=2022,
        vehicle_make="Honda",
        vehicle_model="CR-V",
        payer_type="CUSTOMER_PAY",
        original_estimate_cents=100000,
        current_stage=Stage.CHECKED_IN,
        checked_in_at=local("2026-10-08 09:00"),
    )
    db.add(second_ro)
    db.commit()
    deliver(db, base, when="2026-10-09 15:00", ro_id=second_ro.id)
    send(db, settings, "2026-10-11 10:00")
    assert review_messages(db, first.id)[0].status == MessageStatus.DELIVERED
    (repeat,) = review_messages(db, second_ro.id)
    assert repeat.status == MessageStatus.CANCELLED_SUPERSEDED and repeat.error_text == "ALREADY_ASKED"


def test_check_in_form_and_settings(app, db, base):
    with freeze_time(local("2026-10-05 12:00")):
        admin = login_as(app, "admin@test.local")
        form = {
            "csrf_token": admin.csrf,
            "tab": "texting",
            "quiet_start": "20:00",
            "quiet_end": "08:00",
            "cool_off_minutes": "10",
            "daily_cap": "3",
            "review_url": "",
            "review_request_enabled": "yes",
            "review_request_delay_days": "3",
        }
        assert "Set the review URL" in html.unescape(admin.post("/settings", data=form).text)
        assert "must include {review_url}" in html.unescape(
            admin.post("/settings", data={**form, "review_url": REVIEW_URL, "review_request_template": "{shop_name}: please review us"}).text
        )
        assert admin.post("/settings", data={**form, "review_url": REVIEW_URL, "review_request_delay_days": "20"}).status_code == 422
        assert admin.post("/settings", data={**form, "review_url": REVIEW_URL}, follow_redirects=False).status_code == 303
        assert "1 review request after pickup" in admin.get("/ro/new").text
        created = admin.post(
            "/ro/new",
            data={
                "csrf_token": admin.csrf,
                "phone": "+18185550144",
                "first_name": "Ani",
                "ro_number": "24-1400",
                "vehicle_year": "2023",
                "vehicle_make": "BMW",
                "vehicle_model": "M3",
                "payer_type": "CUSTOMER_PAY",
                "original_estimate": "9120",
                "checked_in_at": "2026-10-05T12:00",
                "review_consent": "yes",
                "consent_method": "SIGNED_FORM",
            },
            follow_redirects=False,
        )
        assert created.status_code == 303
        page = admin.get(created.headers["location"]).text
        assert "Review request: <strong>Opted in</strong>" in page
    assert current_consent(db, base.shop_id, "+18185550144") is None  # repair updates not ticked
    assert current_consent(db, base.shop_id, "+18185550144", ConsentPurpose.REVIEW_REQUESTS).status == ConsentStatus.OPTED_IN
    assert settings_for(db, base.shop_id).review_request_delay_days == 3
