"""Section 16 item 7: AI-drafted adjuster follow-up emails that staff approve before sending.

The Claude client is always mocked; tests never call the real API.
"""

import json
from email import message_from_bytes
from email.policy import default as default_policy
from pathlib import Path
from types import SimpleNamespace

import pytest
from freezegun import freeze_time
from sqlalchemy import select

from app import ai_drafts
from app.ai_drafts import MODEL, DraftError, create_draft
from app.enums import AdjusterEmailStatus, DraftSource, FollowUpMethod, SupplementEventType, SupplementStatus
from app.main import create_app
from app.models import AdjusterEmail, Adjuster, RepairOrder, Supplement, SupplementEvent, User
from app.supplements import create_supplement, transition
from tests.conftest import login_as, make_settings
from tests.fixtures import local

NOW = local("2026-10-09 12:00")
GOOD = {
    "subject": "Supplement S1 for claim CLM-55102 (RO 24-1187)",
    "body": (
        "Hi Dana,\n\nI'm following up on supplement S1 for claim CLM-55102, the 2021 Honda Accord, RO 24-1187. "
        "We submitted it on Mon, Oct 5 for $1,800.00 and it has been waiting 4 business days. "
        "Could you let us know where it stands, or whether you need anything else from us?\n\n"
        "Thank you,\nTest Staff\nTest Collision\n(818) 555-0100"
    ),
}


class FakeClaude:
    """Stands in for anthropic.Anthropic(); records each beta.messages.create call."""

    def __init__(self, reply=None, stop_reason="end_turn", error=None):
        self.calls = []
        self.reply, self.stop_reason, self.error = reply, stop_reason, error
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        content = [SimpleNamespace(type="text", text=json.dumps(self.reply))] if self.reply is not None else []
        return SimpleNamespace(stop_reason=self.stop_reason, content=content)


@pytest.fixture
def ai_app(db_url):
    app = create_app(make_settings(db_url, ANTHROPIC_API_KEY="sk-ant-test"))
    yield app
    app.state.engine.dispose()


@pytest.fixture
def claude(monkeypatch):
    fake = FakeClaude(reply=GOOD)
    monkeypatch.setattr(ai_drafts, "make_anthropic_client", lambda api_key: fake)
    return fake


@pytest.fixture
def supplement(db, base):
    db.get(Adjuster, base.dana_id).email = "dana.reyes@alpha.example"
    ro = db.get(RepairOrder, base.ro1187_id)
    s = create_supplement(db, ro, "Hidden damage: RF apron", 180000, base.admin_id, local("2026-10-05 09:00"))
    transition(db, s, SupplementStatus.SUBMITTED, base.admin_id, local("2026-10-05 10:00"))
    db.commit()
    return s


def test_claude_drafts_from_the_facts(ai_app, db, base, supplement, claude):
    with freeze_time(NOW):
        client = login_as(ai_app, "staff@test.local")
        response = client.post(f"/supplements/{supplement.id}/email-draft", data={"csrf_token": client.csrf}, follow_redirects=False)
        assert response.status_code == 303
        page = client.get(response.headers["location"]).text
    email = db.scalar(select(AdjusterEmail))
    assert (email.source, email.model, email.status) == (DraftSource.AI, MODEL, AdjusterEmailStatus.DRAFT)
    assert email.to_email == "dana.reyes@alpha.example"
    assert email.subject == GOOD["subject"] and email.body == GOOD["body"]
    assert "Drafted by Claude" in page

    (call,) = claude.calls
    assert call["model"] == "claude-opus-5-5"
    assert call["fallbacks"] == "default" and call["betas"] == ["server-side-fallback-2026-07-01"]
    assert call["output_config"]["format"]["type"] == "json_schema"
    facts = call["messages"][0]["content"]
    for fact in ("CLM-55102", "24-1187", "$1,800.00", "Mon, Oct 5", "4 business days", '"adjuster_first_name": "Dana"', "Test Staff"):
        assert fact in facts
    # Customer details never go to the model.
    assert "Maria" not in facts and "+18185550142" not in facts


@pytest.mark.parametrize(
    "fake",
    [
        FakeClaude(reply=None, stop_reason="refusal"),
        FakeClaude(error=RuntimeError("network down")),
        FakeClaude(reply={**GOOD, "body": GOOD["body"].replace("$1,800.00", "$1,900.00")}),  # wrong amount
        FakeClaude(reply={**GOOD, "body": GOOD["body"] + " Otherwise we will take legal action."}),
        FakeClaude(reply={"subject": "x"}),  # missing body
    ],
    ids=["refusal", "error", "wrong-amount", "threat", "bad-json"],
)
def test_falls_back_to_the_template(ai_app, db, base, supplement, monkeypatch, fake):
    monkeypatch.setattr(ai_drafts, "make_anthropic_client", lambda api_key: fake)
    with freeze_time(NOW):
        email = create_draft(db, supplement, db.get(User, base.staff_id), NOW, ai_app.state.settings)
    assert email.source == DraftSource.TEMPLATE and email.model is None
    assert email.subject == "Supplement S1 for claim CLM-55102 (RO 24-1187) - 4 business days"
    assert "We submitted it on Mon, Oct 5 for $1,800.00, and it has been waiting 4 business days." in email.body


def test_no_api_key_uses_the_template_without_calling_claude(app, db, base, supplement, claude):
    with freeze_time(NOW):
        email = create_draft(db, supplement, db.get(User, base.staff_id), NOW, app.state.settings)
    assert email.source == DraftSource.TEMPLATE
    assert claude.calls == []


def test_nothing_is_sent_until_approved_then_it_logs_a_follow_up(ai_app, db, base, supplement, claude, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with freeze_time(NOW):
        client = login_as(ai_app, "staff@test.local")
        location = client.post(f"/supplements/{supplement.id}/email-draft", data={"csrf_token": client.csrf}, follow_redirects=False).headers["location"]
        assert not Path("outbox").exists()  # drafting sends nothing
        edited = GOOD["body"].replace("Could you", "When you have a moment, could you")
        saved = client.post(location, data={"csrf_token": client.csrf, "action": "save", "to_email": "dana.reyes@alpha.example", "subject": GOOD["subject"], "body": edited}, follow_redirects=False)
        assert saved.status_code == 303 and not Path("outbox").exists()
        sent = client.post(location, data={"csrf_token": client.csrf, "action": "send", "to_email": "dana.reyes@alpha.example", "subject": GOOD["subject"], "body": edited}, follow_redirects=False)
        assert sent.status_code == 303
    email = db.scalar(select(AdjusterEmail))
    assert email.status == AdjusterEmailStatus.SENT and email.sent_by_user_id == base.staff_id and email.sent_at == NOW
    (eml,) = list(Path("outbox").glob("*.eml"))
    message = message_from_bytes(eml.read_bytes(), policy=default_policy)
    assert message["To"] == "dana.reyes@alpha.example"
    assert message["Reply-To"] == "staff@test.local"
    assert "When you have a moment" in message.get_content()
    db.refresh(supplement)
    assert supplement.follow_up_count == 1
    event = db.scalar(select(SupplementEvent).where(SupplementEvent.event_type == SupplementEventType.FOLLOW_UP))
    assert event.follow_up_method == FollowUpMethod.EMAIL


def test_edit_checks_and_missing_address(ai_app, db, base, supplement, claude):
    with freeze_time(NOW):
        client = login_as(ai_app, "staff@test.local")
        location = client.post(f"/supplements/{supplement.id}/email-draft", data={"csrf_token": client.csrf}, follow_redirects=False).headers["location"]
        threat = client.post(location, data={"csrf_token": client.csrf, "action": "save", "to_email": "dana.reyes@alpha.example", "subject": "S1", "body": "Pay or we sue."})
        assert threat.status_code == 422 and "Remove &#34;sue&#34;" in threat.text
        no_address = client.post(location, data={"csrf_token": client.csrf, "action": "send", "to_email": "", "subject": "S1", "body": "Checking in."})
        assert no_address.status_code == 422 and "Add the adjuster" in no_address.text
        # A second click reuses the open draft instead of asking Claude again.
        again = client.post(f"/supplements/{supplement.id}/email-draft", data={"csrf_token": client.csrf}, follow_redirects=False)
        assert again.headers["location"] == location
    assert len(claude.calls) == 1
    assert db.scalar(select(AdjusterEmail)).status == AdjusterEmailStatus.DRAFT


def test_only_for_submitted_supplements_and_other_shops_get_404(ai_app, db, base, supplement, claude):
    with freeze_time(NOW):
        transition(db, supplement, SupplementStatus.APPROVED, base.admin_id, NOW)
        db.commit()
        with pytest.raises(DraftError):
            create_draft(db, supplement, db.get(User, base.staff_id), NOW, ai_app.state.settings)
        other = login_as(ai_app, "other@test.local")
        assert other.post(f"/supplements/{supplement.id}/email-draft", data={"csrf_token": other.csrf}).status_code == 404
    assert db.scalar(select(Supplement.status)) == SupplementStatus.APPROVED


def test_real_sdk_sends_the_expected_request(ai_app, db, base, supplement, monkeypatch):
    """The actual anthropic SDK, with its HTTP transport replaced, so the wire format is checked without a network call."""
    import anthropic
    import httpx2

    seen = {}

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen["url"] = str(request.url)
        seen["headers"] = dict(request.headers)
        seen["body"] = json.loads(request.content)
        return httpx2.Response(
            200,
            json={
                "id": "msg_test",
                "type": "message",
                "role": "assistant",
                "model": "claude-opus-5-5",
                "content": [{"type": "text", "text": json.dumps(GOOD)}],
                "stop_reason": "end_turn",
                "stop_sequence": None,
                "usage": {"input_tokens": 300, "output_tokens": 120},
            },
        )

    def make_client(api_key):
        return anthropic.Anthropic(api_key=api_key, http_client=anthropic.DefaultHttpxClient(transport=httpx2.MockTransport(handler)))

    monkeypatch.setattr(ai_drafts, "make_anthropic_client", make_client)
    # No freeze_time here: freezegun's fake datetime breaks pydantic schema building inside the SDK.
    email = create_draft(db, supplement, db.get(User, base.staff_id), NOW, ai_app.state.settings)
    assert email.source == DraftSource.AI
    assert seen["url"].endswith("/v1/messages?beta=true") or seen["url"].endswith("/v1/messages")
    assert seen["headers"]["x-api-key"] == "sk-ant-test"
    assert "server-side-fallback-2026-07-01" in seen["headers"]["anthropic-beta"]
    body = seen["body"]
    assert body["model"] == "claude-opus-5-5" and body["fallbacks"] == "default"
    assert body["output_config"] == {"effort": "low", "format": {"type": "json_schema", "schema": ai_drafts.OUTPUT_SCHEMA}}
    assert "thinking" not in body and "temperature" not in body
