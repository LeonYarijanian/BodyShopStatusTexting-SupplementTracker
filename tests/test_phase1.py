"""Phase 1: repair orders and board."""

import re

from freezegun import freeze_time
from sqlalchemy import func, select

from app.enums import PayerType, Stage
from app.models import RepairOrder, StageEvent
from tests.conftest import login_as
from tests.fixtures import local


def ro_form(client, **overrides) -> dict:
    data = {
        "csrf_token": client.csrf,
        "phone": "(818) 555-0142",
        "first_name": "Maria",
        "last_name": "Lopez",
        "email": "",
        "ro_number": "24-0001",
        "vehicle_year": "2021",
        "vehicle_make": "Honda",
        "vehicle_model": "Accord",
        "vehicle_color": "Silver",
        "vin": "",
        "payer_type": "INSURANCE",
        "insurer_id": "",
        "adjuster_id": "",
        "claim_number": "CLM-1",
        "original_estimate": "4,250.50",
        "checked_in_at": "",
    }
    data.update(overrides)
    return data


def move(client, ro_id: int, stage: str, **extra):
    data = {"csrf_token": client.csrf, "stage": stage, "source": "ro", **extra}
    return client.post(f"/ro/{ro_id}/stage", data=data, follow_redirects=False)


def stage_event_count(db, ro_id: int) -> int:
    return db.scalar(select(func.count(StageEvent.id)).where(StageEvent.repair_order_id == ro_id))


def test_t1_1_create_ro(app, db, base):
    with freeze_time(local("2026-10-05 13:00")):
        admin = login_as(app, "admin@test.local")
        response = admin.post(
            "/ro/new",
            data=ro_form(admin, insurer_id=str(base.alpha_id), adjuster_id=str(base.dana_id), checked_in_at="2026-10-05T13:00"),
            follow_redirects=False,
        )
    assert response.status_code == 303
    ro = db.scalar(select(RepairOrder).where(RepairOrder.ro_number == "24-0001"))
    assert ro.current_stage == Stage.CHECKED_IN
    assert ro.checked_in_at == local("2026-10-05 13:00")
    assert ro.customer_id == base.maria_id
    events = db.scalars(select(StageEvent).where(StageEvent.repair_order_id == ro.id)).all()
    assert len(events) == 1
    assert events[0].from_stage is None
    assert events[0].to_stage == Stage.CHECKED_IN


def test_t1_2_move_twice(app, db, base):
    with freeze_time(local("2026-10-05 13:00")):
        admin = login_as(app, "admin@test.local")
        assert move(admin, base.ro1187_id, "TEARDOWN").status_code == 303
        assert move(admin, base.ro1187_id, "PAINT").status_code == 303
    db.expire_all()
    assert stage_event_count(db, base.ro1187_id) == 3
    assert db.get(RepairOrder, base.ro1187_id).current_stage == Stage.PAINT


def test_t1_3_move_to_same_stage_does_nothing(app, db, base):
    with freeze_time(local("2026-10-05 13:00")):
        admin = login_as(app, "admin@test.local")
        move(admin, base.ro1187_id, "TEARDOWN")
        move(admin, base.ro1187_id, "PAINT")
        move(admin, base.ro1187_id, "PAINT")
    db.expire_all()
    assert stage_event_count(db, base.ro1187_id) == 3


def test_t1_4_delivered_requires_final_invoice(app, db, base):
    with freeze_time(local("2026-10-05 13:00")):
        admin = login_as(app, "admin@test.local")
        move(admin, base.ro1187_id, "PAINT")
        first = move(admin, base.ro1187_id, "DELIVERED")
        page = admin.get(first.headers["location"])
    assert "Enter the final invoice before moving to Delivered." in page.text
    db.expire_all()
    assert db.get(RepairOrder, base.ro1187_id).current_stage == Stage.PAINT

    with freeze_time(local("2026-10-09 16:00")):
        admin = login_as(app, "admin@test.local")
        move(admin, base.ro1187_id, "DELIVERED", final_invoice="5123.00")
    db.expire_all()
    ro = db.get(RepairOrder, base.ro1187_id)
    assert ro.current_stage == Stage.DELIVERED
    assert ro.final_invoice_cents == 512300
    assert ro.delivered_at.isoformat() == "2026-10-09T23:00:00+00:00"


def test_t1_5_only_admin_moves_out_of_delivered(app, db, base):
    with freeze_time(local("2026-10-09 16:00")):
        admin = login_as(app, "admin@test.local")
        staff = login_as(app, "staff@test.local")
        move(admin, base.ro1187_id, "DELIVERED", final_invoice="5123.00")
        assert move(staff, base.ro1187_id, "PAINT").status_code == 403
        db.expire_all()
        assert db.get(RepairOrder, base.ro1187_id).current_stage == Stage.DELIVERED
        assert move(admin, base.ro1187_id, "PAINT").status_code == 303
    db.expire_all()
    ro = db.get(RepairOrder, base.ro1187_id)
    assert ro.current_stage == Stage.PAINT
    assert ro.delivered_at is None


def test_t1_6_customer_pay_with_insurer_is_rejected(admin_client, db, base):
    before = db.scalar(select(func.count(RepairOrder.id)))
    response = admin_client.post(
        "/ro/new",
        data=ro_form(admin_client, ro_number="24-0002", payer_type="CUSTOMER_PAY", insurer_id=str(base.alpha_id), claim_number=""),
        follow_redirects=False,
    )
    assert response.status_code == 422
    assert "customer-pay RO cannot have an insurer" in response.text
    assert db.scalar(select(func.count(RepairOrder.id))) == before


def _add_ro(db, base, number: str, stage: Stage, checked_in: str, delivered: str | None = None) -> RepairOrder:
    ro = RepairOrder(
        shop_id=base.shop_id,
        ro_number=number,
        customer_id=base.james_id,
        vehicle_year=2019,
        vehicle_make="Toyota",
        vehicle_model="Camry",
        payer_type=PayerType.CUSTOMER_PAY,
        original_estimate_cents=100000,
        final_invoice_cents=100000 if delivered else None,
        current_stage=stage,
        checked_in_at=local(checked_in),
        delivered_at=local(delivered) if delivered else None,
    )
    db.add(ro)
    db.commit()
    return ro


def test_t1_7_board_shows_only_active_ros(app, db, base):
    # The base fixture has 2 active ROs; add 3 more active, 2 delivered and 1 cancelled.
    _add_ro(db, base, "A-1", Stage.PAINT, "2026-10-01 08:00")
    _add_ro(db, base, "A-2", Stage.ON_HOLD, "2026-10-01 08:00")
    _add_ro(db, base, "A-3", Stage.READY_FOR_PICKUP, "2026-10-01 08:00")
    _add_ro(db, base, "D-1", Stage.DELIVERED, "2026-10-01 08:00", "2026-10-02 08:00")
    _add_ro(db, base, "D-2", Stage.DELIVERED, "2026-10-01 08:00", "2026-10-02 08:00")
    _add_ro(db, base, "C-1", Stage.CANCELLED, "2026-10-01 08:00")
    with freeze_time(local("2026-10-05 09:00")):
        html = login_as(app, "admin@test.local").get("/").text
    assert len(re.findall(r'<article class="card"', html)) == 5
    assert len(re.findall(r'class="board-column"', html)) == 12
    for hidden in ("D-1", "D-2", "C-1"):
        assert f"<strong>{hidden}</strong>" not in html


def test_t1_8_days_in_shop_rounds_down(app, db, base):
    ro = _add_ro(db, base, "24-2000", Stage.PAINT, "2026-10-01 10:00")
    with freeze_time(local("2026-10-05 09:00")):
        html = login_as(app, "admin@test.local").get("/").text
    card = re.search(rf'data-ro-id="{ro.id}".*?</article>', html, re.S).group(0)
    assert '<span class="days-in-shop">3d</span>' in card


def test_t1_9_vin_rules(app, db, base):
    client = login_as(app, "admin@test.local")
    ok = client.post(
        "/ro/new",
        data=ro_form(client, ro_number="24-0003", payer_type="CUSTOMER_PAY", claim_number="", vin="1hgcv1f3xla000001"),
        follow_redirects=False,
    )
    assert ok.status_code == 303
    assert db.scalar(select(RepairOrder.vin).where(RepairOrder.ro_number == "24-0003")) == "1HGCV1F3XLA000001"

    bad = client.post(
        "/ro/new",
        data=ro_form(client, ro_number="24-0004", payer_type="CUSTOMER_PAY", claim_number="", vin="1HGCV1F3OLA000001"),
        follow_redirects=False,
    )
    assert bad.status_code == 422
    assert "no I, O or Q" in bad.text
    assert db.scalar(select(RepairOrder.id).where(RepairOrder.ro_number == "24-0004")) is None
