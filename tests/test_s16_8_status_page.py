"""Section 16 item 8: customer status page linked from texts."""

import datetime as dt

from fastapi.testclient import TestClient
from freezegun import freeze_time
from sqlalchemy import select

from app.enums import Stage
from app.messaging.engine import settings_for
from app.models import Customer, Message, RepairOrder, User
from app.privacy import anonymize_customer
from app.routes.repair_orders import change_stage
from app.status_page import ensure_status_token
from tests.conftest import login_as
from tests.fixtures import local

NOW = local("2026-10-05 13:00")


def move(db, base, stage, when, ro_id=None, **extra):
    ro = db.get(RepairOrder, ro_id or base.ro1187_id)
    with freeze_time(when):
        change_stage(db, ro, Stage(stage), db.get(User, base.admin_id), when, **extra)
        db.commit()
    return ro


def token_for(db, base, ro_id=None):
    ro = db.get(RepairOrder, ro_id or base.ro1187_id)
    token = ensure_status_token(ro)
    db.commit()
    return token


def test_status_link_in_a_text_and_the_page(app, db, base):
    settings = settings_for(db, base.shop_id)
    settings.stage_templates = {**settings.stage_templates, "PAINT": "{shop_name}: Your {vehicle} is in paint. Track it here: {status_link}"}
    db.commit()
    move(db, base, "PARTS_ORDERED", local("2026-10-02 10:00"))
    move(db, base, "PAINT", NOW)
    ro = db.get(RepairOrder, base.ro1187_id)
    assert len(ro.status_token) == 32
    text = db.scalar(select(Message).where(Message.stage == Stage.PAINT))
    assert text.body == f"Test Collision: Your 2021 Honda Accord is in paint. Track it here: /s/{ro.status_token}"

    with freeze_time(local("2026-10-05 14:00")):
        page = TestClient(app).get(f"/s/{ro.status_token}")
    assert page.status_code == 200
    html = page.text
    assert "Your 2021 Honda Accord" in html and "Now: <strong>Paint</strong>" in html
    assert '<li class="done ">Checked in</li>' in html and '<li class="done ">Parts ordered</li>' in html
    assert '<li class=" current">Paint</li>' in html
    assert "(818) 555-0100" in html
    for private in ("Lopez", "+18185550142", "4,250", "Alpha Insurance", "CLM-55102", "24-1187"):
        assert private not in html, private
    assert page.headers["cache-control"] == "no-store"
    assert page.headers["x-robots-tag"] == "noindex, nofollow"


def test_link_expires_30_days_after_pickup_and_when_cancelled(app, db, base):
    token = token_for(db, base)
    move(db, base, "DELIVERED", NOW, final_invoice_cents=500000)
    with freeze_time(NOW + dt.timedelta(days=29)):
        assert TestClient(app).get(f"/s/{token}").status_code == 200
    with freeze_time(NOW + dt.timedelta(days=31)):
        expired = TestClient(app).get(f"/s/{token}")
    assert expired.status_code == 404 and "This link is not active" in expired.text

    other = token_for(db, base, base.ro1188_id)
    move(db, base, "CANCELLED", NOW, ro_id=base.ro1188_id)
    with freeze_time(NOW):
        assert TestClient(app).get(f"/s/{other}").status_code == 404


def test_unknown_or_malformed_tokens_and_deleted_customers(app, db, base):
    token = token_for(db, base)
    client = TestClient(app)
    assert client.get("/s/" + "x" * 32).status_code == 404
    assert client.get("/s/short").status_code == 404
    anonymize_customer(db, db.get(Customer, base.maria_id), NOW)
    db.commit()
    assert client.get(f"/s/{token}").status_code == 404
    assert db.get(RepairOrder, base.ro1187_id).status_token is None


def test_staff_see_the_link_and_templates_accept_the_variable(app, db, base):
    with freeze_time(NOW):
        admin = login_as(app, "admin@test.local")
        page = admin.get(f"/ro/{base.ro1188_id}").text  # created before item 8: the token is made on first view
        token = db.get(RepairOrder, base.ro1188_id).status_token
        assert token and f"/s/{token}" in page
        preview = admin.post(
            "/settings/preview",
            data={"stage": "READY_FOR_PICKUP", "template_READY_FOR_PICKUP": "{shop_name}: Ready! Details: {status_link}"},
            headers={"X-CSRF-Token": admin.csrf},
        )
    assert preview.text == "Test Collision: Ready! Details: /s/EXAMPLE-LINK-0000000000000000000"
