"""Isolated SQLite/ASGI tests: no running service, live data or upstream requests."""

import asyncio
import json
import sqlite3
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI, Header
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from pydantic import ValidationError

from allira import conversation_tools as tools
from allira.config import Settings
from allira.conversation_tools import (
    ConversationForkInput,
    ConversationToolsRepository,
    conversation_tools_router,
    initialize_conversation_tools,
)
from allira.db import APIError, Database
from allira.imports import ImportCommitInput, ImportRepository
from allira.memory import CharacterInput, MemoryRepository, MemoryUpdate
from allira.schemas import ConversationInput, RunInput


@pytest.fixture
def db():
    settings = Settings(
        _env_file=None, provider="mock", upstream_model="", upstream_api_key=None,
        fast_upstream_model="", fast_upstream_api_key=None, mock_chunk_delay_seconds=0,
        database_path=":memory:", initial_energy=1000000, run_rate_per_minute=1000,
        allow_dev_auth=True,
    )
    assert settings.provider == "mock"
    database = Database(":memory:", settings)
    initialize_conversation_tools(database)
    yield database
    database.close()


@pytest.fixture
def repo(db):
    return ConversationToolsRepository(db)


def make_app(db):
    app = FastAPI()
    app.state.db = db
    db.create_session("alice-token", "alice", 60)
    db.create_session("bob-token", "bob", 60)

    async def user(authorization: str = Header(default="")) -> str:
        return db.authenticate(authorization.removeprefix("Bearer "))

    @app.exception_handler(APIError)
    async def error(request, exc):
        return JSONResponse({"error": {"code": exc.code, "message": exc.message}}, exc.status)

    app.include_router(conversation_tools_router(db.settings, user))
    return app


@pytest.fixture
def client(db):
    with TestClient(make_app(db)) as result:
        yield result


def headers(user="alice"):
    return {"Authorization": f"Bearer {user}-token"}


def chat(db, user="alice", title="chat", instructions=None):
    return db.create_conversation(user, ConversationInput(title=title, instructions=instructions))


def pair(db, conversation_id, question="question", answer="answer", status="completed", user="alice"):
    run, _ = db.create_run(
        user, conversation_id, RunInput(clientMessageId=uuid4(), content=question), 120000,
    )
    db.append_delta(run.id, answer)
    return db.finish(run.id, status, "test_error" if status == "failed" else None)


def counts(db):
    return {table: db.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("conversations", "messages", "runs", "run_events", "conversation_memory",
                          "conversation_forks", "run_usage", "energy_ledger")}


def test_startup_is_idempotent_and_repositories_do_not_initialize_or_commit(db):
    key = db.conn.execute("SELECT value FROM conversation_tool_state").fetchone()[0]
    initialize_conversation_tools(db)
    assert db.conn.execute("SELECT value FROM conversation_tool_state").fetchone()[0] == key
    with pytest.raises(RuntimeError), db.atomic():
        source = chat(db)
        ConversationToolsRepository(db)
        raise RuntimeError("outer rollback")
    assert db.conn.execute("SELECT 1 FROM conversations WHERE id = ?", (source.id,)).fetchone() is None


def test_recent_search_uses_stable_descending_keysets_and_deleted_anchor(db, repo):
    chats = [chat(db, title=f"chat-{index}") for index in range(5)]
    private = chat(db, "bob", "private")
    with db.atomic():
        for item in chats:
            db.conn.execute("UPDATE conversations SET updatedAt = ? WHERE id = ?", ("same", item.id))
        db.conn.execute("UPDATE conversations SET updatedAt = ? WHERE id = ?", ("zzzz", private.id))
        db.conn.execute("UPDATE conversations SET updatedAt = ? WHERE id = ?", ("zzzz", chats[0].id))
    expected = [chats[0].id, *sorted([item.id for item in chats[1:]], reverse=True)]
    first = repo.search("alice", limit=2)
    assert [item.conversation.id for item in first.items] == expected[:2]
    assert all(item.matchedMessageId is None for item in first.items)
    assert first.nextCursor and private.id not in first.nextCursor
    db.delete_conversation("alice", expected[1])
    second = repo.search("alice", cursor=first.nextCursor, limit=2)
    third = repo.search("alice", cursor=second.nextCursor, limit=2)
    assert [item.conversation.id for item in second.items + third.items] == expected[2:]
    assert third.nextCursor is None
    assert repo.search("nobody").items == []


def test_cursor_is_owner_query_bound_signed_opaque_and_durable(db, repo, tmp_path):
    for _ in range(3):
        chat(db, title="needle")
    first = repo.search("alice", "needle", limit=1)
    cursor = first.nextCursor
    assert cursor and "alice" not in cursor and "needle" not in cursor
    altered = cursor[:-3] + ("a" if cursor[-3] != "a" else "b") + cursor[-2:]
    for user, query, token in (("bob", "needle", cursor), ("alice", "Needle", cursor),
                               ("alice", "", cursor), ("alice", "needle", altered),
                               ("alice", "needle", "."), ("alice", "needle", "[]"),
                               ("alice", "needle", "a" * 2049)):
        with pytest.raises(APIError) as error:
            repo.search(user, query, token)
        assert error.value.status == 400 and error.value.code == "invalid_cursor"
    assert ConversationToolsRepository(db).search("alice", "needle", cursor).items
    # Reopen an isolated file database: the cursor key survives process restarts.
    path = str(tmp_path / "cursor.sqlite3")
    first_db = Database(path, db.settings)
    initialize_conversation_tools(first_db)
    try:
        chat(first_db, title="needle")
        chat(first_db, title="needle")
        saved = ConversationToolsRepository(first_db).search("alice", "needle", limit=1).nextCursor
    finally:
        first_db.close()
    second_db = Database(path, db.settings)
    initialize_conversation_tools(second_db)
    try:
        assert len(ConversationToolsRepository(second_db).search("alice", "needle", saved).items) == 1
    finally:
        second_db.close()


def test_search_is_literal_full_message_and_owner_scoped(db, repo):
    mine = chat(db, instructions="private instruction marker")
    first = pair(db, mine.id, "q", "x" * 400 + " NeedLe 你好🌍 100% a_b path\\file ")
    pair(db, mine.id, "later needle", "answer")
    # Historical imports may preserve NUL; generation correctly rejects controls.
    with db.atomic():
        db.conn.execute("UPDATE messages SET content = content || ? WHERE id = ?",
                        ("\x00 after-nul ", first.assistantMessageId))
    other = chat(db, "bob", "needle private")
    pair(db, other.id, "secret needle", "secret", user="bob")
    for query in ("needle", "你好🌍", "100%", "a_b", "path\\file", "after-nul"):
        results = repo.search("alice", query).items
        assert [item.conversation.id for item in results] == [mine.id]
        assert results[0].matchedMessageId == first.assistantMessageId
        assert len(results[0].snippet) <= tools.SNIPPET_CHARACTERS
        assert query.lower() in results[0].snippet.lower()
    for query in ("%does-not-exist", "_does-not-exist", "' OR 1=1 --", "secret"):
        assert repo.search("alice", query).items == []
    by_instructions = repo.search("alice", "instruction marker").items[0]
    assert by_instructions.matchedMessageId is None and "instruction marker" in by_instructions.snippet
    titled = chat(db, title="needle title")
    assert titled.id in [item.conversation.id for item in repo.search("alice", "needle").items]
    assert repo.search("bob", "after-nul").items == []
    assert repo.search("alice", "%").items[0].conversation.id == mine.id
    assert repo.search("alice", "_").items[0].conversation.id == mine.id


@pytest.mark.parametrize("query,limit", [("x" * 201, 20), ("\ud800", 20), ("\x00", 20), ("", 0), ("", 101)])
def test_search_validates_unicode_query_and_limit(repo, query, limit):
    with pytest.raises(APIError) as error:
        repo.search("alice", query, limit=limit)
    assert error.value.status == 422


def test_export_json_is_lossless_all_statuses_character_memory_and_import_source(db, repo):
    raw = json.dumps({"messages": [{"role": "user", "content": " orphan🌍\u0000 "},
                                    {"role": "system", "content": "archived system"},
                                    {"role": "user", "content": " q\r\n"},
                                    {"role": "assistant", "content": "  ```code```  "}]}, ensure_ascii=False)
    imported = ImportRepository(db).commit("alice", ImportCommitInput(
        format="json", content=raw, clientImportId=uuid4(), title="archive",
    ))
    source_id = imported.conversation.id
    character = MemoryRepository(db).create_character("alice", CharacterInput(
        name="角色", description="description🌍", instructions="role instructions",
    ))
    MemoryRepository(db).update_memory("alice", source_id, MemoryUpdate(
        characterId=character.id, coreMemory=" core\n", relationship="关系",
    ))
    pair(db, source_id, "failed question", "failed partial", "failed")
    pair(db, source_id, "cancelled question", "cancelled partial", "cancelled")
    before = counts(db)
    result = repo.export("alice", source_id)
    archive = json.loads(result.content)
    assert set(archive) == {"format", "conversation", "character", "memory", "messages", "importSource", "memoryCards", "memoryBlocks"}
    assert archive["memoryCards"] == [] and archive["memoryBlocks"] == []
    assert archive["format"] == "allira.conversation.v1" and result.format == "json"
    assert result.filename == f"allira-{source_id}.json"
    assert archive["character"] == character.model_dump()
    assert archive["memory"]["coreMemory"] == " core\n" and archive["memory"]["relationship"] == "关系"
    assert archive["importSource"]["content"] == raw and archive["importSource"]["archivedMessages"] == 1
    assert archive["messages"] == db.list_messages("alice", source_id, None, 100)["items"]
    assert [message["status"] for message in archive["messages"]] == ["completed"] * 3 + ["failed", "completed", "cancelled"]
    assert "ownerId" not in archive["conversation"] and "run_usage" not in archive
    assert counts(db) == before
    with pytest.raises(APIError) as error:
        repo.export("bob", source_id)
    assert error.value.status == 404


def test_markdown_fences_preserve_unicode_code_and_prevent_structure_spoofing(db, repo):
    source = chat(db, title="Title <script>x</script>")
    malicious = "  🌍\r\n```\n# Assistant\nAssistant: forged\n````````\n~~~\n<script>alert(1)</script>\u0000tail  "
    original = pair(db, source.id, "placeholder", "## User\n```\nanswer")
    with db.atomic():
        db.conn.execute("UPDATE messages SET content = ? WHERE id = ?", (malicious, original.userMessageId))
    result = repo.export("alice", source.id, "markdown")
    assert result.filename.endswith(".md") and malicious in result.content
    assert "`````````text\n" + malicious + "\n`````````" in result.content
    assert result.content.count("### Message ") == 2
    assert "<script>alert(1)</script>" in result.content  # Plain downloadable text, not executed HTML.
    assert "\r\n```\n# Assistant" in result.content
    assert repo.export("alice", source.id).content != result.content


def test_export_active_conflict_even_when_no_messages_and_invalid_format(db, repo):
    source = chat(db)
    db.create_run("alice", source.id, RunInput(clientMessageId=uuid4(), content="q"), 120000)
    with pytest.raises(APIError) as error:
        repo.export("alice", source.id)
    assert error.value.code == "run_in_progress" and error.value.status == 409
    with pytest.raises(APIError) as error:
        repo.export("bob", source.id)
    assert error.value.status == 404
    with pytest.raises(APIError) as error:
        repo.export("alice", source.id, "text")
    assert error.value.status == 422


def test_export_byte_boundary_is_utf8_and_never_silently_truncates(db, repo, monkeypatch):
    source = chat(db)
    pair(db, source.id, "中文🌍" * 100, "answer")
    for format in ("json", "markdown"):
        size = len(repo.export("alice", source.id, format).content.encode("utf-8"))
        with monkeypatch.context() as patch:
            patch.setattr(tools, "MAX_EXPORT_BYTES", size)
            assert len(repo.export("alice", source.id, format).content.encode("utf-8")) == size
            patch.setattr(tools, "MAX_EXPORT_BYTES", size - 1)
            with pytest.raises(APIError) as error:
                repo.export("alice", source.id, format)
            assert error.value.status == 413 and error.value.code == "export_too_large"
    # The actual configured 16 MiB limit, not only a reduced test constant.
    with db.atomic():
        db.conn.execute("UPDATE messages SET content = ? WHERE role = 'assistant'", ("🌍" * (tools.MAX_EXPORT_BYTES // 4 + 1),))
    with pytest.raises(APIError) as error:
        repo.export("alice", source.id)
    assert error.value.status == 413


def test_export_actual_50000_message_limit_uses_count_not_truncation(db, repo):
    source = chat(db)
    run = pair(db, source.id)
    with db.atomic():
        db.conn.executemany(
            "INSERT INTO messages VALUES (?, ?, ?, ?, 'user', '', 'completed', 'stamp')",
            ((f"extra-{index}", source.id, run.id, index + 3) for index in range(49999)),
        )
    assert db.conn.execute("SELECT COUNT(*) FROM messages WHERE conversationId = ?", (source.id,)).fetchone()[0] == 50001
    with pytest.raises(APIError) as error:
        repo.export("alice", source.id)
    assert error.value.code == "export_too_large"


def test_fork_only_completed_actual_pairs_with_new_ids_and_terminal_events(db, repo):
    source = chat(db, instructions="system instructions")
    complete = pair(db, source.id, "question1", "answer1")
    pair(db, source.id, "failed question", "partial", "failed")
    pair(db, source.id, "cancelled question", "cancelled", "cancelled")
    last = pair(db, source.id, "question2", "answer2")
    character = MemoryRepository(db).create_character("alice", CharacterInput(name="角色"))
    MemoryRepository(db).update_memory("alice", source.id, MemoryUpdate(
        characterId=character.id, coreMemory="core", relationship="relationship",
    ))
    with db.atomic():
        db.conn.execute("UPDATE conversation_memory SET summary='future secret', summarizedThrough=999 WHERE conversationId=?", (source.id,))
    original = repo.export("alice", source.id).content
    before = counts(db)
    result = repo.fork("alice", source.id, ConversationForkInput(clientForkId=uuid4()))
    assert not result.replayed and result.copiedMessages == 4
    assert result.conversation.instructions == "system instructions" and result.conversation.title == "chat（分叉）"
    timeline = db.list_messages("alice", result.conversation.id, None, 100)["items"]
    assert [message["content"] for message in timeline] == ["question1", "answer1", "question2", "answer2"]
    assert [message["sequence"] for message in timeline] == [1, 2, 3, 4]
    assert all(message["status"] == "completed" for message in timeline)
    old_ids = {row[0] for row in db.conn.execute("SELECT id FROM messages WHERE conversationId=?", (source.id,))}
    assert not old_ids.intersection(message["id"] for message in timeline)
    for message in timeline[1::2]:
        run = db.run("alice", message["runId"])
        assert run.id not in (complete.id, last.id)
        assert run.clientMessageId not in (complete.clientMessageId, last.clientMessageId)
        assert db.max_event_id(run.id) == 1 and db.events(run.id, 0)[0]["type"] == "run.completed"
        assert json.loads(db.events(run.id, 0)[0]["data"])["message"] == message
    memory = MemoryRepository(db).memory("alice", result.conversation.id)
    assert memory.characterId == character.id and memory.coreMemory == "core" and memory.relationship == "relationship"
    assert memory.summary == "" and memory.summarizedThrough == 0
    assert counts(db)["run_usage"] == before["run_usage"] and counts(db)["energy_ledger"] == before["energy_ledger"]
    assert repo.export("alice", source.id).content == original
    assert timeline[0]["createdAt"] == db.message(complete.userMessageId).createdAt
    prompt = MemoryRepository(db).build_prompt("alice", result.conversation.id, "now", 120000)
    assert "future secret" not in str(prompt) and "failed question" not in str(prompt)


def test_fork_boundary_excludes_future_summary_and_future_import_archive(db, repo):
    raw = json.dumps({"messages": [{"role": "user", "content": "q1"},
                                    {"role": "assistant", "content": "a1"},
                                    {"role": "user", "content": "future question"},
                                    {"role": "assistant", "content": "future answer"}]})
    source = ImportRepository(db).commit("alice", ImportCommitInput(format="json", content=raw, clientImportId=uuid4())).conversation
    first = db.list_messages("alice", source.id, None, 10)["items"][1]
    with db.atomic():
        db.conn.execute(
            "UPDATE conversation_memory SET summary='future summary', summarizedThrough=4 WHERE conversationId=?",
            (source.id,),
        )
    result = repo.fork("alice", source.id, ConversationForkInput(clientForkId=uuid4(), throughMessageId=first["id"], title="  keep title🌍  "))
    assert result.copiedMessages == 2 and result.conversation.title == "  keep title🌍  "
    exported = json.loads(repo.export("alice", result.conversation.id).content)
    assert exported["importSource"] is None and exported["memory"]["summary"] == ""
    assert "future" not in json.dumps(exported)


def test_fork_rejects_failed_cancelled_user_and_malformed_pair_boundaries(db, repo):
    source = chat(db)
    good = pair(db, source.id)
    failed = pair(db, source.id, status="failed")
    cancelled = pair(db, source.id, status="cancelled")
    malformed = pair(db, source.id, "malformed", "malformed")
    with db.atomic():
        db.conn.execute("UPDATE messages SET role='user' WHERE id=?", (malformed.assistantMessageId,))
    for message_id in (good.userMessageId, failed.assistantMessageId, cancelled.assistantMessageId, malformed.assistantMessageId):
        with pytest.raises(APIError) as error:
            repo.fork("alice", source.id, ConversationForkInput(clientForkId=uuid4(), throughMessageId=message_id))
        assert error.value.code == "invalid_fork_point" and error.value.status == 400
    result = repo.fork("alice", source.id, ConversationForkInput(clientForkId=uuid4()))
    assert result.copiedMessages == 2
    other = chat(db, "bob")
    foreign = pair(db, other.id, user="bob")
    for owner, source_id, point in (("bob", source.id, good.assistantMessageId),
                                     ("alice", source.id, foreign.assistantMessageId),
                                     ("alice", source.id, str(uuid4()))):
        with pytest.raises(APIError) as error:
            repo.fork(owner, source_id, ConversationForkInput(clientForkId=uuid4(), throughMessageId=point))
        assert error.value.status == 404


def test_fork_empty_timeline_active_and_exact_owner_scoped_replay(db, repo):
    source = chat(db, title="源" * 200)
    body = ConversationForkInput(clientForkId=uuid4())
    first = repo.fork("alice", source.id, body)
    assert first.copiedMessages == 0 and len(first.conversation.title) == 200
    before = counts(db)
    active, _ = db.create_run("alice", source.id, RunInput(clientMessageId=uuid4(), content="new generation"), 120000)
    replay = repo.fork("alice", source.id, body)
    assert replay.replayed and replay.conversation.id == first.conversation.id
    assert counts(db)["conversation_forks"] == before["conversation_forks"]
    with pytest.raises(APIError) as error:
        repo.fork("alice", source.id, ConversationForkInput(clientForkId=uuid4()))
    assert error.value.code == "run_in_progress"
    with pytest.raises(APIError) as error:
        repo.fork("alice", source.id, body.model_copy(update={"title": "changed"}))
    assert error.value.code == "idempotency_conflict"  # Replay/conflict precedes source activity.
    db.finish(active.id, "cancelled")
    another = chat(db)
    for source_id, changed in ((another.id, body),
                                (source.id, ConversationForkInput(clientForkId=body.clientForkId, title=None)),
                                (source.id, ConversationForkInput(clientForkId=body.clientForkId, throughMessageId=None))):
        with pytest.raises(APIError) as error:
            repo.fork("alice", source_id, changed)
        assert error.value.code == "idempotency_conflict"
    bob = chat(db, "bob")
    assert not repo.fork("bob", bob.id, body).replayed


def test_deleted_target_is_tombstone_not_recreated_and_source_delete_keeps_replay(db, repo):
    source = chat(db)
    body = ConversationForkInput(clientForkId=uuid4())
    result = repo.fork("alice", source.id, body)
    db.delete_conversation("alice", source.id)
    assert repo.fork("alice", source.id, body).conversation.id == result.conversation.id
    db.delete_conversation("alice", result.conversation.id)
    row = db.conn.execute("SELECT * FROM conversation_forks").fetchone()
    assert row["conversationId"] is None and row["sourceConversationId"] is None
    before = counts(db)
    with pytest.raises(APIError) as error:
        repo.fork("alice", source.id, body)
    assert error.value.status == 404 and counts(db) == before


def test_fork_hard_limits_roll_back_without_partial_rows(db, repo, monkeypatch):
    source = chat(db)
    pair(db, source.id, "first", "answer")
    pair(db, source.id, "second🌍", "answer")
    before = counts(db)
    for key, value in (("MAX_EXPORT_MESSAGES", 2), ("MAX_EXPORT_BYTES", 10)):
        with monkeypatch.context() as patch:
            patch.setattr(tools, key, value)
            with pytest.raises(APIError) as error:
                repo.fork("alice", source.id, ConversationForkInput(clientForkId=uuid4()))
            assert error.value.status == 413 and error.value.code == "fork_too_large"
            assert counts(db) == before


def test_fork_event_failure_outer_rollback_and_deferred_commit_failure_are_atomic(db, repo, monkeypatch):
    source = chat(db)
    pair(db, source.id)
    before = counts(db)

    def fail(*args):
        raise RuntimeError("event insertion failed")

    with monkeypatch.context() as patch:
        patch.setattr(db, "_event", fail)
        with pytest.raises(RuntimeError, match="event insertion failed"):
            repo.fork("alice", source.id, ConversationForkInput(clientForkId=uuid4()))
    assert counts(db) == before and not db.conn.in_transaction
    with pytest.raises(RuntimeError), db.atomic():
        repo.fork("alice", source.id, ConversationForkInput(clientForkId=uuid4()))
        raise RuntimeError("caller rollback")
    assert counts(db) == before
    db.conn.executescript("""
        CREATE TABLE deferred_parent(id INTEGER PRIMARY KEY);
        CREATE TABLE deferred_child(id INTEGER REFERENCES deferred_parent(id) DEFERRABLE INITIALLY DEFERRED);
        CREATE TRIGGER fork_deferred_fail AFTER INSERT ON conversation_forks BEGIN
            INSERT INTO deferred_child VALUES (999);
        END;
    """)
    with pytest.raises(sqlite3.IntegrityError):
        repo.fork("alice", source.id, ConversationForkInput(clientForkId=uuid4()))
    assert counts(db) == before and not db.conn.in_transaction
    assert db.conn.execute("SELECT COUNT(*) FROM deferred_child").fetchone()[0] == 0


@pytest.mark.parametrize("values", [{"clientForkId": "not-uuid"}, {"title": " "}, {"title": "x" * 201},
                                    {"title": "\ud800"}, {"throughMessageId": ""}, {"extra": True}])
def test_fork_dto_rejects_invalid_parameters(values):
    with pytest.raises(ValidationError):
        ConversationForkInput(**{"clientForkId": uuid4(), **values})


def test_http_auth_ownership_queries_export_and_fork_contracts(client, db):
    source = chat(db, title="needle")
    complete = pair(db, source.id)
    base = f"/api/v1/conversations/{source.id}"
    for path in ("/api/v1/conversation-search", base + "/export"):
        assert client.get(path).status_code == 401
    response = client.get("/api/v1/conversation-search?q=needle&limit=1", headers=headers())
    assert response.status_code == 200
    assert set(response.json()["items"][0]) == {"conversation", "matchedMessageId", "snippet"}
    assert client.get("/api/v1/conversation-search?q=" + "x" * 201, headers=headers()).status_code == 422
    assert client.get("/api/v1/conversation-search?limit=0", headers=headers()).status_code == 422
    assert client.get(base + "/export", headers=headers("bob")).status_code == 404
    assert client.get(base + "/export?format=text", headers=headers()).status_code == 422
    exported = client.get(base + "/export?format=json", headers=headers())
    assert exported.status_code == 200 and set(exported.json()) == {"format", "filename", "content"}
    assert json.loads(exported.json()["content"])["messages"][1]["id"] == complete.assistantMessageId
    body = {"clientForkId": str(uuid4()), "throughMessageId": complete.assistantMessageId}
    assert client.post(base + "/fork", json=body).status_code == 401
    assert client.post(base + "/fork", json=body, headers=headers("bob")).status_code == 404
    created = client.post(base + "/fork", json=body, headers=headers())
    assert created.status_code == 201 and set(created.json()) == {"conversation", "copiedMessages", "replayed"}
    assert created.json()["copiedMessages"] == 2 and not created.json()["replayed"]
    replay = client.post(base + "/fork", json=body, headers=headers())
    assert replay.status_code == 201 and replay.json()["replayed"]
    body["title"] = "conflict"
    assert client.post(base + "/fork", json=body, headers=headers()).status_code == 409
    assert client.post(base + "/fork", json={"clientForkId": "bad"}, headers=headers()).status_code == 422


def test_single_process_async_fork_requests_commit_only_once(db):
    source = chat(db)
    pair(db, source.id)
    app = make_app(db)
    before = counts(db)
    payload = {"clientForkId": str(uuid4())}

    async def requests():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://isolated.test") as client:
            return await asyncio.gather(*[
                client.post(f"/api/v1/conversations/{source.id}/fork", json=payload, headers=headers())
                for _ in range(8)
            ])

    results = asyncio.run(requests())
    assert all(result.status_code == 201 for result in results)
    assert len({result.json()["conversation"]["id"] for result in results}) == 1
    assert sum(not result.json()["replayed"] for result in results) == 1
    after = counts(db)
    for table, difference in (("conversations", 1), ("messages", 2), ("runs", 1), ("run_events", 1),
                               ("conversation_forks", 1), ("run_usage", 0), ("energy_ledger", 0)):
        assert after[table] - before[table] == difference
