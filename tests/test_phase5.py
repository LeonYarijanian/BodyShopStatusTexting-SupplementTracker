"""Phase 5: CSV import.

The base fixture's 2 ROs are deleted first, because the sample file reuses those RO numbers. Then insurers
Northline Insurance (Demo) and Harbor Mutual (Demo) are created, and the Section 11 sample file is used.
"""

import re
from pathlib import Path

import pytest
from freezegun import freeze_time
from sqlalchemy import delete, func, select

from app.csv_import import IMPORTED_NOTE, ImportFileError, commit_import, dry_run, read_rows
from app.enums import ConsentMethod, MessageKind, Stage
from app.models import Consent, Insurer, Message, RepairOrder, Shop, StageEvent, User
from app.routes.repair_orders import change_stage
from tests.conftest import login_as
from tests.fixtures import local

HEADER = (
    "ro_number,checked_in_date,checked_in_time,customer_first_name,customer_last_name,customer_phone,customer_email,"
    "vehicle_year,vehicle_make,vehicle_model,vehicle_color,vin,payer_type,insurer_name,adjuster_name,claim_number,"
    "original_estimate,stage,consent"
)
ROW_1 = '24-1187,2026-09-28,09:15,Maria,Lopez,(818) 555-0142,,2021,Honda,Accord,Silver,,INSURANCE,Northline Insurance (Demo),Dana Reyes,CLM-55102,"$4,250.50",PAINT,YES'
ROW_2 = "24-1188,2026-09-29,,James,Carter,818-555-0143,james@example.com,2019,Toyota,Camry,White,,CUSTOMER_PAY,,,,1875.00,PARTS_ORDERED,NO"
ROW_3 = "24-1189,2026-09-30,13:40,Ani,Petrosyan,+1 818 555 0144,,2023,BMW,M3,Blue,,INSURANCE,Harbor Mutual (Demo),,HM-77310,9120,TEARDOWN,YES"
NOW = local("2026-10-02 10:00")


def csv_bytes(*rows: str, header: str = HEADER) -> bytes:
    return ("\n".join([header, *rows]) + "\n").encode("utf-8")


SAMPLE = csv_bytes(ROW_1, ROW_2, ROW_3)


@pytest.fixture
def phase5(db, base, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # ./import_tmp/ lives in the test's folder
    ro_ids = [base.ro1187_id, base.ro1188_id]
    db.execute(delete(Message).where(Message.repair_order_id.in_(ro_ids)))
    db.execute(delete(StageEvent).where(StageEvent.repair_order_id.in_(ro_ids)))
    db.execute(delete(RepairOrder).where(RepairOrder.id.in_(ro_ids)))
    db.add_all([Insurer(shop_id=base.shop_id, name="Northline Insurance (Demo)"), Insurer(shop_id=base.shop_id, name="Harbor Mutual (Demo)")])
    db.commit()
    return db.get(Shop, base.shop_id)


def count(db, model, *where):
    return db.scalar(select(func.count(model.id)).where(*where))


def upload(client, data: bytes, create_missing=False):
    form = {"csrf_token": client.csrf, "action": "dry_run"}
    if create_missing:
        form["create_missing_insurers"] = "yes"
    return client.post("/import", data=form, files={"file": ("sample.csv", data, "text/csv")})


def test_t5_1_dry_run_then_commit_sample(app, db, base, phase5):
    messages_before = count(db, Message)
    with freeze_time(NOW):
        client = login_as(app, "admin@test.local")
        dry = upload(client, SAMPLE)
        assert dry.status_code == 200
        assert dry.text.count('<span class="row-ok">OK</span>') == 3
        assert count(db, RepairOrder) == 0  # nothing saved by the dry run
        upload_id = dry.text.split('name="upload_id" value="')[1].split('"')[0]
        committed = client.post("/import", data={"csrf_token": client.csrf, "action": "commit", "upload_id": upload_id})
        assert committed.status_code == 200, re.findall(r'role="alert">([^<]*)<', committed.text)
        assert "Imported 3 repair orders" in committed.text

    ros = db.scalars(select(RepairOrder).order_by(RepairOrder.ro_number)).all()
    assert [ro.ro_number for ro in ros] == ["24-1187", "24-1188", "24-1189"]
    assert [ro.original_estimate_cents for ro in ros] == [425050, 187500, 912000]
    assert [ro.current_stage for ro in ros] == [Stage.PAINT, Stage.PARTS_ORDERED, Stage.TEARDOWN]
    events = db.scalars(select(StageEvent)).all()
    assert len(events) == 3
    assert all(e.note == IMPORTED_NOTE and e.from_stage is None and e.changed_by_user_id == base.admin_id for e in events)
    assert count(db, Message) == messages_before
    assert count(db, Consent, Consent.method == ConsentMethod.IMPORTED) == 2
    assert ros[0].customer_id == base.maria_id  # existing customer reused
    assert ros[0].checked_in_at == local("2026-09-28 09:15")
    assert ros[1].checked_in_at == local("2026-09-29 08:00")  # default time
    assert ros[0].adjuster.full_name == "Dana Reyes" and ros[0].adjuster.insurer.name == "Northline Insurance (Demo)"
    assert not list(Path("import_tmp").glob("*.csv"))  # held file deleted after commit


def test_t5_2_invalid_phone_blocks_commit(app, db, base, phase5):
    bad = csv_bytes(ROW_1, ROW_2.replace("818-555-0143", "123"), ROW_3)
    results = dry_run(db, phase5, bad, False, NOW)
    assert [r.row_number for r in results if not r.ok] == [3]
    assert any("customer_phone" in e for e in results[1].errors)
    with freeze_time(NOW):
        client = login_as(app, "admin@test.local")
        dry = upload(client, bad)
        upload_id = dry.text.split('name="upload_id" value="')[1].split('"')[0]
        commit = client.post("/import", data={"csrf_token": client.csrf, "action": "commit", "upload_id": upload_id})
    assert commit.status_code == 400
    assert count(db, RepairOrder) == 0


def test_t5_3_repeated_ro_number(db, base, phase5):
    results = dry_run(db, phase5, csv_bytes(ROW_1, ROW_2.replace("24-1188", "24-1187")), False, NOW)
    assert results[0].ok
    assert not results[1].ok
    assert any("appears more than once" in e for e in results[1].errors)


def test_t5_4_too_many_rows_rejected_before_reading(db, base, phase5):
    rows = [f"X-{i},not-a-date,,,,bad,,1,,,,,BOGUS,,,,abc,,MAYBE" for i in range(1001)]
    with pytest.raises(ImportFileError, match="1,001 data rows. The limit is 1,000"):
        read_rows(csv_bytes(*rows))
    with pytest.raises(ImportFileError, match="1,000"):
        dry_run(db, phase5, csv_bytes(*rows), False, NOW)


def test_t5_5_unknown_column_rejects_file(app, db, base, phase5):
    data = csv_bytes(ROW_1 + ",x", header=HEADER + ",color2")
    with pytest.raises(ImportFileError, match="Unknown column: color2"):
        dry_run(db, phase5, data, False, NOW)
    with freeze_time(NOW):
        response = upload(login_as(app, "admin@test.local"), data)
    assert response.status_code == 422
    assert "Unknown column: color2" in response.text


def test_t5_6_imported_stage_counts_as_reached(db, base, phase5):
    admin = db.get(User, base.admin_id)
    commit_import(db, phase5, admin, SAMPLE, False, NOW)
    ro = db.scalar(select(RepairOrder).where(RepairOrder.ro_number == "24-1187"))

    def stage_updates():
        return count(db, Message, Message.repair_order_id == ro.id, Message.kind == MessageKind.STAGE_UPDATE)

    with freeze_time(local("2026-10-05 13:00")):
        change_stage(db, ro, Stage.READY_FOR_PICKUP, admin, local("2026-10-05 13:00"))
        db.commit()
    assert stage_updates() == 1
    with freeze_time(local("2026-10-05 13:05")):
        change_stage(db, ro, Stage.PAINT, admin, local("2026-10-05 13:05"))
        db.commit()
    assert stage_updates() == 1


def test_t5_7_create_missing_insurers(db, base, phase5):
    data = csv_bytes(ROW_3.replace("Harbor Mutual (Demo)", "Zeta Insurance"))
    without = dry_run(db, phase5, data, False, NOW)
    assert not without[0].ok
    assert any("Zeta Insurance does not exist" in e for e in without[0].errors)

    with_flag = dry_run(db, phase5, data, True, NOW)
    assert with_flag[0].ok
    commit_import(db, phase5, db.get(User, base.admin_id), data, True, NOW)
    zeta = db.scalar(select(Insurer).where(Insurer.name == "Zeta Insurance"))
    assert zeta is not None and zeta.shop_id == base.shop_id


def test_t5_8_delivered_stage_is_a_row_error(db, base, phase5):
    results = dry_run(db, phase5, csv_bytes(ROW_2.replace("PARTS_ORDERED", "DELIVERED")), False, NOW)
    assert not results[0].ok
    assert any("DELIVERED is not allowed" in e for e in results[0].errors)


def test_import_is_admin_only(app, base, phase5):
    with freeze_time(NOW):
        assert login_as(app, "staff@test.local").get("/import").status_code == 403


def test_held_upload_deleted_after_1_hour(app, base, phase5):
    from app.csv_import import delete_old_uploads, save_upload, upload_path

    upload_id = save_upload(SAMPLE, NOW)
    assert delete_old_uploads(local("2026-10-02 10:59")) == 0
    assert upload_path(upload_id) is not None
    assert delete_old_uploads(local("2026-10-02 11:01")) == 1
    assert upload_path(upload_id) is None
