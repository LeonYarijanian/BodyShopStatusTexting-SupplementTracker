"""Deterministic fixture data (Section 15, base fixture)."""

import datetime as dt
from functools import lru_cache
from types import SimpleNamespace

from freezegun import freeze_time
from sqlalchemy.orm import Session

from app.auth import hash_password
from app.business_days import local_to_utc
from app.enums import (
    ConsentMethod,
    ConsentStatus,
    MessageDirection,
    MessageKind,
    MessageStatus,
    PayerType,
    Role,
    Stage,
)
from app.models import (
    Adjuster,
    Consent,
    Customer,
    Insurer,
    Message,
    RepairOrder,
    Shop,
    ShopSettings,
    StageEvent,
    User,
)

TZ = "America/Los_Angeles"
PASSWORD = "test-password-123"
OTHER_SHOP_PHONE = "+18185550198"


@lru_cache
def password_hash() -> str:
    return hash_password(PASSWORD)


def local(text: str) -> dt.datetime:
    """'2026-10-05 13:00' local (America/Los_Angeles) -> aware UTC datetime."""
    return local_to_utc(dt.datetime.fromisoformat(text), TZ)


def build_base_fixture(db: Session) -> SimpleNamespace:
    with freeze_time("2026-10-02T17:00:00Z"):
        shop = Shop(name="Test Collision", phone_e164="+18185550100", timezone=TZ)
        other = Shop(name="Other Shop", phone_e164=OTHER_SHOP_PHONE, timezone=TZ)
        db.add_all([shop, other])
        db.flush()
        db.add_all([ShopSettings(shop_id=shop.id), ShopSettings(shop_id=other.id)])

        admin = User(shop_id=shop.id, email="admin@test.local", password_hash=password_hash(), full_name="Test Admin", role=Role.ADMIN)
        staff = User(shop_id=shop.id, email="staff@test.local", password_hash=password_hash(), full_name="Test Staff", role=Role.STAFF)
        other_admin = User(shop_id=other.id, email="other@test.local", password_hash=password_hash(), full_name="Other Admin", role=Role.ADMIN)
        db.add_all([admin, staff, other_admin])

        alpha = Insurer(shop_id=shop.id, name="Alpha Insurance", is_drp=True)
        beta = Insurer(shop_id=shop.id, name="Beta Insurance", is_drp=False)
        db.add_all([alpha, beta])
        db.flush()
        dana = Adjuster(shop_id=shop.id, insurer_id=alpha.id, full_name="Dana Reyes")
        sam = Adjuster(shop_id=shop.id, insurer_id=beta.id, full_name="Sam Ortiz")
        db.add_all([dana, sam])

        maria = Customer(shop_id=shop.id, first_name="Maria", last_name="Lopez", phone_e164="+18185550142")
        james = Customer(shop_id=shop.id, first_name="James", last_name="Carter", phone_e164="+18185550143")
        db.add_all([maria, james])
        db.flush()

        db.add(
            Consent(
                shop_id=shop.id,
                customer_id=maria.id,
                phone_e164=maria.phone_e164,
                status=ConsentStatus.OPTED_IN,
                method=ConsentMethod.IN_PERSON_VERBAL,
                recorded_by_user_id=admin.id,
                recorded_at=local("2026-10-01 09:00"),
            )
        )

        ro1187 = RepairOrder(
            shop_id=shop.id,
            ro_number="24-1187",
            customer_id=maria.id,
            vehicle_year=2021,
            vehicle_make="Honda",
            vehicle_model="Accord",
            payer_type=PayerType.INSURANCE,
            insurer_id=alpha.id,
            adjuster_id=dana.id,
            claim_number="CLM-55102",
            original_estimate_cents=425050,
            current_stage=Stage.CHECKED_IN,
            checked_in_at=local("2026-10-01 09:00"),
        )
        ro1188 = RepairOrder(
            shop_id=shop.id,
            ro_number="24-1188",
            customer_id=james.id,
            vehicle_year=2019,
            vehicle_make="Toyota",
            vehicle_model="Camry",
            payer_type=PayerType.CUSTOMER_PAY,
            original_estimate_cents=187500,
            current_stage=Stage.CHECKED_IN,
            checked_in_at=local("2026-10-02 09:00"),
        )
        db.add_all([ro1187, ro1188])
        db.flush()

        for ro in (ro1187, ro1188):
            db.add(
                StageEvent(
                    shop_id=shop.id,
                    repair_order_id=ro.id,
                    from_stage=None,
                    to_stage=Stage.CHECKED_IN,
                    changed_by_user_id=admin.id,
                    changed_at=ro.checked_in_at,
                )
            )

        checkin_text = Message(
            shop_id=shop.id,
            repair_order_id=ro1187.id,
            customer_id=maria.id,
            direction=MessageDirection.OUTBOUND,
            kind=MessageKind.STAGE_UPDATE,
            status=MessageStatus.DELIVERED,
            to_e164=maria.phone_e164,
            from_e164=shop.phone_e164,
            body=(
                "Hi Maria, this is Test Collision. Your 2021 Honda Accord is checked in (RO 24-1187). "
                "We'll text you as the repair moves along. Reply STOP to opt out, HELP for help."
            ),
            stage=Stage.CHECKED_IN,
            scheduled_send_at=local("2026-10-01 09:10"),
            sent_at=local("2026-10-01 09:10"),
            provider_message_id="demo-00000000-0000-4000-8000-000000000001",
        )
        db.add(checkin_text)
        db.commit()

    return SimpleNamespace(
        shop_id=shop.id,
        other_shop_id=other.id,
        admin_id=admin.id,
        staff_id=staff.id,
        other_admin_id=other_admin.id,
        alpha_id=alpha.id,
        beta_id=beta.id,
        dana_id=dana.id,
        sam_id=sam.id,
        maria_id=maria.id,
        james_id=james.id,
        ro1187_id=ro1187.id,
        ro1188_id=ro1188.id,
        checkin_message_id=checkin_text.id,
    )
