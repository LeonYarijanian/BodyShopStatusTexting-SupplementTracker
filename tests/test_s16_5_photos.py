"""Section 16 item 5: photo updates by picture message (MMS)."""

import datetime as dt
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from freezegun import freeze_time
from sqlalchemy import func, select

from app.enums import MessageKind, MessageStatus, MessagingMode
from app.main import create_app
from app.media import MAX_PHOTO_BYTES, delete_old_media
from app.messaging import providers
from app.messaging.engine import settings_for
from app.models import Customer, Message, RepairOrder
from app.privacy import anonymize_customer
from tests.conftest import login_as, make_settings
from tests.fixtures import local

NOW = local("2026-10-05 13:00")
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 2000
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 500


@pytest.fixture
def in_tmp(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # ./media lives in the test's folder
    return tmp_path


def send_photo(client, ro_id, data, filename="paint.jpg", body="Your car in the paint booth today."):
    return client.post(
        f"/ro/{ro_id}/messages",
        data={"csrf_token": client.csrf, "body": body},
        files={"photo": (filename, data, "image/jpeg")},
        follow_redirects=False,
    )


def _manual(db):
    return db.scalar(select(Message).where(Message.kind == MessageKind.MANUAL))


def test_photo_text_in_demo(app, db, base, in_tmp, capsys):
    with freeze_time(NOW):
        client = login_as(app, "staff@test.local")
        assert send_photo(client, base.ro1187_id, JPEG).status_code == 303
        message = _manual(db)
        assert message.status == MessageStatus.DELIVERED
        assert message.media_content_type == "image/jpeg" and len(message.media_token) == 32
        assert Path("media", f"{message.media_token}.jpg").read_bytes() == JPEG
        out = capsys.readouterr().out
        assert f"[DEMO MMS] to=+1818***0142 body=Test Collision: Your car in the paint booth today. media=/media/{message.media_token}" in out

        public = TestClient(app).get(f"/media/{message.media_token}")  # no login: Twilio fetches it
        assert public.status_code == 200
        assert public.content == JPEG and public.headers["content-type"] == "image/jpeg"
        assert public.headers["x-content-type-options"] == "nosniff"
        assert f'src="/media/{message.media_token}"' in client.get(f"/ro/{base.ro1187_id}").text
    assert TestClient(app).get("/media/" + "A" * 32).status_code == 404
    assert TestClient(app).get("/media/../../etc/passwd").status_code == 404


@pytest.mark.parametrize(
    "data, error",
    [(b"%PDF-1.7 not an image", "must be a JPEG, PNG or GIF"), (b"\xff\xd8\xff" + b"\x00" * MAX_PHOTO_BYTES, "5 MB or smaller")],
)
def test_bad_photos_are_rejected(app, db, base, in_tmp, data, error):
    with freeze_time(NOW):
        client = login_as(app, "staff@test.local")
        send_photo(client, base.ro1187_id, data)
        assert error in client.get(f"/ro/{base.ro1187_id}").text
    assert db.scalar(select(func.count(Message.id)).where(Message.kind == MessageKind.MANUAL)) == 0
    assert not Path("media").exists() or not list(Path("media").glob("*"))


def test_live_picture_message_passes_the_public_url_to_twilio(db_url, db, base, in_tmp, monkeypatch):
    client_mock = MagicMock()
    client_mock.messages.create.return_value = SimpleNamespace(sid="SM" + "9" * 32)
    monkeypatch.setattr(providers, "make_twilio_client", lambda sid, token: client_mock)
    live = create_app(
        make_settings(db_url, ALLOW_LIVE_SMS=True, TWILIO_ACCOUNT_SID="AC" + "0" * 32, TWILIO_AUTH_TOKEN="t", PUBLIC_BASE_URL="https://shop.example.com")
    )
    shop_settings = settings_for(db, base.shop_id)
    shop_settings.messaging_mode = MessagingMode.LIVE
    shop_settings.twilio_from_e164 = "+18185550188"
    db.commit()
    with freeze_time(NOW):
        client = login_as(live, "staff@test.local")
        send_photo(client, base.ro1187_id, PNG, filename="before.png")
    message = _manual(db)
    assert message.status == MessageStatus.SENT
    kwargs = client_mock.messages.create.call_args.kwargs
    assert kwargs["media_url"] == [f"https://shop.example.com/media/{message.media_token}"]
    live.state.engine.dispose()


def test_photos_deleted_after_30_days_and_with_customer_data(app, db, base, in_tmp):
    with freeze_time(NOW):
        client = login_as(app, "staff@test.local")
        send_photo(client, base.ro1187_id, JPEG)
        send_photo(client, base.ro1187_id, PNG, filename="after.png")
    first, second = db.scalars(select(Message).where(Message.kind == MessageKind.MANUAL).order_by(Message.id)).all()
    first.created_at = NOW - dt.timedelta(days=31)
    db.commit()
    assert delete_old_media(NOW, db) == 1
    db.refresh(first)
    assert first.media_token is None and len(list(Path("media").glob("*"))) == 1

    anonymize_customer(db, db.get(Customer, base.maria_id), NOW)
    db.commit()
    db.refresh(second)
    assert second.media_token is None
    assert list(Path("media").glob("*")) == []
    assert db.get(RepairOrder, base.ro1187_id) is not None
