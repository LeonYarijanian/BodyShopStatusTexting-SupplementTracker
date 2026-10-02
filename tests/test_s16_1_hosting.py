"""Section 16 item 1: hosting. Real-shop setup command, Postgres, daily backups."""

import os
import shutil
import subprocess
import sys

import pytest
from sqlalchemy import select
from sqlalchemy.engine import make_url

from app.enums import MessagingMode, Role
from app.models import Shop, ShopSettings, User
from app.seed import create_shop
from tests.conftest import ROOT, TEST_POSTGRES_URL, login_as

GOOD = {
    "name": "Glendale Auto Body",
    "phone": "(818) 555-0177",
    "timezone": "America/Los_Angeles",
    "admin_email": "Owner@GlendaleAutoBody.example",
    "admin_name": "Ana Owner",
    "admin_password": "pilot-password-2026",
}


def test_create_shop_makes_a_demo_mode_shop_with_its_first_admin(app, db):
    shop = create_shop(db, **GOOD)
    assert (shop.name, shop.phone_e164, shop.timezone) == ("Glendale Auto Body", "+18185550177", "America/Los_Angeles")
    settings = db.scalar(select(ShopSettings).where(ShopSettings.shop_id == shop.id))
    assert settings.messaging_mode == MessagingMode.DEMO
    admin = db.scalar(select(User).where(User.shop_id == shop.id))
    assert (admin.email, admin.role, admin.is_active) == ("owner@glendaleautobody.example", Role.ADMIN, True)
    page = login_as(app, "owner@glendaleautobody.example", "pilot-password-2026").get("/settings").text
    assert "Glendale Auto Body" in page


@pytest.mark.parametrize(
    "change, message",
    [
        ({"phone": "123"}, "Invalid phone number"),
        ({"timezone": "Mars/Olympus"}, "Unknown time zone"),
        ({"admin_password": "short"}, "at least 12 characters"),
        ({"admin_email": "not-an-email"}, "valid admin email"),
        ({"name": ""}, "Shop name must be"),
    ],
)
def test_create_shop_rejects_bad_input(db, change, message):
    with pytest.raises(ValueError, match=message):
        create_shop(db, **{**GOOD, **change})
    assert db.scalar(select(Shop.id)) is None


def test_create_shop_rejects_duplicates(db):
    create_shop(db, **GOOD)
    with pytest.raises(ValueError, match="already exists"):
        create_shop(db, **{**GOOD, "admin_email": "someone.else@example.com"})
    with pytest.raises(ValueError, match="already exists"):
        create_shop(db, **{**GOOD, "name": "Another Shop"})


def _cli(db_url, tmp_path, *args, password="pilot-password-2026"):
    env = {**os.environ, "DATABASE_URL": db_url, "PYTHONPATH": str(ROOT), "NEW_ADMIN_PASSWORD": password}
    return subprocess.run([sys.executable, "-m", "app.seed", *args], cwd=tmp_path, env=env, capture_output=True, text=True, timeout=120)


def test_new_shop_cli(db_url, tmp_path):
    args = ["--new-shop", "--name", "Glendale Auto Body", "--phone", "818-555-0177", "--admin-email", "ana@example.com", "--admin-name", "Ana Owner"]
    first = _cli(db_url, tmp_path, *args)
    assert first.returncode == 0, first.stderr
    assert "Created Glendale Auto Body in DEMO mode." in first.stdout
    again = _cli(db_url, tmp_path, *args)
    assert again.returncode == 1
    assert "Not created:" in again.stderr
    missing = _cli(db_url, tmp_path, "--new-shop", "--name", "X")
    assert missing.returncode == 2
    assert "--phone" in missing.stderr


@pytest.mark.skipif(not TEST_POSTGRES_URL or not shutil.which("pg_dump"), reason="needs TEST_POSTGRES_URL and pg_dump")
def test_backup_script_writes_a_restorable_dump(db_url, base, tmp_path):
    url = make_url(db_url)
    env = {
        **os.environ,
        "PGHOST": url.host or "localhost",
        "PGPORT": str(url.port or 5432),
        "PGUSER": url.username or "postgres",
        "PGPASSWORD": url.password or "",
        "PGDATABASE": url.database,
        "BACKUP_DIR": str(tmp_path / "backups"),
        "BACKUP_ONCE": "1",
    }
    result = subprocess.run(["bash", str(ROOT / "deploy" / "backup.sh")], env=env, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stderr
    dumps = list((tmp_path / "backups").glob("bodyshop-*.dump"))
    assert len(dumps) == 1
    listing = subprocess.run(["pg_restore", "--list", str(dumps[0])], capture_output=True, text=True, check=True).stdout
    assert "TABLE DATA public repair_orders" in listing
