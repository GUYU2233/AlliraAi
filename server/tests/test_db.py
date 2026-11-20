"""Repository transactions, idempotency, prompt policy and recovery."""

import hashlib
import json
from uuid import uuid4

import pytest

from allira.config import Settings
from allira.db import APIError, Database
from allira.schemas import ConversationInput, RunInput


@pytest.fixture
def db():
    database = Database(":memory:", Settings(_env_file=None, allow_dev_auth=True))
    yield database
    database.close()


def create(db, content="hello", client_id=None):
    chat = db.create_conversation("alice", ConversationInput(instructions="system"))
    body = RunInput(clientMessageId=client_id or uuid4(), content=content)
    run, prompt = db.create_run("alice", chat.id, body, 120000)
    return chat, body, run, prompt


def test_sessions_are_hashed_and_expire(db):
    db.create_session("token-value", "alice", 60)
    row = dict(db.conn.execute("SELECT * FROM sessions").fetchone())
    assert row["tokenHash"] == hashlib.sha256(b"token-value").hexdigest()
    assert "token-value" not in row.values()
    assert db.authenticate("token-value") == "alice"
    with db.conn:
        db.conn.execute("UPDATE sessions SET expiresAt = 0")
    with pytest.raises(APIError, match="session") as error:
        db.authenticate("token-value")
    assert error.value.status == 401
    db.create_session("new-token", "bob", 60)
    assert db.conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 1


def test_one_running_run_and_identical_retry(db):
    chat, body, created, _ = create(db)
    replay, prompt = db.create_run("alice", chat.id, body, 1)
    assert replay == created
    assert prompt is None  # idempotent retry must not re-evaluate a grown context.
    with pytest.raises(APIError) as error:
        db.create_run("alice", chat.id, RunInput(clientMessageId=uuid4(), content="next"), 120000)
    assert error.value.code == "run_in_progress"
    assert db.conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 1
    assert db.conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 2
    with pytest.raises(APIError) as error:
        db.create_run("alice", chat.id, body.model_copy(update={"content": " hello"}), 120000)
    assert error.value.code == "idempotency_conflict"


def test_partial_text_and_event_commit_and_terminal_is_immutable(db):
    _, _, created, _ = create(db)
    db.append_delta(created.id, "你好")
    assert db.message(created.assistantMessageId).content == "你好"
    assert json.loads(db.events(created.id, 1)[0]["data"])["delta"] == "你好"
    finished = db.finish(created.id, "cancelled")
    assert finished.status == "cancelled"
    assert db.message(created.assistantMessageId).status == "cancelled"
    db.append_delta(created.id, "must not append")
    assert db.finish(created.id, "completed") == finished
    assert db.max_event_id(created.id) == 3
    assert json.loads(db.events(created.id, 2)[0]["data"])["message"]["content"] == "你好"


def test_prompt_contains_only_completed_pairs_and_current_input(db):
    chat, _, created, prompt = create(db)
    assert prompt == [{"role": "system", "content": "system"}, {"role": "user", "content": "hello"}]
    db.append_delta(created.id, "completed answer")
    db.finish(created.id, "completed")
    failed, prompt = db.create_run(
        "alice", chat.id, RunInput(clientMessageId=uuid4(), content="failed question"), 120000,
    )
    assert prompt[-3:] == [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "completed answer"},
        {"role": "user", "content": "failed question"},
    ]
    db.append_delta(failed.id, "failed partial")
    db.finish(failed.id, "failed", "upstream_error")
    _, prompt = db.create_run(
        "alice", chat.id, RunInput(clientMessageId=uuid4(), content="current"), 120000,
    )
    assert prompt == [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "completed answer"},
        {"role": "user", "content": "current"},
    ]


def test_context_limit_rejects_without_writing_any_run_or_message(db):
    chat = db.create_conversation("alice", ConversationInput(instructions="system"))
    with pytest.raises(APIError) as error:
        db.create_run("alice", chat.id, RunInput(clientMessageId=uuid4(), content="hello"), 10)
    assert error.value.status == 413
    assert error.value.code == "context_too_large"
    for table in ("runs", "messages", "run_events"):
        assert db.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0


def test_restart_recovers_partial_run_and_is_idempotent(tmp_path):
    path = str(tmp_path / "recovery.sqlite3")
    first = Database(path)
    _, _, created, _ = create(first)
    first.append_delta(created.id, "partial")
    first.close()
    second = Database(path)
    try:
        second.recover_interrupted_runs()
        failed = second.run("alice", created.id)
        assert failed.status == "failed"
        assert failed.errorCode == "server_restarted"
        assert second.message(failed.assistantMessageId).content == "partial"
        assert second.message(failed.assistantMessageId).status == "failed"
        assert second.events(created.id, 2)[0]["type"] == "run.failed"
        second.recover_interrupted_runs()
        assert second.max_event_id(created.id) == 3
    finally:
        second.close()


def test_run_creation_rolls_back_all_rows_on_event_failure(db):
    chat = db.create_conversation("alice", ConversationInput())
    def fail_event(*args):
        raise RuntimeError("simulate event write failure")
    db._event = fail_event
    with pytest.raises(RuntimeError):
        db.create_run("alice", chat.id, RunInput(clientMessageId=uuid4(), content="hello"), 120000)
    assert db.conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 0
    assert db.conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 0


def test_atomic_deferred_commit_failure_rolls_back_and_remains_usable(db):
    db.conn.executescript("""
        CREATE TABLE parent_guard(id INTEGER PRIMARY KEY);
        CREATE TABLE child_guard(parentId INTEGER REFERENCES parent_guard(id)
                                 DEFERRABLE INITIALLY DEFERRED);
    """)
    import sqlite3
    with pytest.raises(sqlite3.IntegrityError), db.atomic():
        db.conn.execute("INSERT INTO child_guard VALUES (123)")
    assert not db.conn.in_transaction
    assert db.conn.execute("SELECT COUNT(*) FROM child_guard").fetchone()[0] == 0
    with db.atomic():
        db.conn.execute("INSERT INTO parent_guard VALUES (123)")
        db.conn.execute("INSERT INTO child_guard VALUES (123)")
    assert not db.conn.in_transaction
