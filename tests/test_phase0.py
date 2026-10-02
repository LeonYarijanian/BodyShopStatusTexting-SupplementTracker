"""Phase 0: scaffold, database, login."""

import os
import re
import sqlite3
import subprocess
import sys

import pytest
from fastapi.testclient import TestClient
from freezegun import freeze_time

from app.money import dollars_to_cents, format_cents
from tests.conftest import ROOT, get_csrf, login, run_migrations
from tests.fixtures import PASSWORD

SECTION_5_TABLES = {
    "shops",
    "shop_settings",
    "users",
    "login_attempts",
    "customers",
    "insurers",
    "adjusters",
    "repair_orders",
    "stage_events",
    "consents",
    "messages",
    "supplements",
    "supplement_events",
}


def test_t0_1_migrations_create_exactly_the_13_tables(tmp_path):
    path = tmp_path / "empty.db"
    run_migrations(f"sqlite:///{path}")
    with sqlite3.connect(path) as conn:
        names = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
    names.discard("alembic_version")
    assert names == SECTION_5_TABLES
    assert len(names) == 13


def test_t0_2_login_right_and_wrong_password(client, base):
    token = get_csrf(client)
    wrong = client.post("/login", data={"email": "admin@test.local", "password": "not-the-password", "csrf_token": token}, follow_redirects=False)
    assert wrong.status_code == 200
    assert "Email or password is incorrect." in wrong.text

    right = client.post("/login", data={"email": "admin@test.local", "password": PASSWORD, "csrf_token": token}, follow_redirects=False)
    assert right.status_code == 302
    assert right.headers["location"] == "/"


def test_t0_3_lockout_after_10_failures(client, base):
    with freeze_time("2026-10-05T17:00:00Z") as frozen:
        token = get_csrf(client)
        for _ in range(10):
            r = client.post("/login", data={"email": "staff@test.local", "password": "wrong-password!", "csrf_token": token}, follow_redirects=False)
            assert r.status_code == 200
            assert "Email or password is incorrect." in r.text

        eleventh = client.post("/login", data={"email": "staff@test.local", "password": PASSWORD, "csrf_token": token}, follow_redirects=False)
        assert eleventh.status_code == 200
        assert "Too many attempts. Try again in 15 minutes." in eleventh.text

        frozen.move_to("2026-10-05T17:15:01Z")  # 15 min 1 s after the 10th failure
        later = client.post("/login", data={"email": "staff@test.local", "password": PASSWORD, "csrf_token": token}, follow_redirects=False)
        assert later.status_code == 302


def test_t0_4_settings_is_admin_only(app, base):
    staff = TestClient(app)
    login(staff, "staff@test.local")
    assert staff.get("/settings").status_code == 403

    admin = TestClient(app)
    login(admin, "admin@test.local")
    assert admin.get("/settings").status_code == 200


def test_t0_5_other_shop_record_returns_404(client, base):
    login(client, "other@test.local")
    assert client.get(f"/ro/{base.ro1187_id}").status_code == 404


def test_t0_6_money_helpers():
    assert dollars_to_cents("4,250.50") == 425050
    assert dollars_to_cents("$1,800") == 180000
    with pytest.raises(ValueError):
        dollars_to_cents("12.345")
    assert format_cents(317050) == "$3,170.50"


def test_t0_7_app_exits_1_without_secret_key(tmp_path):
    env = {k: v for k, v in os.environ.items() if k != "APP_SECRET_KEY"}
    env["PYTHONPATH"] = str(ROOT)
    env["DATABASE_URL"] = f"sqlite:///{tmp_path / 'x.db'}"
    result = subprocess.run(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--port", "0"],
        cwd=tmp_path,  # no .env here
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 1
    assert "APP_SECRET_KEY" in result.stderr


def _walk_routes(routes):
    for route in routes:
        nested = getattr(route, "original_router", None)
        if nested is not None:
            yield from _walk_routes(nested.routes)
        else:
            yield route


def _post_routes(app):
    paths = []
    for route in _walk_routes(app.routes):
        methods = getattr(route, "methods", None) or set()
        if "POST" in methods and not route.path.startswith("/webhooks/"):
            paths.append(re.sub(r"\{[a-z_]+\}", "1", route.path))
    return paths


def test_t0_8_post_without_csrf_token_is_403(app, base):
    anonymous = TestClient(app)
    assert anonymous.post("/login", data={"email": "admin@test.local", "password": PASSWORD}, follow_redirects=False).status_code == 403

    client = TestClient(app)
    token = login(client, "admin@test.local")
    paths = _post_routes(app)
    assert "/login" in paths and "/logout" in paths
    for path in paths:
        response = client.post(path, data={"anything": "x"}, follow_redirects=False)
        assert response.status_code == 403, path
        wrong = client.post(path, data={"csrf_token": "wrong-token"}, follow_redirects=False)
        assert wrong.status_code == 403, path
    # The real token is accepted.
    assert client.post("/logout", data={"csrf_token": token}, follow_redirects=False).status_code == 302
