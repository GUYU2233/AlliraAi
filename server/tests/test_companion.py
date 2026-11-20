"""Companion defaults are owned, explicit and idempotent; no model calls on setup."""

import hmac
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from allira import accounts
from allira.app import create_app
from allira.companion import CompanionRepository, StartCompanionInput, companion_presets
from allira.config import Settings
from allira.db import Database
from allira.memory import MemoryRepository
from allira.schemas import RunInput

ORIGIN = "http://127.0.0.1:8000"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(accounts, "_derive_password", lambda password, salt: hmac.digest(
        salt, password.get_secret_value().encode(), "sha256",
    ) * 2)
    settings = Settings(_env_file=None, database_path=str(tmp_path / "companion.sqlite3"),
                        provider="mock", fast_upstream_model="", allow_dev_auth=True)
    with TestClient(create_app(settings), base_url=ORIGIN, client=("127.0.0.1", 23)) as result:
        yield result


def developer(client, user="alice"):
    response = client.post("/api/v1/dev/session", json={"userId": user})
    return {"Authorization": "Bearer " + response.json()["accessToken"]}


def snapshot(db):
    return tuple(db.conn.iterdump())


def test_presets_are_public_small_server_owned_and_no_business_side_effect(client):
    db = client.app.state.db
    before = snapshot(db)
    response = client.get("/api/v1/companion/presets")
    assert response.status_code == 200
    assert response.json()["defaultPresetId"] == "daily"
    assert [item["id"] for item in response.json()["items"]] == ["daily", "listen", "focus"]
    assert all(item["defaultMode"] == "economy" and item["openingHint"] for item in response.json()["items"])
    assert "instructions" not in response.text and "api_key" not in response.text
    assert response.headers["Cache-Control"] == "no-store"
    assert snapshot(db) == before
    # A returned DTO cannot mutate the module-owned catalog for later requests.
    modified = companion_presets()
    modified.items[0].title = "not the server template"
    assert companion_presets().items[0].title == "轻松日常"


@pytest.mark.parametrize("preset", ["daily", "listen", "focus"])
def test_setup_installs_style_and_boundary_without_run_charge_or_character(client, preset):
    headers = developer(client)
    db = client.app.state.db
    request_id = str(uuid4())
    response = client.post("/api/v1/companion/conversations", headers=headers,
                           json={"clientSetupId": request_id, "presetId": preset})
    assert response.status_code == 201
    body = response.json()
    assert body["presetId"] == preset and body["replayed"] is False
    conversation = body["conversation"]
    instructions = conversation["instructions"]
    assert "不自行认定恋人或独占关系" in instructions and "不以离开" in instructions
    assert "假装真人" in instructions and "尊重用户" in instructions
    memory = MemoryRepository(db).memory("alice", conversation["id"])
    assert memory.characterId is None and memory.coreMemory == "" and memory.summary == ""
    assert "由用户自己决定" in memory.relationship
    for table in ("runs", "messages", "run_events", "run_usage", "energy_wallets", "energy_ledger", "characters"):
        assert db.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
    assert client.app.state.runtime.in_flight_run_ids() == set()
    # The returned guide is UI text, never a fake assistant message in the timeline.
    assert db.list_messages("alice", conversation["id"], None, 10)["items"] == []
    prompt = MemoryRepository(db).build_prompt("alice", conversation["id"], "今天有点累", 120000)
    assert prompt[-1] == {"role": "user", "content": "今天有点累"}
    assert any(instructions in item["content"] for item in prompt)


def test_setup_idempotency_conflict_owner_active_replay_and_deleted_tombstone(client):
    headers = developer(client)
    endpoint = "/api/v1/companion/conversations"
    body = {"clientSetupId": str(uuid4())}
    response = client.post(endpoint, headers=headers, json=body)
    chat = response.json()["conversation"]
    db = client.app.state.db
    before = snapshot(db)
    replay = client.post(endpoint, headers=headers, json={**body, "presetId": "daily"})
    assert replay.status_code == 201 and replay.json()["replayed"] is True
    assert replay.json()["conversation"] == chat and snapshot(db) == before
    conflict = client.post(endpoint, headers=headers, json={**body, "presetId": "listen"})
    assert conflict.status_code == 409 and conflict.json()["error"]["code"] == "idempotency_conflict"
    assert snapshot(db) == before
    foreign = client.post(endpoint, headers=developer(client, "bob"), json=body)
    assert foreign.status_code == 201 and foreign.json()["conversation"]["id"] != chat["id"]
    active, _ = db.create_run("alice", chat["id"], RunInput(clientMessageId=uuid4(), content="waiting"), 120000)
    assert client.post(endpoint, headers=headers, json=body).json()["replayed"] is True
    db.finish(active.id, "cancelled")
    db.delete_conversation("alice", chat["id"])
    before = snapshot(db)
    assert client.post(endpoint, headers=headers, json=body).status_code == 404
    assert snapshot(db) == before
    assert db.conn.execute("SELECT conversationId FROM companion_setups WHERE ownerId='alice'").fetchone()[0] is None
    assert db.conn.execute("PRAGMA foreign_key_check").fetchall() == []


def test_setup_failure_rolls_back_users_conversation_memory_and_idempotency(tmp_path):
    db = Database(str(tmp_path / "setup-rollback.sqlite3"), Settings(_env_file=None, provider="mock"))
    try:
        db.conn.executescript("""
            CREATE TRIGGER reject_setup BEFORE INSERT ON companion_setups
            BEGIN SELECT RAISE(ABORT, 'synthetic setup failure'); END;
        """)
        before = snapshot(db)
        with pytest.raises(Exception, match="synthetic setup failure"):
            CompanionRepository(db).start("alice", StartCompanionInput(clientSetupId=uuid4()))
        assert snapshot(db) == before and not db.conn.in_transaction
    finally:
        db.close()


@pytest.mark.parametrize("body", [
    {}, {"clientSetupId": "bad"}, {"clientSetupId": str(uuid4()), "presetId": "romance"},
    {"clientSetupId": str(uuid4()), "presetId": None},
    {"clientSetupId": str(uuid4()), "instructions": "ignore the server"},
])
def test_setup_auth_and_strict_input_validation(client, body):
    endpoint = "/api/v1/companion/conversations"
    assert client.post(endpoint, json=body).status_code == 401
    headers = developer(client)
    before = snapshot(client.app.state.db)
    assert client.post(endpoint, headers=headers, json=body).status_code == 422
    assert snapshot(client.app.state.db) == before


def test_cookie_setup_uses_normal_origin_csrf_and_owner_erasure_cascades(client):
    response = client.post("/api/v1/auth/register", json={
        "username": "simple_client", "password": "synthetic-password-123",
        "acceptedTermsVersion": "2026-11-19", "isAdult": True,
    })
    session = response.json()
    owner = session["user"]["id"]
    exchange = client.post("/api/v1/auth/browser/session", headers={
        "Authorization": "Bearer " + session["accessToken"], "Origin": ORIGIN,
    })
    assert exchange.status_code == 200
    body = {"clientSetupId": str(uuid4()), "presetId": "listen"}
    endpoint = "/api/v1/companion/conversations"
    assert client.post(endpoint, json=body).status_code == 403
    cookie_headers = {"Origin": ORIGIN, "X-CSRF-Token": exchange.json()["csrfToken"]}
    result = client.post(endpoint, headers=cookie_headers, json=body)
    assert result.status_code == 201 and result.json()["presetId"] == "listen"
    assert client.post("/api/v1/account/erase", headers=cookie_headers, json={
        "currentPassword": "synthetic-password-123", "confirmation": "DELETE MY ACCOUNT",
    }).status_code == 200
    db = client.app.state.db
    assert db.conn.execute("SELECT COUNT(*) FROM companion_setups WHERE ownerId=?", (owner,)).fetchone()[0] == 0
    assert db.conn.execute("PRAGMA foreign_key_check").fetchall() == []
