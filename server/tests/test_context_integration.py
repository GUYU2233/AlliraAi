"""Context-memory integration uses isolated SQLite/ASGI and synthetic providers only."""

import hmac
import json
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from allira import accounts, conversation_tools, data_controls
from allira.app import create_app
from allira.config import Settings
from allira.conversation_tools import ConversationForkInput, ConversationToolsRepository
from allira.db import Database
from allira.memory import MemoryRepository, MemoryUpdate
from allira.schemas import ConversationInput, RunInput

ORIGIN = "http://127.0.0.1:8000"
PASSWORD = "synthetic-context-password-123"


class RecordingProvider:
    def __init__(self):
        self.prompts = []

    async def stream(self, messages):
        self.prompts.append([dict(message) for message in messages])
        yield "synthetic reply"


@pytest.fixture
def client(tmp_path, monkeypatch):
    # Fast deterministic derivation is fixture-only, not the production password KDF.
    monkeypatch.setattr(accounts, "_derive_password", lambda password, salt: hmac.digest(
        salt, password.get_secret_value().encode(), "sha256",
    ) * 2)
    settings = Settings(
        _env_file=None, database_path=str(tmp_path / "context.sqlite3"), provider="mock",
        fast_upstream_model="", allow_dev_auth=True, admin_enabled=True,
        context_recent_turns=1, summary_max_characters=6000,
    )
    provider = RecordingProvider()
    with TestClient(create_app(settings, provider), base_url=ORIGIN,
                    client=("127.0.0.1", 12)) as result:
        result.recording_provider = provider
        yield result


def developer(client, name="alice"):
    session = client.post("/api/v1/dev/session", json={"userId": name}).json()
    return {"Authorization": "Bearer " + session["accessToken"]}


def register(client, name):
    response = client.post("/api/v1/auth/register", json={
        "username": name, "password": PASSWORD, "isAdult": True,
        "acceptedTermsVersion": "2026-11-19",
    })
    assert response.status_code == 201
    session = response.json()
    return session, {"Authorization": "Bearer " + session["accessToken"]}


def completed(db, owner, conversation_id, question, answer):
    run, _ = db.create_run(owner, conversation_id, RunInput(
        clientMessageId=uuid4(), content=question,
    ), 120000, recent_turns=1)
    db.append_delta(run.id, answer)
    db.finish(run.id, "completed")
    return run


def card(client, conversation_id, headers, *, source=None, enabled=True):
    response = client.post(f"/api/v1/conversations/{conversation_id}/memory/cards", headers=headers,
                           json={"category": "boundary", "title": "人工边界",
                                 "content": "不替用户推断关系 <script>not executable</script>",
                                 "sourceMessageId": source, "enabled": enabled})
    assert response.status_code == 201
    return response.json()


def snapshot(db):
    return tuple(db.conn.iterdump())


def test_preview_rolls_back_pending_blocks_and_matches_actual_provider_prompt(client):
    headers = developer(client)
    db = client.app.state.db
    chat = db.create_conversation("alice", ConversationInput(instructions="role instructions"))
    first = completed(db, "alice", chat.id, "first original question", "first original answer")
    completed(db, "alice", chat.id, "second question", "second answer")
    created = card(client, chat.id, headers, source=first.userMessageId)
    before = snapshot(db)
    endpoint = f"/api/v1/conversations/{chat.id}"
    preview = client.post(endpoint + "/context/preview", headers=headers,
                          json={"content": "current unmodified question"})
    assert preview.status_code == 200
    body = preview.json()
    assert body["measurement"] == "characters_not_tokens"
    assert body["totalCharacters"] == sum(len(message["content"]) for message in body["messages"])
    assert body["remainingCharacters"] == body["characterLimit"] - body["totalCharacters"]
    assert sum(section["characters"] for section in body["sections"]) == body["totalCharacters"]
    assert created["id"] in body["includedCardIds"]
    assert body["warnings"] and body["pendingBlockIds"]
    assert snapshot(db) == before and not db.conn.in_transaction
    assert client.recording_provider.prompts == []
    response = client.post(endpoint + "/runs", headers=headers, json={
        "clientMessageId": str(uuid4()), "content": "current unmodified question",
    })
    assert response.status_code == 202
    run_id = response.json()["id"]
    events = client.get(f"/api/v1/runs/{run_id}/events", headers=headers)
    assert events.status_code == 200 and "event: run.completed" in events.text
    assert client.recording_provider.prompts == [body["messages"]]
    blocks = client.get(endpoint + "/memory/blocks", headers=headers).json()["items"]
    assert set(body["pendingBlockIds"]).issubset({item["id"] for item in blocks})


def test_conversation_and_account_archives_include_disabled_memory_and_erasure_cascades(client):
    session, headers = register(client, "context_archive")
    other, _ = register(client, "context_other")
    owner = session["user"]["id"]
    db = client.app.state.db
    chat = db.create_conversation(owner, ConversationInput())
    first = completed(db, owner, chat.id, "old original", "old response")
    completed(db, owner, chat.id, "recent original", "recent response")
    created = card(client, chat.id, headers, source=first.userMessageId, enabled=False)
    endpoint = f"/api/v1/conversations/{chat.id}"
    assert client.post(endpoint + "/memory/summarize", headers=headers).status_code == 200
    blocks = client.get(endpoint + "/memory/blocks", headers=headers).json()["items"]
    assert blocks
    block_id = blocks[0]["id"]
    changed = client.post(endpoint + f"/memory/blocks/{block_id}/update", headers=headers,
                          json={"enabled": False})
    assert changed.status_code == 200
    before = snapshot(db)
    exported = client.get(endpoint + "/export", headers=headers)
    archive = json.loads(exported.json()["content"])
    assert archive["memoryCards"] == [created]
    assert any(item["id"] == block_id and not item["enabled"] for item in archive["memoryBlocks"])
    markdown = client.get(endpoint + "/export?format=markdown", headers=headers).json()["content"]
    assert created["content"] in markdown and "## Source-bound extract blocks" in markdown
    full = client.get("/api/v1/account/export", headers=headers)
    assert full.status_code == 200 and full.json()["conversations"] == [archive]
    assert session["accessToken"] not in full.text and "tokenHash" not in full.text
    assert snapshot(db) == before
    foreign = db.create_conversation(other["user"]["id"], ConversationInput())
    foreign_headers = {"Authorization": "Bearer " + other["accessToken"]}
    card(client, foreign.id, foreign_headers)
    response = client.post("/api/v1/account/erase", headers=headers, json={
        "currentPassword": PASSWORD, "confirmation": "DELETE MY ACCOUNT",
    })
    assert response.status_code == 200
    for table in ("memory_cards", "memory_blocks"):
        assert db.conn.execute(f"SELECT COUNT(*) FROM {table} WHERE conversationId=?", (chat.id,)).fetchone()[0] == 0
    assert db.conn.execute("SELECT COUNT(*) FROM memory_cards WHERE conversationId=?", (foreign.id,)).fetchone()[0] == 1
    assert db.conn.execute("PRAGMA foreign_key_check").fetchall() == []


def test_fork_never_implicitly_copies_cards_blocks_or_future_facts(client):
    headers = developer(client)
    db = client.app.state.db
    chat = db.create_conversation("alice", ConversationInput())
    first = completed(db, "alice", chat.id, "before fork", "before answer")
    second = completed(db, "alice", chat.id, "future question", "future answer")
    card(client, chat.id, headers, source=second.userMessageId)
    card(client, chat.id, headers)
    MemoryRepository(db).update_memory("alice", chat.id, MemoryUpdate(coreMemory="explicit current snapshot"))
    assert client.post(f"/api/v1/conversations/{chat.id}/memory/summarize", headers=headers).status_code == 200
    repo = ConversationToolsRepository(db)
    for point in (None, first.assistantMessageId):
        result = repo.fork("alice", chat.id, ConversationForkInput(
            clientForkId=uuid4(), throughMessageId=point,
        ))
        archive = json.loads(repo.export("alice", result.conversation.id).content)
        assert archive["memoryCards"] == [] and archive["memoryBlocks"] == []
        assert archive["memory"]["summary"] == ""
        assert archive["memory"]["coreMemory"] == "explicit current snapshot"
        assert "不替用户推断关系" not in str(archive)
        if point is not None:
            assert "future" not in str(archive)


def test_context_records_participate_in_both_export_limits(client, monkeypatch):
    session, headers = register(client, "context_bounds")
    owner = session["user"]["id"]
    db = client.app.state.db
    chat = db.create_conversation(owner, ConversationInput())
    card(client, chat.id, headers)
    before = snapshot(db)
    with monkeypatch.context() as patch:
        patch.setattr(conversation_tools, "MAX_EXPORT_RECORDS", 0)
        response = client.get(f"/api/v1/conversations/{chat.id}/export", headers=headers)
        assert response.status_code == 413 and response.json()["error"]["code"] == "export_too_large"
    with monkeypatch.context() as patch:
        patch.setattr(data_controls, "MAX_ACCOUNT_EXPORT_RECORDS", 1)
        response = client.get("/api/v1/account/export", headers=headers)
        assert response.status_code == 413 and response.json()["error"]["code"] == "account_export_too_large"
    assert snapshot(db) == before


def test_cookie_auth_csrf_owner_and_preview_are_consistent(client):
    session, headers = register(client, "context_cookie")
    db = client.app.state.db
    mine = db.create_conversation(session["user"]["id"], ConversationInput())
    other = db.create_conversation("another-user", ConversationInput())
    exchange = client.post("/api/v1/auth/browser/session", headers={**headers, "Origin": ORIGIN})
    assert exchange.status_code == 200
    csrf = exchange.json()["csrfToken"]
    endpoint = f"/api/v1/conversations/{mine.id}"
    assert client.post(endpoint + "/context/preview", json={"content": "hello"}).status_code == 403
    cookie_headers = {"Origin": ORIGIN, "X-CSRF-Token": csrf}
    assert client.post(endpoint + "/context/preview", headers=cookie_headers,
                       json={"content": "hello"}).status_code == 200
    assert client.get(f"/api/v1/conversations/{other.id}/memory/cards").status_code == 404
    assert client.get(f"/api/v1/conversations/{other.id}/memory/blocks").status_code == 404
    active, _ = db.create_run(session["user"]["id"], mine.id, RunInput(clientMessageId=uuid4(), content="waiting"), 120000)
    assert client.post(endpoint + "/context/preview", headers=cookie_headers,
                       json={"content": "hello"}).status_code == 409
    db.finish(active.id, "cancelled")


def test_schema_four_upgrade_preserves_original_messages_sessions_and_legacy_summary(tmp_path):
    path = str(tmp_path / "old-context.sqlite3")
    db = Database(path, Settings(_env_file=None, provider="mock", allow_dev_auth=True))
    db.create_session("synthetic-old-session", "alice", 600)
    chat = db.create_conversation("alice", ConversationInput())
    completed(db, "alice", chat.id, "original old question", "original old answer")
    MemoryRepository(db).memory("alice", chat.id)
    with db.atomic():
        db.conn.execute("INSERT OR IGNORE INTO conversation_memory(conversationId,characterId,coreMemory,relationship,summary,summarizedThrough,updatedAt) VALUES (?,NULL,'','','legacy excerpt',2,'2026-11-19T00:00:00Z')", (chat.id,))
        db.conn.execute("UPDATE conversation_memory SET summary='legacy excerpt',summarizedThrough=2 WHERE conversationId=?", (chat.id,))
    db.conn.executescript("DROP TABLE memory_cards; DROP TABLE memory_blocks; DROP TABLE context_memory_state; PRAGMA user_version=4;")
    messages = db.list_messages("alice", chat.id, None, 10)
    db.close()
    restored = Database(path, Settings(_env_file=None, provider="mock", allow_dev_auth=True))
    try:
        assert restored.conn.execute("PRAGMA user_version").fetchone()[0] == 8
        assert restored.authenticate("synthetic-old-session") == "alice"
        assert restored.list_messages("alice", chat.id, None, 10) == messages
        assert MemoryRepository(restored).memory("alice", chat.id).summary == "legacy excerpt"
        assert restored.conn.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        restored.close()
