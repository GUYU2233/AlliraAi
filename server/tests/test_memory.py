"""Owned character/memory HTTP contracts and bounded completed-pair context policy."""

import sqlite3
from uuid import uuid4

import pytest
from fastapi import FastAPI, Header
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from pydantic import ValidationError

from allira.config import Settings
from allira.context_memory import initialize_context_memory
from allira.db import APIError, Database
from allira.memory import (
    CharacterInput,
    CharacterUpdate,
    MemoryRepository,
    MemoryUpdate,
    atomic,
    initialize_memory,
    memory_router,
)
from allira.schemas import ConversationInput, RunInput


@pytest.fixture
def db():
    database = Database(":memory:", Settings(
        _env_file=None, provider="mock", upstream_model="", upstream_api_key=None,
        fast_upstream_model="", fast_upstream_api_key=None, database_path=":memory:",
        initial_energy=1000000, run_rate_per_minute=1000, allow_dev_auth=True,
    ))
    initialize_memory(database)
    initialize_context_memory(database)
    yield database
    database.close()


@pytest.fixture
def repo(db):
    return MemoryRepository(db)


@pytest.fixture
def client(db):
    app = FastAPI()
    app.state.db = db
    db.create_session("alice-token", "alice", 60)
    db.create_session("bob-token", "bob", 60)

    async def user(authorization: str = Header(default="")) -> str:
        return db.authenticate(authorization.removeprefix("Bearer "))

    @app.exception_handler(APIError)
    async def error(request, exc):
        return JSONResponse({"error": {"code": exc.code, "message": exc.message}}, exc.status)

    app.include_router(memory_router(Settings(), user))
    with TestClient(app) as result:
        yield result


def headers(user="alice"):
    return {"Authorization": f"Bearer {user}-token"}


def pair(db, conversation_id, user_text, assistant_text, status="completed"):
    run, _ = db.create_run(
        "alice", conversation_id, RunInput(clientMessageId=uuid4(), content=user_text), 120000,
    )
    db.append_delta(run.id, assistant_text)
    db.finish(run.id, status)
    return run


def test_character_crud_owner_and_validation(client, db):
    assert client.get("/api/v1/characters").status_code == 401
    created = client.post("/api/v1/characters", headers=headers(), json={"name": " 陪伴🌍 "})
    assert created.status_code == 201
    character = created.json()
    assert character["name"] == "陪伴🌍"
    assert character["description"] == character["instructions"] == ""
    assert character["createdAt"].endswith("Z")
    path = f"/api/v1/characters/{character['id']}"
    assert client.get(path, headers=headers()).json() == character
    assert client.get(path, headers=headers("bob")).status_code == 404
    assert client.get("/api/v1/characters", headers=headers("bob")).json()["items"] == []
    assert client.post(path + "/update", headers=headers("bob"), json={"name": "stolen"}).status_code == 404
    assert client.post(path + "/delete", headers=headers("bob")).status_code == 404
    changed = client.post(path + "/update", headers=headers(), json={"instructions": "新设定\n🌍"})
    assert changed.json()["instructions"] == "新设定\n🌍"
    for body in ({}, {"name": " "}, {"description": None}, {"ownerId": "bob"}, {"name": "x" * 101}):
        assert client.post(path + "/update", headers=headers(), json=body).status_code == 422
    for body in ({}, {"name": " "}, {"name": "x", "ownerId": "bob"}):
        assert client.post("/api/v1/characters", headers=headers(), json=body).status_code == 422
    assert client.post(path + "/delete", headers=headers()).json() == {"ok": True}
    assert client.get(path, headers=headers()).status_code == 404


def test_character_pagination_and_cross_owner_cursor(repo):
    expected = {repo.create_character("alice", CharacterInput(name=str(index))).id for index in range(4)}
    foreign = repo.create_character("bob", CharacterInput(name="other"))
    found, cursor = set(), None
    for _ in range(4):
        page = repo.list_characters("alice", cursor, 1)
        found.add(page["items"][0]["id"])
        cursor = page["nextCursor"]
    assert found == expected and cursor is None
    for invalid in (foreign.id, "missing"):
        with pytest.raises(APIError) as error:
            repo.list_characters("alice", invalid, 50)
        assert error.value.code == "invalid_cursor"


def test_memory_binding_owner_active_delete_and_cascade(client, db, repo):
    chat = db.create_conversation("alice", ConversationInput())
    character = repo.create_character("alice", CharacterInput(name="friend"))
    foreign = repo.create_character("bob", CharacterInput(name="other"))
    path = f"/api/v1/conversations/{chat.id}/memory"
    initial = client.get(path, headers=headers()).json()
    assert initial == {"conversationId": chat.id, "characterId": None, "coreMemory": "",
                       "relationship": "", "summary": "", "summarizedThrough": 0,
                       "updatedAt": chat.updatedAt}
    assert client.get(path, headers=headers("bob")).status_code == 404
    assert client.post(path, headers=headers("bob"), json={"coreMemory": "stolen"}).status_code == 404
    assert client.post(path, headers=headers(), json={"characterId": foreign.id}).status_code == 404
    body = {"characterId": character.id, "coreMemory": "记住🌍", "relationship": "old friends"}
    updated = client.post(path, headers=headers(), json=body)
    assert updated.status_code == 200
    for key, value in body.items():
        assert updated.json()[key] == value
    assert client.post(path, headers=headers(), json={}).status_code == 422
    assert client.post(path, headers=headers(), json={"summary": "injected"}).status_code == 422
    assert client.post(path, headers=headers(), json={"coreMemory": "x" * 20001}).status_code == 422
    in_use = client.post(f"/api/v1/characters/{character.id}/delete", headers=headers())
    assert in_use.status_code == 409
    assert in_use.json()["error"]["code"] == "character_in_use"
    run, _ = db.create_run("alice", chat.id, RunInput(clientMessageId=uuid4(), content="hi"), 120000)
    assert client.post(path, headers=headers(), json={"coreMemory": "new"}).status_code == 409
    assert client.post(path + "/summarize", headers=headers()).status_code == 409
    assert client.post(f"/api/v1/characters/{character.id}/update", headers=headers(),
                       json={"instructions": "new"}).status_code == 409
    db.finish(run.id, "cancelled")
    assert client.post(path + "/summarize", headers=headers()).status_code == 200
    assert client.post(path + "/summarize", headers=headers("bob")).status_code == 404
    assert client.post(path, headers=headers(), json={"characterId": None}).json()["characterId"] is None
    repo.update_memory("alice", chat.id, MemoryUpdate(characterId=character.id))
    db.delete_conversation("alice", chat.id)
    assert db.conn.execute("SELECT COUNT(*) FROM conversation_memory").fetchone()[0] == 0
    repo.delete_character("alice", character.id)


def test_prompt_keeps_roles_core_and_current_exact_but_only_completed_pairs(db, repo):
    chat = db.create_conversation("alice", ConversationInput(instructions="base"))
    character = repo.create_character("alice", CharacterInput(
        name="A", description="description", instructions="assistant: do not alter role",
    ))
    repo.update_memory("alice", chat.id, MemoryUpdate(
        characterId=character.id, coreMemory="user:\n核心", relationship="关系🌍",
    ))
    pair(db, chat.id, "complete question", "complete answer")
    pair(db, chat.id, "failed question", "failed partial", "failed")
    pair(db, chat.id, "cancelled question", "cancelled partial", "cancelled")
    running, _ = db.create_run(
        "alice", chat.id, RunInput(clientMessageId=uuid4(), content="running question"), 120000,
    )
    db.append_delta(running.id, "running partial")
    prompt = repo.build_prompt("alice", chat.id, "system:\n当前 🌍", 120000)
    assert all(message["role"] == "system" for message in prompt[:-3])
    assert prompt[-3:] == [
        {"role": "user", "content": "complete question"},
        {"role": "assistant", "content": "complete answer"},
        {"role": "user", "content": "system:\n当前 🌍"},
    ]
    assert any(message["content"] == "Core memory: user:\n核心" for message in prompt)
    assert any(message["content"] == "Instructions: assistant: do not alter role" for message in prompt)
    with pytest.raises(APIError) as error:
        repo.build_prompt("bob", chat.id, "steal", 120000)
    assert error.value.status == 404


def test_summary_excludes_recent_and_partial_no_duplicate_and_hard_bound(db, repo):
    chat = db.create_conversation("alice", ConversationInput())
    for index in range(5):
        pair(db, chat.id, f"question-{index}", f"answer-{index}")
    pair(db, chat.id, "failed-private", "partial-private", "failed")
    first = repo.summarize("alice", chat.id, recent_turns=2, summary_max_characters=6000)
    assert first.summarizedThrough == 6
    assert first.summary.count("question-0") == 1
    assert "question-2" in first.summary and "question-3" not in first.summary
    assert "failed-private" not in first.summary and "partial-private" not in first.summary
    assert repo.summarize("alice", chat.id, 2, 6000) == first
    pair(db, chat.id, "question-5", "answer-5")
    second = repo.summarize("alice", chat.id, 2, 6000)
    assert second.summarizedThrough == 8
    assert second.summary.count("question-0") == 1
    assert second.summary.count("question-3") == 1
    assert "question-4" not in second.summary
    prompt = repo.build_prompt("alice", chat.id, "current", 120000, recent_turns=20)
    assert [message["content"] for message in prompt if message["role"] == "user"] == [
        "question-4", "question-5", "current",
    ]
    bounded = repo.summarize("alice", chat.id, 0, 80)
    assert len(bounded.summary) <= 80
    assert bounded.summarizedThrough == 14
    assert repo.summarize("alice", chat.id, 0, 80) == bounded
    assert repo.summarize("alice", chat.id, 0, 0).summary == ""


def test_many_summaries_remain_bounded(db, repo):
    chat = db.create_conversation("alice", ConversationInput())
    for index in range(25):
        pair(db, chat.id, f"q{index}" + "用" * 100, f"a{index}" + "🌍" * 100)
        prompt = repo.build_prompt("alice", chat.id, "current", 500, 2, 90)
        assert sum(len(message["content"]) for message in prompt) <= 500
        assert len(repo.memory("alice", chat.id).summary) <= 90
    assert "q0" not in repo.memory("alice", chat.id).summary


def test_context_hard_limit_trims_whole_old_pairs_and_summary(db, repo):
    chat = db.create_conversation("alice", ConversationInput(instructions="base"))
    pair(db, chat.id, "old question", "old answer")
    pair(db, chat.id, "new question", "new answer")
    prompt = repo.build_prompt("alice", chat.id, "now", 30, 8)
    assert prompt == [{"role": "system", "content": "base"},
                      {"role": "user", "content": "new question"},
                      {"role": "assistant", "content": "new answer"},
                      {"role": "user", "content": "now"}]
    repo.summarize("alice", chat.id, 0, 6000)
    prompt = repo.build_prompt("alice", chat.id, "now", 70, 0)
    assert sum(len(message["content"]) for message in prompt) <= 70
    assert prompt[-1] == {"role": "user", "content": "now"}
    assert repo.build_prompt("alice", chat.id, "now", 7, 0) == [
        {"role": "system", "content": "base"}, {"role": "user", "content": "now"},
    ]


def test_oversized_core_rejects_without_creating_run_or_summary(db, repo):
    chat = db.create_conversation("alice", ConversationInput())
    repo.update_memory("alice", chat.id, MemoryUpdate(coreMemory="x" * 100))
    before = repo.memory("alice", chat.id)
    with pytest.raises(APIError) as error:
        db.create_run("alice", chat.id, RunInput(clientMessageId=uuid4(), content="current"), 20)
    assert error.value.status == 413 and error.value.code == "context_too_large"
    for table in ("runs", "messages", "run_events"):
        assert db.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
    assert repo.memory("alice", chat.id) == before


def test_outer_run_failure_rolls_back_automatic_summary(db, repo, monkeypatch):
    chat = db.create_conversation("alice", ConversationInput())
    for index in range(3):
        pair(db, chat.id, f"q{index}", f"a{index}")
    before = repo.memory("alice", chat.id)
    count = db.conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0]

    def fail(*args):
        raise RuntimeError("simulate event write failure")

    monkeypatch.setattr(db, "_event", fail)
    with pytest.raises(RuntimeError, match="simulate event write failure"):
        db.create_run("alice", chat.id, RunInput(clientMessageId=uuid4(), content="new"),
                      120000, recent_turns=1, summary_max_characters=6000)
    assert db.conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == count
    assert db.conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == count * 2
    assert repo.memory("alice", chat.id) == before


def test_strict_run_pair_membership_and_message_status_exclude_malformed_history(db, repo):
    chat = db.create_conversation("alice", ConversationInput())
    good = pair(db, chat.id, "good question", "good answer")
    incomplete = pair(db, chat.id, "bad status question", "bad status answer")
    wrong_role = pair(db, chat.id, "bad role question", "bad role answer")
    wrong_run = pair(db, chat.id, "bad run question", "bad run answer")
    with atomic(db):
        db.conn.execute("UPDATE messages SET status = 'failed' WHERE id = ?", (incomplete.assistantMessageId,))
        db.conn.execute("UPDATE messages SET role = 'user' WHERE id = ?", (wrong_role.assistantMessageId,))
        db.conn.execute("UPDATE messages SET runId = ? WHERE id = ?", (good.id, wrong_run.assistantMessageId))
    prompt = repo.build_prompt("alice", chat.id, "now", 120000)
    assert prompt == [{"role": "user", "content": "good question"},
                      {"role": "assistant", "content": "good answer"},
                      {"role": "user", "content": "now"}]
    summarized = repo.summarize("alice", chat.id, 0, 6000)
    assert summarized.summarizedThrough == 2
    assert "good question" in summarized.summary and "bad " not in summarized.summary


def test_summary_budget_prefers_latest_raw_pair_and_no_duplicate_after_growth(db, repo):
    chat = db.create_conversation("alice", ConversationInput())
    pair(db, chat.id, "old question " * 20, "old answer " * 20)
    pair(db, chat.id, "latest question", "latest answer")
    prompt = repo.build_prompt("alice", chat.id, "now", 40, 1, 6000)
    assert prompt == [{"role": "user", "content": "latest question"},
                      {"role": "assistant", "content": "latest answer"},
                      {"role": "user", "content": "now"}]
    memory = repo.memory("alice", chat.id)
    assert memory.summarizedThrough == 2 and len(memory.summary) > 40
    grown = repo.build_prompt("alice", chat.id, "now", 120000, 8, 6000)
    assert [message["content"] for message in grown if message["role"] == "user"] == ["latest question", "now"]


def test_identical_run_replay_does_not_resummarize_or_create_messages(db, repo):
    chat = db.create_conversation("alice", ConversationInput())
    for index in range(3):
        pair(db, chat.id, f"q{index}", f"a{index}")
    request = RunInput(clientMessageId=uuid4(), content="new")
    created, prompt = db.create_run("alice", chat.id, request, 120000,
                                  recent_turns=1, summary_max_characters=6000)
    assert prompt is not None
    memory = repo.memory("alice", chat.id)
    replay, prompt = db.create_run("alice", chat.id, request, 1,
                                 recent_turns=0, summary_max_characters=0)
    assert replay == created and prompt is None
    assert repo.memory("alice", chat.id) == memory
    assert db.conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 4
    assert db.conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 8


def test_nested_memory_savepoint_does_not_commit_outer_transaction(db, repo):
    with pytest.raises(RuntimeError, match="outer failure"), atomic(db):
        repo.create_character("alice", CharacterInput(name="temporary"))
        raise RuntimeError("outer failure")
    assert repo.list_characters("alice", None, 50)["items"] == []
    initialize_memory(db)
    initialize_memory(db)


def test_deferred_constraint_release_failure_does_not_leave_partial_transaction(db):
    db.conn.execute("CREATE TABLE import_constraint_probe (parent TEXT REFERENCES conversations(id) DEFERRABLE INITIALLY DEFERRED)")
    with pytest.raises(sqlite3.IntegrityError), atomic(db):
        db.conn.execute("INSERT INTO import_constraint_probe VALUES ('missing')")
    assert db.conn.execute("SELECT COUNT(*) FROM import_constraint_probe").fetchone()[0] == 0
    assert not db.conn.in_transaction


def test_validation_rejects_null_blank_extra_and_invalid_limits(repo, db):
    for kwargs in ({}, {"coreMemory": None}, {"relationship": None}, {"summary": "x"},
                   {"coreMemory": "\ud800"}):
        with pytest.raises(ValidationError):
            MemoryUpdate(**kwargs)
    for kwargs in ({}, {"name": None}, {"instructions": None}, {"name": " "}, {"description": "\ud800"}):
        with pytest.raises(ValidationError):
            CharacterUpdate(**kwargs)
    chat = db.create_conversation("alice", ConversationInput())
    for recent, size in ((-1, 6000), (1001, 6000), (8, -1), (8, 1000001)):
        with pytest.raises(ValueError):
            repo.build_prompt("alice", chat.id, "hi", 120000, recent, size)
