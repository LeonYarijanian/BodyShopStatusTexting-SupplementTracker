import os
import re
import shutil
import uuid
from pathlib import Path

os.environ["APP_SECRET_KEY"] = "test-secret-key-0123456789abcdef0123456789abcdef"
os.environ["SCHEDULER_ENABLED"] = "false"
os.environ["ALLOW_LIVE_SMS"] = "false"

import pytest  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402

from app.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402
from tests.fixtures import PASSWORD, build_base_fixture  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
_CSRF_RE = re.compile(r'name="csrf-token" content="([^"]+)"')


def run_migrations(database_url: str) -> None:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")


# Set TEST_POSTGRES_URL (for example postgresql+psycopg://postgres@localhost:5432/postgres) to run every
# test against Postgres instead of SQLite. Each test then gets its own database, copied from a migrated template.
TEST_POSTGRES_URL = os.environ.get("TEST_POSTGRES_URL", "")
PG_TEMPLATE = "bodyshop_test_template"


def _pg_admin():
    return create_engine(TEST_POSTGRES_URL, isolation_level="AUTOCOMMIT")


def _pg_url(database: str) -> str:
    return make_url(TEST_POSTGRES_URL).set(database=database).render_as_string(hide_password=False)


def _pg_create(database: str, template: str | None = None) -> str:
    engine = _pg_admin()
    with engine.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{database}" WITH (FORCE)'))
        conn.execute(text(f'CREATE DATABASE "{database}"' + (f' TEMPLATE "{template}"' if template else "")))
    engine.dispose()
    return _pg_url(database)


def _pg_drop(database: str) -> None:
    engine = _pg_admin()
    with engine.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{database}" WITH (FORCE)'))
    engine.dispose()


@pytest.fixture(scope="session")
def migrated_template(tmp_path_factory):
    if TEST_POSTGRES_URL:
        url = _pg_create(PG_TEMPLATE)
        run_migrations(url)
        # A template database must have no open connections when it is copied.
        create_engine(url).dispose()
        yield PG_TEMPLATE
        _pg_drop(PG_TEMPLATE)
        return
    path = tmp_path_factory.mktemp("template") / "template.db"
    run_migrations(f"sqlite:///{path}")
    yield path


@pytest.fixture
def db_url(tmp_path, migrated_template):
    if TEST_POSTGRES_URL:
        name = f"bodyshop_test_{uuid.uuid4().hex[:12]}"
        yield _pg_create(name, template=PG_TEMPLATE)
        _pg_drop(name)
        return
    path = tmp_path / "test.db"
    shutil.copy(migrated_template, path)
    yield f"sqlite:///{path}"


@pytest.fixture
def empty_db_url(tmp_path):
    """A brand-new database with no tables (SQLite file or Postgres database)."""
    if TEST_POSTGRES_URL:
        name = f"bodyshop_empty_{uuid.uuid4().hex[:12]}"
        yield _pg_create(name)
        _pg_drop(name)
        return
    yield f"sqlite:///{tmp_path / 'empty.db'}"


def make_settings(db_url: str, **overrides) -> Settings:
    values = {
        "APP_SECRET_KEY": os.environ["APP_SECRET_KEY"],
        "DATABASE_URL": db_url,
        "SCHEDULER_ENABLED": False,
        "ALLOW_LIVE_SMS": False,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


@pytest.fixture
def settings(db_url) -> Settings:
    return make_settings(db_url)


@pytest.fixture
def app(settings):
    app = create_app(settings)
    yield app
    app.state.engine.dispose()


@pytest.fixture
def db(app):
    session = app.state.SessionLocal()
    yield session
    session.close()


@pytest.fixture
def base(db):
    return build_base_fixture(db)


@pytest.fixture
def client(app):
    return TestClient(app)


def csrf_from(html: str) -> str:
    match = _CSRF_RE.search(html)
    assert match, "page has no csrf-token meta tag"
    return match.group(1)


def get_csrf(client: TestClient, path: str = "/login") -> str:
    return csrf_from(client.get(path).text)


def login(client: TestClient, email: str, password: str = PASSWORD) -> str:
    """Log in and return the CSRF token of the new session."""
    token = get_csrf(client, "/login")
    response = client.post("/login", data={"email": email, "password": password, "csrf_token": token}, follow_redirects=False)
    assert response.status_code == 302, response.text
    return get_csrf(client, "/")


def login_as(app, email: str, password: str = PASSWORD) -> TestClient:
    """A new client logged in at the current (possibly frozen) time; `client.csrf` holds its token.

    Uses https so the Secure session cookie (set when PUBLIC_BASE_URL is https) is sent back.
    """
    client = TestClient(app, base_url="https://testserver")
    client.csrf = login(client, email, password)
    return client


@pytest.fixture
def admin_client(client, base):
    client.csrf = login(client, "admin@test.local")
    return client


@pytest.fixture
def staff_client(app, base):
    c = TestClient(app)
    c.csrf = login(c, "staff@test.local")
    return c
