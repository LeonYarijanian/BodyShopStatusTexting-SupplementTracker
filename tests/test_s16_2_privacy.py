"""Section 16 item 2: delete or anonymize a customer on request."""

from freezegun import freeze_time
from sqlalchemy import func, select

from app.enums import MessageKind, MessageStatus, Stage
from app.messaging.engine import MessagingError, current_consent, send_manual_text
from app.models import Consent, Customer, Message, RepairOrder, User
from app.privacy import REMOVED_BODY, anonymize_customer
from app.routes.repair_orders import change_stage
from tests.conftest import login_as
from tests.fixtures import local

NOW = local("2026-10-05 13:00")


def _prepare(db, base):
    """Maria has a VIN, a scheduled stage text and an inbound reply."""
    ro = db.get(RepairOrder, base.ro1187_id)
    ro.vin = "1HGCV1F3XLA000001"
    with freeze_time(NOW):
        change_stage(db, ro, Stage.PARTS_ORDERED, db.get(User, base.admin_id), NOW)
        db.commit()
    return ro


def _anonymize_via_page(app, base, confirm="DELETE"):
    with freeze_time(NOW):
        admin = login_as(app, "admin@test.local")
        return admin.post(f"/customers/{base.maria_id}/anonymize", data={"csrf_token": admin.csrf, "confirm": confirm}, follow_redirects=False)


def test_anonymize_removes_personal_data_and_keeps_the_ro(app, db, base):
    ro = _prepare(db, base)
    response = _anonymize_via_page(app, base)
    assert response.status_code == 303 and response.headers["location"] == f"/ro/{base.ro1187_id}"
    db.expire_all()

    maria = db.get(Customer, base.maria_id)
    assert (maria.first_name, maria.last_name, maria.email) == ("Deleted", None, None)
    assert maria.phone_e164 == f"deleted-{maria.id}"
    assert maria.anonymized_at == NOW
    assert db.scalar(select(func.count(Consent.id)).where(Consent.customer_id == maria.id)) == 0
    assert current_consent(db, base.shop_id, "+18185550142") is None

    messages = db.scalars(select(Message).where(Message.repair_order_id == ro.id)).all()
    assert messages and all(m.body == REMOVED_BODY for m in messages)
    assert all("+18185550142" not in (m.to_e164, m.from_e164) for m in messages)
    scheduled = [m for m in messages if m.kind == MessageKind.STAGE_UPDATE and m.stage == Stage.PARTS_ORDERED][0]
    assert scheduled.status == MessageStatus.CANCELLED_SUPERSEDED
    assert scheduled.error_text == "CUSTOMER_DATA_DELETED"

    ro = db.get(RepairOrder, base.ro1187_id)
    assert (ro.vin, ro.claim_number) == (None, None)
    assert (ro.ro_number, ro.original_estimate_cents, ro.current_stage) == ("24-1187", 425050, Stage.PARTS_ORDERED)


def test_no_texts_after_anonymizing(app, db, base):
    _prepare(db, base)
    _anonymize_via_page(app, base)
    db.expire_all()
    ro = db.get(RepairOrder, base.ro1187_id)
    before = db.scalar(select(func.count(Message.id)))
    with freeze_time(local("2026-10-06 10:00")):
        change_stage(db, ro, Stage.PAINT, db.get(User, base.admin_id), local("2026-10-06 10:00"))
        db.commit()
    assert db.scalar(select(func.count(Message.id))) == before
    try:
        send_manual_text(db, ro, "Hello", base.admin_id, local("2026-10-06 10:00"))
        raise AssertionError("expected MessagingError")
    except MessagingError as exc:
        assert "deleted" in str(exc)
    with freeze_time(NOW):
        page = login_as(app, "staff@test.local").get(f"/ro/{base.ro1187_id}").text
    assert "Customer data deleted" in page and "Simulate customer reply" not in page and "+18185550142" not in page


def test_confirmation_word_required_and_admin_only(app, db, base):
    assert _anonymize_via_page(app, base, confirm="delete it").status_code == 303
    db.expire_all()
    assert db.get(Customer, base.maria_id).anonymized_at is None
    with freeze_time(NOW):
        staff = login_as(app, "staff@test.local")
        assert staff.post(f"/customers/{base.maria_id}/anonymize", data={"csrf_token": staff.csrf, "confirm": "DELETE"}).status_code == 403
        other = login_as(app, "other@test.local")
        assert other.post(f"/customers/{base.maria_id}/anonymize", data={"csrf_token": other.csrf, "confirm": "DELETE"}).status_code == 404
    db.expire_all()
    assert db.get(Customer, base.maria_id).anonymized_at is None


def test_running_twice_changes_nothing_and_the_phone_can_be_used_again(app, db, base):
    first = anonymize_customer(db, db.get(Customer, base.maria_id), NOW)
    db.commit()
    assert first["consents"] == 1
    assert anonymize_customer(db, db.get(Customer, base.maria_id), NOW) == {"already": True}

    with freeze_time(NOW):
        admin = login_as(app, "admin@test.local")
        created = admin.post(
            "/ro/new",
            data={
                "csrf_token": admin.csrf,
                "phone": "(818) 555-0142",
                "first_name": "Maria",
                "ro_number": "24-2001",
                "vehicle_year": "2022",
                "vehicle_make": "Honda",
                "vehicle_model": "CR-V",
                "payer_type": "CUSTOMER_PAY",
                "original_estimate": "1000",
                "checked_in_at": "2026-10-05T13:00",
            },
            follow_redirects=False,
        )
        assert created.status_code == 303
        edit = admin.get(f"/ro/{base.ro1187_id}/edit").text
        assert 'value="deleted-' in edit
        saved = admin.post(
            f"/ro/{base.ro1187_id}/edit",
            data={
                "csrf_token": admin.csrf,
                "phone": f"deleted-{base.maria_id}",
                "ro_number": "24-1187",
                "vehicle_year": "2021",
                "vehicle_make": "Honda",
                "vehicle_model": "Accord",
                "payer_type": "INSURANCE",
                "insurer_id": str(base.alpha_id),
                "original_estimate": "4500",
                "checked_in_at": "2026-10-01T09:00",
            },
            follow_redirects=False,
        )
        assert saved.status_code == 303
    new_customer = db.scalar(select(Customer).where(Customer.phone_e164 == "+18185550142"))
    assert new_customer is not None and new_customer.id != base.maria_id
    db.expire_all()
    ro = db.get(RepairOrder, base.ro1187_id)
    assert ro.customer_id == base.maria_id and ro.original_estimate_cents == 450000
