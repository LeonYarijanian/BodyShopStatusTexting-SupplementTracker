"""Section 16 item 9: multiple locations per shop. Tests never call the real Twilio API."""

import base64
import datetime as dt
import hashlib
import hmac
import html
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from freezegun import freeze_time
from sqlalchemy import select

from app.digest import build_digest
from app.enums import MessageKind, MessageStatus, MessagingMode, Stage, SupplementStatus
from app.locations import LocationError, add_location, ensure_main_location, set_active
from app.main import create_app
from app.messaging import providers
from app.messaging.engine import run_sender, send_manual_text, settings_for
from app.models import Location, Message, RepairOrder, Shop, User
from app.reports import all_reports
from app.routes.repair_orders import change_stage
from app.supplements import create_supplement, follow_up_email, transition
from tests.conftest import login_as, make_settings
from tests.fixtures import local

NOW = local("2026-10-05 12:00")
SHOP_PHONE = "+18185550100"
VAN_NUYS_PHONE = "+18185550200"
VAN_NUYS_TWILIO = "+18185550299"
SHOP_TWILIO = "+18185550188"
AUTH_TOKEN = "test-twilio-auth-token"
PUBLIC_BASE_URL = "https://shop.example.com"


def add_van_nuys(db, base, **extra) -> Location:
    location = add_location(db, db.get(Shop, base.shop_id), {"name": "Van Nuys", "phone": VAN_NUYS_PHONE, "address": "7000 Van Nuys Blvd", **extra})
    db.commit()
    return location


def main_location(db, base) -> Location:
    return db.scalar(select(Location).where(Location.shop_id == base.shop_id, Location.name == "Main"))


def move_to(db, ro_id, location) -> RepairOrder:
    ro = db.get(RepairOrder, ro_id)
    ro.location_id = location.id
    db.commit()
    return ro


def post_settings(client, tab, **fields):
    return client.post("/settings", data={"csrf_token": client.csrf, "tab": tab, **fields}, follow_redirects=False)


def switch(client, location_id, next_url="/"):
    return client.post("/location", data={"csrf_token": client.csrf, "location_id": str(location_id), "next": next_url}, follow_redirects=False)


# ---------------------------------------------------------------- setting up locations


def test_first_extra_location_turns_the_shop_into_main_plus_the_new_one(app, db, base):
    with freeze_time(NOW):
        admin = login_as(app, "admin@test.local")
        before = admin.get("/settings?tab=locations").text
        assert "This shop has one location" in before
        assert 'name="location_id" aria-label="Location"' not in admin.get("/").text  # no switcher yet
        added = post_settings(admin, "locations", action="add", name="Van Nuys", phone="(818) 555-0200", address="7000 Van Nuys Blvd")
        assert added.status_code == 303
        board = admin.get("/").text
    main = main_location(db, base)
    assert (main.phone_e164, main.address, main.is_active) == (SHOP_PHONE, None, True)
    van_nuys = db.scalar(select(Location).where(Location.name == "Van Nuys"))
    assert van_nuys.phone_e164 == VAN_NUYS_PHONE and van_nuys.twilio_from_e164 == ""
    # Existing repair orders go to Main.
    for ro_id in (base.ro1187_id, base.ro1188_id):
        assert db.get(RepairOrder, ro_id).location_id == main.id
    assert 'aria-label="Location"' in board and ">All locations<" in board and ">Van Nuys<" in board


def test_location_rules(app, db, base):
    van_nuys = add_van_nuys(db, base)
    shop = db.get(Shop, base.shop_id)
    other_settings = settings_for(db, base.other_shop_id)
    other_settings.twilio_from_e164 = "+18185550777"
    settings_for(db, base.shop_id).twilio_from_e164 = "+18185550666"
    db.commit()
    for values, message in (
        ({"name": "van nuys", "phone": "8185550300"}, "already a location named"),
        ({"name": "Burbank", "phone": "123"}, "valid US phone"),
        ({"name": "Burbank", "phone": "8185550300", "twilio_from_e164": "+18185550777"}, "already used by another location"),
        ({"name": "Burbank", "phone": "8185550300", "twilio_from_e164": "818-555-0300"}, "valid E.164"),
        ({"name": "Burbank", "phone": "8185550300", "twilio_from_e164": "+18185550666"}, "shop's own Twilio number"),
        ({"name": "Burbank", "phone": "8185550300", "review_url": "http://example.com"}, "start with https://"),
        ({"name": "", "phone": "8185550300"}, "1 to 120 characters"),
    ):
        with pytest.raises(LocationError, match=message):
            add_location(db, shop, values)
        db.rollback()

    # A location with open repair orders cannot be closed; once they move, it can.
    move_to(db, base.ro1188_id, van_nuys)
    with pytest.raises(LocationError, match="has 1 open RO"):
        set_active(db, van_nuys, False)
    move_to(db, base.ro1188_id, main_location(db, base))
    staff = db.get(User, base.staff_id)
    staff.location_id = van_nuys.id
    set_active(db, van_nuys, False)
    db.commit()
    assert staff.location_id is None  # their home location closed: they see everything again
    with pytest.raises(LocationError, match="at least one active location"):
        set_active(db, main_location(db, base), False)


def test_closing_through_settings_and_other_shops_get_404(app, db, base):
    van_nuys = add_van_nuys(db, base)
    move_to(db, base.ro1188_id, van_nuys)
    with freeze_time(NOW):
        admin = login_as(app, "admin@test.local")
        refused = post_settings(admin, "locations", action="close", location_id=str(van_nuys.id))
        assert refused.status_code == 422 and "Move them to another location first" in html.unescape(refused.text)
        edited = post_settings(admin, "locations", action="edit", location_id=str(van_nuys.id), name="Van Nuys", phone=VAN_NUYS_PHONE, twilio_from_e164=VAN_NUYS_TWILIO)
        assert edited.status_code == 303
        other = login_as(app, "other@test.local")
        assert post_settings(other, "locations", action="close", location_id=str(van_nuys.id)).status_code == 404
        # Another shop's location id in the switcher is ignored: they see their own shop.
        assert switch(other, van_nuys.id).status_code == 303
        assert "Van Nuys" not in other.get("/").text
    db.refresh(van_nuys)
    assert van_nuys.twilio_from_e164 == VAN_NUYS_TWILIO and van_nuys.is_active


# ---------------------------------------------------------------- filtering


@pytest.fixture
def two_locations(db, base):
    """ro1187 (insurance, with a submitted supplement) at Main; ro1188 (customer pay) at Van Nuys."""
    van_nuys = add_van_nuys(db, base)
    move_to(db, base.ro1188_id, van_nuys)
    ro = db.get(RepairOrder, base.ro1187_id)
    supplement = create_supplement(db, ro, "Hidden damage: RF apron", 180000, base.admin_id, local("2026-10-01 10:00"))
    transition(db, supplement, SupplementStatus.SUBMITTED, base.admin_id, local("2026-10-01 11:00"))
    db.commit()
    return SimpleNamespace(main=main_location(db, base), van_nuys=van_nuys, supplement=supplement)


def test_switcher_filters_board_supplements_and_counters(app, db, base, two_locations):
    with freeze_time(NOW):
        admin = login_as(app, "admin@test.local")
        everything = admin.get("/").text
        assert "24-1187" in everything and "24-1188" in everything
        assert '<span class="tag">Van Nuys</span>' in everything  # cards name their location under All locations

        assert switch(admin, two_locations.van_nuys.id, "/supplements?tab=open").headers["location"] == "/supplements?tab=open"
        board = admin.get("/").text
        assert "24-1188" in board and "24-1187" not in board
        assert "Waiting: $0.00" in board
        supplements = admin.get("/supplements").text
        assert "No supplements here." in supplements

        switch(admin, two_locations.main.id)
        board = admin.get("/").text
        assert "24-1187" in board and "24-1188" not in board
        assert "Waiting: $1,800.00" in board
        assert 'href="/ro/%d">24-1187</a>' % base.ro1187_id in admin.get("/supplements").text

        switch(admin, "all")
        assert "24-1188" in admin.get("/").text
        assert switch(admin, 999999).status_code == 303  # unknown id: all locations
        assert "24-1187" in admin.get("/").text and "24-1188" in admin.get("/").text
        assert switch(admin, "all", "//evil.example.com").headers["location"] == "/"


def test_home_location_is_where_a_user_starts(app, db, base, two_locations):
    with freeze_time(NOW):
        admin = login_as(app, "admin@test.local")
        saved = post_settings(admin, "users", action="set_location", user_id=str(base.staff_id), location_id=str(two_locations.van_nuys.id))
        assert saved.status_code == 303
        assert post_settings(admin, "users", action="set_location", user_id=str(base.staff_id), location_id="999999").status_code == 422
        staff = login_as(app, "staff@test.local")
        board = staff.get("/").text
        assert "24-1188" in board and "24-1187" not in board
        switch(staff, "all")
        assert "24-1187" in staff.get("/").text
    assert db.get(User, base.staff_id).location_id == two_locations.van_nuys.id


def test_reports_for_one_location(app, db, base, two_locations):
    admin = db.get(User, base.admin_id)
    with freeze_time(local("2026-10-05 15:00")):
        change_stage(db, db.get(RepairOrder, base.ro1187_id), Stage.DELIVERED, admin, local("2026-10-05 15:00"), final_invoice_cents=500000)
        change_stage(db, db.get(RepairOrder, base.ro1188_id), Stage.DELIVERED, admin, local("2026-10-05 16:00"), final_invoice_cents=200000)
        db.commit()
    start, end, now = dt.date(2026, 10, 1), dt.date(2026, 10, 31), local("2026-10-06 12:00")
    shop_wide = all_reports(db, base.shop_id, start, end, now)
    van_nuys = all_reports(db, base.shop_id, start, end, now, two_locations.van_nuys.id)
    main = all_reports(db, base.shop_id, start, end, now, two_locations.main.id)
    assert shop_wide["revenue"]["total_cents"] == 700000
    assert van_nuys["revenue"]["total_cents"] == 200000 and [r["name"] for r in van_nuys["revenue"]["rows"]] == ["Customer pay"]
    assert main["revenue"]["total_cents"] == 500000
    assert (shop_wide["cycle_time"]["overall"]["count"], van_nuys["cycle_time"]["overall"]["count"]) == (2, 1)
    assert (shop_wide["open_aging"]["count"], van_nuys["open_aging"]["count"], main["open_aging"]["count"]) == (1, 0, 1)
    assert van_nuys["days_waiting"]["count"] == 0 and main["days_waiting"]["count"] == 1
    # The fixture's check-in text is about ro1187, at Main.
    assert main["texting"]["delivered"] >= 1 and van_nuys["texting"]["delivered"] == 0

    with freeze_time(local("2026-10-06 12:00")):
        client = login_as(app, "admin@test.local")
        switch(client, two_locations.van_nuys.id)
        page = client.get("/reports?date_from=2026-10-01&date_to=2026-10-31").text
    assert "Reports · Van Nuys" in page and "Opt-outs by keyword (all locations)" in page


# ---------------------------------------------------------------- texts, emails, status page


def test_texts_show_the_location_phone_and_come_from_it(db, base, settings, two_locations, capsys):
    shop_settings = settings_for(db, base.shop_id)
    shop_settings.stage_templates = {**shop_settings.stage_templates, "PAINT": "{shop_name}: Your {vehicle} is in paint. Questions? {shop_phone}"}
    db.commit()
    james = db.get(RepairOrder, base.ro1188_id).customer
    from app.routes.repair_orders import record_consent
    from app.enums import ConsentMethod, ConsentStatus

    record_consent(db, base.shop_id, james, ConsentStatus.OPTED_IN, ConsentMethod.SIGNED_FORM, base.admin_id, local("2026-10-02 09:00"))
    db.commit()
    with freeze_time(NOW):
        change_stage(db, db.get(RepairOrder, base.ro1188_id), Stage.PAINT, db.get(User, base.admin_id), NOW)
        db.commit()
    with freeze_time(NOW + dt.timedelta(minutes=10)):
        run_sender(NOW + dt.timedelta(minutes=10), db, settings)
    text = db.scalar(select(Message).where(Message.repair_order_id == base.ro1188_id, Message.stage == Stage.PAINT))
    assert "Questions? (818) 555-0200" in text.body
    assert text.status == MessageStatus.DELIVERED and text.from_e164 == VAN_NUYS_PHONE

    # Adjuster follow-up emails sign off with the RO's location phone.
    email = follow_up_email(db, two_locations.supplement, db.get(User, base.staff_id), NOW)
    assert email.rstrip().endswith("(818) 555-0100")  # ro1187 is at Main
    move_to(db, base.ro1187_id, two_locations.van_nuys)
    assert follow_up_email(db, two_locations.supplement, db.get(User, base.staff_id), NOW).rstrip().endswith("(818) 555-0200")


@pytest.fixture
def live_app(db_url):
    app = create_app(
        make_settings(db_url, ALLOW_LIVE_SMS=True, TWILIO_ACCOUNT_SID="AC" + "0" * 32, TWILIO_AUTH_TOKEN=AUTH_TOKEN, PUBLIC_BASE_URL=PUBLIC_BASE_URL)
    )
    yield app
    app.state.engine.dispose()


@pytest.fixture
def twilio_mock(monkeypatch):
    client = MagicMock()
    client.messages.create.return_value = SimpleNamespace(sid="SM" + "1" * 32)
    monkeypatch.setattr(providers, "make_twilio_client", lambda sid, token: client)
    return client


def make_live(db, base, service_sid=""):
    shop_settings = settings_for(db, base.shop_id)
    shop_settings.messaging_mode = MessagingMode.LIVE
    shop_settings.twilio_from_e164 = SHOP_TWILIO
    shop_settings.twilio_messaging_service_sid = service_sid
    shop_settings.a2p_10dlc_approved = True
    shop_settings.consent_script_confirmed = True
    shop_settings.twilio_handles_keyword_replies = False
    db.commit()


def twilio_signature(url: str, params: dict) -> str:
    data = url + "".join(key + params[key] for key in sorted(params))
    return base64.b64encode(hmac.new(AUTH_TOKEN.encode(), data.encode(), hashlib.sha1).digest()).decode()


@pytest.mark.parametrize("service_sid", ["", "MG" + "3" * 32], ids=["from-number", "messaging-service"])
def test_live_texts_go_out_from_the_location_twilio_number(live_app, db, base, twilio_mock, service_sid):
    make_live(db, base, service_sid)
    van_nuys = add_van_nuys(db, base, twilio_from_e164=VAN_NUYS_TWILIO)
    assert main_location(db, base).twilio_from_e164 == ""  # Main keeps using the shop's number
    move_to(db, base.ro1187_id, van_nuys)
    with freeze_time(NOW):
        send_manual_text(db, db.get(RepairOrder, base.ro1187_id), "Your car is ready.", base.admin_id, NOW, live_app.state.settings)
        db.commit()
    kwargs = twilio_mock.messages.create.call_args.kwargs
    assert kwargs["from_"] == VAN_NUYS_TWILIO
    assert kwargs.get("messaging_service_sid", "") == service_sid
    assert db.scalar(select(Message).where(Message.kind == MessageKind.MANUAL)).from_e164 == VAN_NUYS_TWILIO

    # An RO at Main is sent exactly as before locations: the shop's number, or the messaging service picks the sender.
    move_to(db, base.ro1187_id, main_location(db, base))
    with freeze_time(NOW):
        send_manual_text(db, db.get(RepairOrder, base.ro1187_id), "Second text.", base.admin_id, NOW, live_app.state.settings)
        db.commit()
    kwargs = twilio_mock.messages.create.call_args.kwargs
    if service_sid:
        assert kwargs["messaging_service_sid"] == service_sid and "from_" not in kwargs
    else:
        assert kwargs["from_"] == SHOP_TWILIO


def test_inbound_text_to_a_location_number(live_app, db, base, twilio_mock):
    make_live(db, base)
    van_nuys = add_van_nuys(db, base, twilio_from_e164=VAN_NUYS_TWILIO)
    move_to(db, base.ro1188_id, van_nuys)
    params = {"From": "+18185550143", "To": VAN_NUYS_TWILIO, "Body": "HELP", "MessageSid": "SM" + "4" * 32}
    url = PUBLIC_BASE_URL + "/webhooks/twilio/inbound"
    with freeze_time(NOW):
        response = TestClient(live_app).post("/webhooks/twilio/inbound", data=params, headers={"X-Twilio-Signature": twilio_signature(url, params)})
    assert response.status_code == 200
    db.expire_all()
    inbound = db.scalar(select(Message).where(Message.kind == MessageKind.INBOUND_KEYWORD))
    assert inbound.shop_id == base.shop_id and inbound.repair_order_id == base.ro1188_id
    reply = db.scalar(select(Message).where(Message.kind == MessageKind.HELP_REPLY))
    assert "Questions? Call (818) 555-0200." in reply.body
    assert twilio_mock.messages.create.call_args.kwargs["from_"] == VAN_NUYS_TWILIO

    # A number nobody owns is ignored.
    stray = {**params, "To": "+18185550999", "MessageSid": "SM" + "5" * 32}
    with freeze_time(NOW):
        TestClient(live_app).post("/webhooks/twilio/inbound", data=stray, headers={"X-Twilio-Signature": twilio_signature(url, stray)})
    db.expire_all()
    assert db.scalar(select(Message).where(Message.provider_message_id == stray["MessageSid"])) is None


def test_status_page_and_digest_name_the_location(app, db, base, two_locations):
    from app.status_page import ensure_status_token

    ro = move_to(db, base.ro1187_id, two_locations.van_nuys)
    token = ensure_status_token(ro)
    db.commit()
    with freeze_time(NOW):
        page = TestClient(app).get(f"/s/{token}").text
    assert "Test Collision</strong> · Van Nuys" in page
    assert 'href="tel:+18185550200"' in page and "(818) 555-0200" in page and "7000 Van Nuys Blvd" in page
    assert "(818) 555-0100" not in page

    with freeze_time(local("2026-10-09 07:30")):
        digest = build_digest(db, db.get(Shop, base.shop_id), local("2026-10-09 07:30"))
    assert digest is not None and "RO 24-1187 S1: 2021 Honda Accord, Maria (Van Nuys)" in digest[1]


def test_review_request_uses_the_location_review_url(db, base, settings):
    from app.enums import ConsentMethod, ConsentPurpose, ConsentStatus
    from app.routes.repair_orders import record_consent

    van_nuys = add_van_nuys(db, base, review_url="https://g.page/r/van-nuys")
    shop_settings = settings_for(db, base.shop_id)
    shop_settings.review_url = "https://g.page/r/test-collision"
    shop_settings.review_request_enabled = True
    db.commit()
    maria = db.get(RepairOrder, base.ro1187_id).customer
    record_consent(db, base.shop_id, maria, ConsentStatus.OPTED_IN, ConsentMethod.IN_PERSON_VERBAL, base.admin_id, NOW, ConsentPurpose.REVIEW_REQUESTS)
    move_to(db, base.ro1187_id, van_nuys)
    with freeze_time(NOW):
        change_stage(db, db.get(RepairOrder, base.ro1187_id), Stage.DELIVERED, db.get(User, base.admin_id), NOW, final_invoice_cents=500000)
        db.commit()
    request = db.scalar(select(Message).where(Message.kind == MessageKind.REVIEW_REQUEST))
    assert "https://g.page/r/van-nuys" in request.body and "test-collision" not in request.body


# ---------------------------------------------------------------- RO form and CSV import


def test_ro_form_and_csv_import_pick_a_location(app, db, base, two_locations):
    form = {
        "phone": "+18185550144",
        "first_name": "Ani",
        "ro_number": "24-1400",
        "vehicle_year": "2023",
        "vehicle_make": "BMW",
        "vehicle_model": "M3",
        "payer_type": "CUSTOMER_PAY",
        "original_estimate": "9120",
        "checked_in_at": "2026-10-05T12:00",
    }
    with freeze_time(NOW):
        admin = login_as(app, "admin@test.local")
        switch(admin, two_locations.van_nuys.id)
        new_page = admin.get("/ro/new").text
        assert f'<option value="{two_locations.van_nuys.id}" selected>Van Nuys</option>' in new_page
        missing = admin.post("/ro/new", data={"csrf_token": admin.csrf, **form})
        assert missing.status_code == 422 and "Choose the location." in missing.text
        created = admin.post("/ro/new", data={"csrf_token": admin.csrf, **form, "location_id": str(two_locations.van_nuys.id)}, follow_redirects=False)
        assert created.status_code == 303
        ro_id = int(created.headers["location"].rsplit("/", 1)[1])
        assert "Van Nuys" in admin.get(f"/ro/{ro_id}").text
        moved = admin.post(f"/ro/{ro_id}/edit", data={"csrf_token": admin.csrf, **form, "location_id": str(two_locations.main.id)}, follow_redirects=False)
        assert moved.status_code == 303

        header = "ro_number,checked_in_date,customer_first_name,customer_phone,vehicle_year,vehicle_make,vehicle_model,payer_type,original_estimate,location\n"
        bad = header + "24-1500,2026-10-04,Lee,8185550150,2020,Ford,F-150,CUSTOMER_PAY,1000,Nowhere\n"
        dry = admin.post("/import", data={"csrf_token": admin.csrf, "action": "dry_run"}, files={"file": ("ros.csv", bad.encode(), "text/csv")})
        assert "location: Nowhere is not one of this shop&#39;s active locations." in dry.text
        good = header + "24-1500,2026-10-04,Lee,8185550150,2020,Ford,F-150,CUSTOMER_PAY,1000,main\n24-1501,2026-10-04,Kim,8185550151,2020,Kia,Soul,CUSTOMER_PAY,900,\n"
        dry = admin.post("/import", data={"csrf_token": admin.csrf, "action": "dry_run"}, files={"file": ("ros.csv", good.encode(), "text/csv")})
        upload_id = dry.text.split('name="upload_id" value="')[1].split('"')[0]
        committed = admin.post("/import", data={"csrf_token": admin.csrf, "action": "commit", "upload_id": upload_id})
        assert "Imported 2 repair orders" in committed.text
    assert db.get(RepairOrder, ro_id).location_id == two_locations.main.id
    by_number = {ro.ro_number: ro.location_id for ro in db.scalars(select(RepairOrder).where(RepairOrder.ro_number.in_(["24-1500", "24-1501"])))}
    assert by_number == {"24-1500": two_locations.main.id, "24-1501": two_locations.van_nuys.id}  # empty: the importer's location


def test_single_location_shops_work_like_before(db, base):
    """One location (or none): nothing is filtered and the form asks nothing new."""
    shop = db.get(Shop, base.shop_id)
    main = ensure_main_location(db, shop)
    db.commit()
    assert db.get(RepairOrder, base.ro1187_id).location_id == main.id
    assert ensure_main_location(db, shop).id == main.id  # idempotent
    from app.locations import active_location_id

    assert active_location_id(main.id, None, [main]) is None
