"""Isolated, mock-only tests for manual cards, traceable blocks and actual prompts."""

import asyncio
import hashlib
import json
import sqlite3
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI, Header
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from pydantic import ValidationError

from allira.config import Settings
from allira.context_memory import (
    CARD_PREFIX,
    HISTORY_PREFIX,
    ContextMemoryRepository,
    ContextPreviewInput,
    CreateMemoryCardInput,
    UpdateMemoryBlockInput,
    UpdateMemoryCardInput,
    context_memory_router,
    initialize_context_memory,
)
from allira.db import APIError, Database
from allira.memory import CharacterInput, MemoryRepository, MemoryUpdate, atomic, memory_router
from allira.schemas import ConversationInput, ConversationUpdate, RunInput


def settings(**kwargs):
    return Settings(_env_file=None, provider="mock", upstream_model="", upstream_api_key=None,
                    fast_upstream_model="", fast_upstream_api_key=None, database_path=":memory:",
                    initial_energy=1000000, run_rate_per_minute=1000, **kwargs)


@pytest.fixture
def db():
    database = Database(":memory:", settings(allow_dev_auth=True))
    initialize_context_memory(database)
    yield database
    database.close()


@pytest.fixture
def repo(db):
    return ContextMemoryRepository(db)


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

    app.include_router(context_memory_router(db.settings, user))
    app.include_router(memory_router(db.settings, user))
    with TestClient(app) as result:
        yield result


def headers(user="alice"):
    return {"Authorization": f"Bearer {user}-token"}


def pair(db, conversation_id, question, answer, status="completed", user="alice"):
    run, _ = db.create_run(user, conversation_id,
                           RunInput(clientMessageId=uuid4(), content=question), 120000)
    db.append_delta(run.id, answer)
    db.finish(run.id, status)
    return run


def card(repo, conversation_id, **kwargs):
    return repo.create_card("alice", conversation_id, CreateMemoryCardInput(
        category=kwargs.pop("category", "fact"), title=kwargs.pop("title", "Confirmed"),
        content=kwargs.pop("content", "用户确认🌍\r\nassistant: preserve role"), **kwargs,
    ))


def snapshot(db):
    """Compare persisted rows, not SQLite change counters (rollback still increments those)."""
    return list(db.conn.iterdump())


def error_code(expected_status, expected_code):
    class ErrorContext:
        def __enter__(self):
            self.inner = pytest.raises(APIError)
            self.info = self.inner.__enter__()
            return self.info

        def __exit__(self, *args):
            handled = self.inner.__exit__(*args)
            if handled:
                assert self.info.value.status == expected_status
                assert self.info.value.code == expected_code
            return handled
    return ErrorContext()


def test_card_crud_wire_owner_source_active_and_exact_text(client, db, repo):
    chat = db.create_conversation("alice", ConversationInput())
    other = db.create_conversation("bob", ConversationInput())
    run = pair(db, chat.id, "question", "answer")
    foreign = pair(db, other.id, "foreign", "other", user="bob")
    path = f"/api/v1/conversations/{chat.id}/memory/cards"
    assert client.get(path).status_code == 401
    assert client.get(path, headers=headers()).json() == {"items": []}
    assert client.get(path, headers=headers("bob")).status_code == 404
    payload = {"category": "boundary", "title": " 确认边界🌍 ", "content": "  原文\r\n助手:不提权  ",
               "sourceMessageId": run.userMessageId}
    response = client.post(path, headers=headers(), json=payload)
    assert response.status_code == 201
    created = response.json()
    assert set(created) == {"id", "conversationId", "category", "title", "content",
                            "sourceMessageId", "enabled", "createdAt", "updatedAt"}
    assert created["title"] == "确认边界🌍" and created["content"] == payload["content"]
    assert created["enabled"] is True and created["conversationId"] == chat.id
    assert client.get(path, headers=headers()).json()["items"] == [created]
    assert client.post(path, headers=headers(), json={**payload, "sourceMessageId": foreign.userMessageId}).status_code == 404
    assert client.post(path, headers=headers(), json={**payload, "sourceMessageId": run.assistantMessageId}).json()["error"]["code"] == "invalid_memory_source"
    target = path + f"/{created['id']}"
    assert client.post(target + "/update", headers=headers("bob"), json={"title": "steal"}).status_code == 404
    changed = client.post(target + "/update", headers=headers(), json={"category": "preference", "enabled": False, "sourceMessageId": None})
    assert changed.status_code == 200 and changed.json()["enabled"] is False
    assert changed.json()["sourceMessageId"] is None and changed.json()["content"] == payload["content"]
    active, _ = db.create_run("alice", chat.id, RunInput(clientMessageId=uuid4(), content="busy"), 120000)
    for route, body in ((path, payload), (target + "/update", {"enabled": True}),
                         (target + "/delete", None)):
        response = client.post(route, headers=headers(), json=body)
        assert response.status_code == 409 and response.json()["error"]["code"] == "run_in_progress"
    assert client.get(path, headers=headers()).status_code == 200
    db.finish(active.id, "cancelled")
    assert client.post(target + "/delete", headers=headers("bob")).status_code == 404
    assert client.post(target + "/delete", headers=headers()).json() == {"ok": True}
    assert client.post(target + "/delete", headers=headers()).status_code == 404


@pytest.mark.parametrize("body", [
    {}, {"category": "auto", "title": "x", "content": "x"},
    {"category": "fact", "title": " ", "content": "x"},
    {"category": "fact", "title": "x" * 101, "content": "x"},
    {"category": "fact", "title": "x", "content": " "},
    {"category": "fact", "title": "x", "content": "x" * 2001},
    {"category": "fact", "title": "x", "content": "x", "enabled": 1},
    {"category": "fact", "title": "x", "content": "x", "ownerId": "bob"},
    {"category": "fact", "title": "x", "content": "\ud800"},
])
def test_create_card_validation(body):
    with pytest.raises(ValidationError):
        CreateMemoryCardInput(**body)


@pytest.mark.parametrize("body", [
    {}, {"enabled": None}, {"enabled": "false"}, {"enabled": 0}, {"title": None},
    {"content": None}, {"category": None}, {"category": "inferred"}, {"title": " "},
    {"content": "\ud800"}, {"sourceMessageId": ""}, {"summary": "upgrade"},
])
def test_update_card_validation(body):
    with pytest.raises(ValidationError):
        UpdateMemoryCardInput(**body)


def test_source_requires_exact_completed_run_owned_user_and_not_partial(db, repo):
    chat = db.create_conversation("alice", ConversationInput())
    good = pair(db, chat.id, "good", "yes")
    failed = pair(db, chat.id, "failed", "partial", "failed")
    cancelled = pair(db, chat.id, "cancelled", "partial", "cancelled")
    malformed = pair(db, chat.id, "malformed", "bad")
    with atomic(db):
        db.conn.execute("UPDATE messages SET runId = ? WHERE id = ?", (good.id, malformed.assistantMessageId))
    for source in (failed.userMessageId, cancelled.userMessageId, malformed.userMessageId,
                   good.assistantMessageId):
        with error_code(400, "invalid_memory_source"):
            card(repo, chat.id, sourceMessageId=source)
    with error_code(404, "not_found"):
        card(repo, chat.id, sourceMessageId="missing")
    assert card(repo, chat.id, sourceMessageId=good.userMessageId).sourceMessageId == good.userMessageId
    active, _ = db.create_run("alice", chat.id, RunInput(clientMessageId=uuid4(), content="active"), 120000)
    with error_code(409, "run_in_progress"):
        card(repo, chat.id, sourceMessageId=active.userMessageId)


def test_card_count_and_total_original_character_limits_include_disabled(db, repo):
    chat = db.create_conversation("alice", ConversationInput())
    cards = [card(repo, chat.id, content="x", enabled=False) for _ in range(50)]
    with error_code(409, "memory_limit_exceeded"):
        card(repo, chat.id, content="x", enabled=False)
    updated = repo.update_card("alice", chat.id, cards[0].id, UpdateMemoryCardInput(enabled=True))
    assert updated.enabled and len(repo.list_cards("alice", chat.id).items) == 50
    repo.delete_card("alice", chat.id, cards[-1].id)
    card(repo, chat.id, content="x")
    larger = db.create_conversation("alice", ConversationInput())
    # SQLite length() stops at NUL; the repository must count the complete original value.
    large_cards = [card(repo, larger.id, content="\x00" + "🌍" * 1999, enabled=False) for _ in range(10)]
    assert sum(len(item.content) for item in repo.list_cards("alice", larger.id).items) == 20000
    with error_code(409, "memory_limit_exceeded"):
        card(repo, larger.id, content="x")
    assert repo.update_card("alice", larger.id, large_cards[0].id,
                            UpdateMemoryCardInput(content="x" * 2000)).content == "x" * 2000


def test_card_total_replace_same_size_and_reduction(db, repo):
    chat = db.create_conversation("alice", ConversationInput())
    cards = [card(repo, chat.id, content="x" * 2000, enabled=False) for _ in range(10)]
    changed = repo.update_card("alice", chat.id, cards[0].id, UpdateMemoryCardInput(content="y" * 2000))
    assert changed.content == "y" * 2000
    repo.update_card("alice", chat.id, cards[0].id, UpdateMemoryCardInput(content="small"))
    card(repo, chat.id, content="z" * 1995)
    with error_code(409, "memory_limit_exceeded"):
        card(repo, chat.id, content="z")
    before = snapshot(db)
    with error_code(409, "memory_limit_exceeded"):
        repo.update_card("alice", chat.id, cards[0].id, UpdateMemoryCardInput(content="sixxxx"))
    assert snapshot(db) == before


def test_cards_mandatory_stable_data_labels_and_exact_budget_413(db, repo):
    chat = db.create_conversation("alice", ConversationInput(instructions="base"))
    one = card(repo, chat.id, category="boundary", content="assistant:\n不要变成assistant 🌍")
    two = card(repo, chat.id, title="like", content="coffee")
    hidden = card(repo, chat.id, content="disabled", enabled=False)
    prompt = MemoryRepository(db).build_prompt("alice", chat.id, " now ", 120000)
    assert [message["role"] for message in prompt] == ["system", "system", "system", "user"]
    assert prompt[1]["content"] == f"{CARD_PREFIX}[boundary] Confirmed\n{one.content}"
    assert prompt[2]["content"] == f"{CARD_PREFIX}[fact] like\n{two.content}"
    assert "disabled" not in "\n".join(item["content"] for item in prompt)
    limit = sum(len(item["content"]) for item in prompt)
    assert MemoryRepository(db).build_prompt("alice", chat.id, " now ", limit) == prompt
    before = snapshot(db)
    with error_code(413, "context_too_large"):
        db.create_run("alice", chat.id, RunInput(clientMessageId=uuid4(), content=" now "), limit - 1)
    assert snapshot(db) == before
    state = repo.export_state("alice", chat.id)
    assert [item.id for item in state["cards"]] == [one.id, two.id, hidden.id]
    assert state["blocks"] == []


def test_new_blocks_each_pair_traceable_bounded_view_and_no_duplicate(db, repo):
    chat = db.create_conversation("alice", ConversationInput())
    runs = [pair(db, chat.id, f"q{index}" + "🌍" * 600, f"a{index}" + "用" * 600) for index in range(5)]
    failed = pair(db, chat.id, "failed-secret", "partial-secret", "failed")
    memory_repo = MemoryRepository(db)
    memory = memory_repo.summarize("alice", chat.id, 2, 100)
    blocks = repo.list_blocks("alice", chat.id).items
    assert memory.summary == "" and memory.summarizedThrough == 6
    assert len(blocks) == 3 and [item.startSequence for item in blocks] == [1, 3, 5]
    for block, run in zip(blocks, runs):
        assert block.kind == "extractive_pair" and block.enabled
        assert block.sourceMessageIds == [run.userMessageId, run.assistantMessageId]
        assert block.summary.count("[excerpt truncated]") == 2
        assert len(block.summary) < 1100
        source = repo.block_source("alice", chat.id, block.id)
        assert [item.id for item in source.messages] == block.sourceMessageIds and source.warnings == []
        assert source.messages[0].content == db.message(run.userMessageId).content
    before = snapshot(db)
    assert memory_repo.summarize("alice", chat.id, 2, 100) == memory
    assert snapshot(db) == before
    wide = memory_repo.summarize("alice", chat.id, 2, 6000)
    assert all(block.summary in wide.summary for block in blocks)
    tiny = memory_repo.summarize("alice", chat.id, 2, 1)
    assert tiny.summary == "" and tiny.summarizedThrough == 6
    assert [item.id for item in repo.list_blocks("alice", chat.id).items] == [item.id for item in blocks]
    assert failed.userMessageId not in {source for item in blocks for source in item.sourceMessageIds}


def test_block_disable_excludes_old_raw_after_window_growth_and_reenable(db, repo):
    chat = db.create_conversation("alice", ConversationInput())
    pair(db, chat.id, "old private q", "old private a")
    pair(db, chat.id, "latest q", "latest a")
    memory_repo = MemoryRepository(db)
    memory_repo.summarize("alice", chat.id, 1, 6000)
    block = repo.list_blocks("alice", chat.id).items[0]
    through = memory_repo.memory("alice", chat.id).summarizedThrough
    disabled = repo.update_block("alice", chat.id, block.id, UpdateMemoryBlockInput(enabled=False))
    assert not disabled.enabled and memory_repo.memory("alice", chat.id).summary == ""
    prompt = memory_repo.build_prompt("alice", chat.id, "current", 120000, 50)
    assert [item["content"] for item in prompt] == ["latest q", "latest a", "current"]
    assert memory_repo.memory("alice", chat.id).summarizedThrough == through
    assert repo.block_source("alice", chat.id, block.id).messages[0].content == "old private q"
    repo.update_block("alice", chat.id, block.id, UpdateMemoryBlockInput(enabled=True))
    restored = memory_repo.build_prompt("alice", chat.id, "current", 120000, 50)
    assert restored[0]["role"] == "system" and block.summary in restored[0]["content"]
    assert [item["content"] for item in restored if item["role"] == "user"] == ["latest q", "current"]


def test_legacy_one_time_no_precise_source_no_backfill_and_no_recreate(db, repo):
    chat = db.create_conversation("alice", ConversationInput())
    runs = [pair(db, chat.id, f"q{index}", f"a{index}") for index in range(4)]
    memory_repo = MemoryRepository(db)
    previous = memory_repo.memory("alice", chat.id).model_copy(update={
        "summary": "OLD partial-tail Unicode🌍", "summarizedThrough": 4,
    })
    with atomic(db):
        memory_repo._store_memory(previous)
    memory = memory_repo.summarize("alice", chat.id, 1, 6000)
    blocks = repo.list_blocks("alice", chat.id).items
    assert len(blocks) == 2
    legacy, exact = blocks
    assert legacy.kind == "legacy_summary" and legacy.summary == previous.summary
    assert legacy.startSequence == 0 and legacy.endSequence == 4 and legacy.sourceMessageIds == []
    assert repo.block_source("alice", chat.id, legacy.id).messages == []
    assert "no precise" in repo.block_source("alice", chat.id, legacy.id).warnings[0]
    assert exact.sourceMessageIds == [runs[2].userMessageId, runs[2].assistantMessageId]
    assert memory.summarizedThrough == 6
    repo.update_block("alice", chat.id, legacy.id, UpdateMemoryBlockInput(enabled=False))
    memory_repo.summarize("alice", chat.id, 30, 6000)
    assert len(repo.list_blocks("alice", chat.id).items) == 2
    assert "OLD" not in memory_repo.build_prompt("alice", chat.id, "now", 120000, 30)[0]["content"]
    assert all(source not in exact.sourceMessageIds for source in (runs[0].userMessageId, runs[1].userMessageId))


def test_full_newest_notes_skip_oversized_blocks_no_text_slicing(db, repo):
    chat = db.create_conversation("alice", ConversationInput())
    pair(db, chat.id, "small-old", "small-answer")
    pair(db, chat.id, "middle" * 200, "large" * 200)
    pair(db, chat.id, "new", "reply")
    memory_repo = MemoryRepository(db)
    memory = memory_repo.summarize("alice", chat.id, 0, 100)
    blocks = repo.list_blocks("alice", chat.id).items
    assert len(blocks) == 3 and blocks[-1].summary in memory.summary
    assert blocks[1].summary not in memory.summary
    prompt = memory_repo.build_prompt("alice", chat.id, "now", len(HISTORY_PREFIX) + len(blocks[-1].summary) + 3, 0, 6000)
    assert prompt == [{"role": "system", "content": HISTORY_PREFIX + blocks[-1].summary},
                      {"role": "user", "content": "now"}]
    assert len(repo.list_blocks("alice", chat.id).items) == 3


def test_blocks_owner_scope_cursor_search_literal_and_invalid_before_owner(db, repo):
    chat = db.create_conversation("alice", ConversationInput())
    other = db.create_conversation("alice", ConversationInput())
    foreign = db.create_conversation("bob", ConversationInput())
    for index in range(4):
        pair(db, chat.id, f"Q{index} literal%_\\ after", f"answer{index}")
    MemoryRepository(db).summarize("alice", chat.id, 0, 6000)
    blocks = repo.list_blocks("alice", chat.id).items
    first = repo.list_blocks("alice", chat.id, "literal%_\\", None, 1)
    assert first.items == [blocks[0]] and first.nextCursor
    second = repo.list_blocks("alice", chat.id, "literal%_\\", first.nextCursor, 1)
    assert second.items == [blocks[1]]
    assert repo.list_blocks("alice", chat.id, "NO MATCH").items == []
    assert len(repo.list_blocks("alice", chat.id, "q").items) == 4
    for user, conversation_id, query, cursor in (
        ("alice", chat.id, "other query", first.nextCursor),
        ("alice", other.id, "literal%_\\", first.nextCursor),
        ("alice", chat.id, "literal%_\\", "x" + first.nextCursor),
        ("alice", chat.id, "literal%_\\", ""),
        ("alice", chat.id, "literal%_\\", "x" * 2049),
    ):
        with error_code(400, "invalid_cursor"):
            repo.list_blocks(user, conversation_id, query, cursor, 1)
    with error_code(404, "not_found"):
        repo.list_blocks("bob", chat.id, "x" * 201, "invalid", 0)
    with error_code(404, "not_found"):
        repo.block_source("alice", foreign.id, blocks[0].id)
    with error_code(404, "not_found"):
        repo.block_source("alice", other.id, blocks[0].id)
    with error_code(404, "not_found"):
        repo.update_block("bob", chat.id, blocks[0].id, UpdateMemoryBlockInput(enabled=False))
    for query, limit in (("q" * 201, 20), ("\ud800", 20), ("", 0), ("", 101)):
        with error_code(422, "invalid_query"):
            repo.list_blocks("alice", chat.id, query, None, limit)
    with atomic(db):
        db.conn.execute("UPDATE memory_blocks SET summary = ? WHERE id = ?", ("before\x00needle🌍", blocks[0].id))
    assert repo.list_blocks("alice", chat.id, "needle🌍").items[0].id == blocks[0].id
    # Deleting an anchor does not invalidate its authenticated keyset.
    with atomic(db):
        db.conn.execute("DELETE FROM memory_blocks WHERE id = ?", (blocks[0].id,))
    assert repo.list_blocks("alice", chat.id, "literal%_\\", first.nextCursor, 1).items == [blocks[1]]


def test_persistent_block_cursor_and_initializer_no_mass_backfill(tmp_path):
    path = str(tmp_path / "context.sqlite3")
    first = Database(path, settings())
    initialize_context_memory(first)
    chat = first.create_conversation("alice", ConversationInput())
    for index in range(3):
        pair(first, chat.id, f"q{index}", f"a{index}")
    MemoryRepository(first).summarize("alice", chat.id, 0, 6000)
    repo = ContextMemoryRepository(first)
    page = repo.list_blocks("alice", chat.id, "", None, 1)
    next_expected = repo.list_blocks("alice", chat.id, "", page.nextCursor, 1)
    first.close()
    second = Database(path, settings())
    try:
        initialize_context_memory(second)
        before = snapshot(second)
        assert ContextMemoryRepository(second).list_blocks("alice", chat.id, "", page.nextCursor, 1) == next_expected
        ContextMemoryRepository(second)
        MemoryRepository(second)
        assert snapshot(second) == before
    finally:
        second.close()


def test_source_unavailable_never_reconstructs_or_crosses_ownership(db, repo):
    chat = db.create_conversation("alice", ConversationInput())
    run = pair(db, chat.id, "original", "answer")
    MemoryRepository(db).summarize("alice", chat.id, 0, 6000)
    block = repo.list_blocks("alice", chat.id).items[0]
    with atomic(db):
        db.conn.execute("UPDATE messages SET status = 'failed' WHERE id = ?", (run.assistantMessageId,))
    source = repo.block_source("alice", chat.id, block.id)
    assert source.messages == [] and "unavailable" in source.warnings[0]


def test_preview_exact_run_prompt_pending_ids_character_accounting_readonly(db, repo):
    chat = db.create_conversation("alice", ConversationInput(instructions="  exact instructions\r\n🌍 "))
    memory_repo = MemoryRepository(db)
    character = memory_repo.create_character("alice", CharacterInput(name="friend", description="d", instructions="rules"))
    memory_repo.update_memory("alice", chat.id, MemoryUpdate(characterId=character.id, coreMemory="core", relationship="relationship"))
    confirmed = card(repo, chat.id, category="boundary", content="verified")
    disabled = card(repo, chat.id, content="disabled", enabled=False)
    for index in range(4):
        pair(db, chat.id, f"question{index}", f"answer{index}")
    configured = settings(context_recent_turns=1, summary_max_characters=6000)
    before = snapshot(db)
    preview = repo.preview("alice", chat.id, ContextPreviewInput(content=" now🌍\r\n "), configured)
    assert snapshot(db) == before
    assert preview.measurement == "characters_not_tokens"
    assert preview.totalCharacters == sum(len(item["content"]) for item in preview.messages)
    assert preview.totalCharacters == sum(section.characters for section in preview.sections)
    assert len(preview.messages) == sum(section.messages for section in preview.sections)
    assert preview.remainingCharacters + preview.totalCharacters == configured.max_prompt_characters
    assert preview.includedCardIds == [confirmed.id] and preview.excludedCardCount == 1
    assert disabled.id not in preview.includedCardIds
    assert len(preview.pendingBlockIds) == 3 and set(preview.includedBlockIds) == set(preview.pendingBlockIds)
    assert preview.omittedBlockCount == preview.omittedRecentPairCount == 0
    assert {section.kind for section in preview.sections} == {
        "instructions", "character", "core", "relationship", "memory_cards", "history_notes",
        "recent_messages", "current_message",
    }
    assert any("energy" in warning for warning in preview.warnings)
    assert repo.preview("alice", chat.id, ContextPreviewInput(content=" now🌍\r\n "), configured) == preview
    run, actual = db.create_run("alice", chat.id, RunInput(clientMessageId=uuid4(), content=" now🌍\r\n "),
                                configured.max_prompt_characters, recent_turns=1, summary_max_characters=6000)
    assert actual == preview.messages
    assert [item.id for item in repo.list_blocks("alice", chat.id).items] == preview.pendingBlockIds
    db.finish(run.id, "cancelled")


def test_preview_legacy_pending_deterministic_and_never_commits_outer_work(db, repo):
    chat = db.create_conversation("alice", ConversationInput())
    memory_repo = MemoryRepository(db)
    old = memory_repo.memory("alice", chat.id).model_copy(update={"summary": "previous 🌍", "summarizedThrough": 8})
    with atomic(db):
        memory_repo._store_memory(old)
    before = snapshot(db)
    one = repo.preview("alice", chat.id, ContextPreviewInput(content="current"), db.settings)
    two = repo.preview("alice", chat.id, ContextPreviewInput(content="current"), db.settings)
    assert one == two and len(one.pendingBlockIds) == 1
    assert snapshot(db) == before
    with pytest.raises(RuntimeError, match="outer"), atomic(db):
        made = card(repo, chat.id, content="temporary")
        inner_before = snapshot(db)
        inner = repo.preview("alice", chat.id, ContextPreviewInput(content="current"), db.settings)
        assert made.id in inner.includedCardIds and snapshot(db) == inner_before
        raise RuntimeError("outer")
    assert snapshot(db) == before


def test_preview_omits_complete_pairs_and_notes_but_not_boundary_cards(db, repo):
    chat = db.create_conversation("alice", ConversationInput())
    pair(db, chat.id, "old" * 200, "answer" * 200)
    pair(db, chat.id, "mid" * 100, "reply" * 100)
    pair(db, chat.id, "latest", "answer")
    confirmed = card(repo, chat.id, category="boundary", content="do not omit me")
    preview = repo.preview("alice", chat.id, ContextPreviewInput(content="current"),
                           settings(max_prompt_characters=1000, context_recent_turns=3))
    assert preview.omittedRecentPairCount == 1
    assert preview.includedCardIds == [confirmed.id] and preview.remainingCharacters >= 0
    assert [item["content"] for item in preview.messages if item["role"] == "user"] == ["mid" * 100, "latest", "current"]
    for _ in range(5):
        card(repo, chat.id, category="boundary", content="big" * 100)
    before = snapshot(db)
    with error_code(413, "context_too_large"):
        repo.preview("alice", chat.id, ContextPreviewInput(content="current"), settings(max_prompt_characters=1000))
    assert snapshot(db) == before


@pytest.mark.parametrize("location", ["current", "instructions", "character", "core", "relationship", "card", "recent", "note"])
def test_preview_checks_safety_all_prompt_sections_and_rolls_back(db, repo, location):
    chat = db.create_conversation("alice", ConversationInput())
    memory_repo = MemoryRepository(db)
    configured = settings(context_recent_turns=1, safety_blocked_terms=["BLOCKED🌍"])
    content = "BLOCKED🌍" if location == "current" else "current"
    if location == "instructions":
        db.update_conversation("alice", chat.id, ConversationUpdate(instructions="BLOCKED🌍"))
    elif location == "character":
        character = memory_repo.create_character("alice", CharacterInput(name="BLOCKED🌍"))
        memory_repo.update_memory("alice", chat.id, MemoryUpdate(characterId=character.id))
    elif location in {"core", "relationship"}:
        memory_repo.update_memory("alice", chat.id, MemoryUpdate(**{
            "coreMemory" if location == "core" else "relationship": "BLOCKED🌍",
        }))
    elif location == "card":
        card(repo, chat.id, content="BLOCKED🌍")
    elif location in {"recent", "note"}:
        pair(db, chat.id, "BLOCKED🌍", "answer")
        if location == "note":
            pair(db, chat.id, "latest", "answer")
    before = snapshot(db)
    with error_code(422, "content_blocked"):
        repo.preview("alice", chat.id, ContextPreviewInput(content=content), configured)
    assert snapshot(db) == before


def test_preview_control_character_nul_stored_card_disabled_and_run_same_rule(db, repo):
    chat = db.create_conversation("alice", ConversationInput())
    confirmed = card(repo, chat.id, content="raw\x00data")
    assert repo.list_cards("alice", chat.id).items[0].content == "raw\x00data"
    for content in ("normal", "bad\x00current"):
        before = snapshot(db)
        with error_code(422, "unsafe_control_character"):
            repo.preview("alice", chat.id, ContextPreviewInput(content=content), db.settings)
        assert snapshot(db) == before
    with error_code(422, "unsafe_control_character"):
        db.create_run("alice", chat.id, RunInput(clientMessageId=uuid4(), content="normal"), 120000)
    repo.update_card("alice", chat.id, confirmed.id, UpdateMemoryCardInput(enabled=False))
    assert repo.preview("alice", chat.id, ContextPreviewInput(content="normal"), db.settings).excludedCardCount == 1


def test_preview_http_validation_owner_inactive_and_source_routes(client, db, repo):
    chat = db.create_conversation("alice", ConversationInput())
    path = f"/api/v1/conversations/{chat.id}"
    for body in ({}, {"content": " "}, {"content": "x" * 20001}, {"content": "\ud800"}, {"content": "x", "budget": 999999}):
        # TestClient's JSON encoder cannot serialize lone surrogates in strict UTF-8.
        if body.get("content") == "\ud800":
            with pytest.raises(ValidationError):
                ContextPreviewInput(**body)
        else:
            assert client.post(path + "/context/preview", headers=headers(), json=body).status_code == 422
    assert client.post(path + "/context/preview", headers=headers("bob"), json={"content": "steal"}).status_code == 404
    pair(db, chat.id, "question", "answer")
    MemoryRepository(db).summarize("alice", chat.id, 0, 6000)
    block = repo.list_blocks("alice", chat.id).items[0]
    block_path = path + f"/memory/blocks/{block.id}"
    assert client.get(block_path + "/source", headers=headers()).json()["messages"][0]["role"] == "user"
    for body in ({}, {"enabled": "false"}, {"enabled": False, "summary": "fake"}):
        assert client.post(block_path + "/update", headers=headers(), json=body).status_code == 422
    assert client.get(path + "/memory/blocks", headers=headers(), params={"q": "q" * 201}).status_code == 422
    active, _ = db.create_run("alice", chat.id, RunInput(clientMessageId=uuid4(), content="active"), 120000)
    assert client.post(path + "/context/preview", headers=headers(), json={"content": "next"}).status_code == 409
    assert client.post(block_path + "/update", headers=headers(), json={"enabled": False}).status_code == 409
    assert client.get(block_path + "/source", headers=headers()).status_code == 200
    db.finish(active.id, "cancelled")
    assert client.post(block_path + "/update", headers=headers(), json={"enabled": False}).json()["enabled"] is False
    before = snapshot(db)
    preview = client.post(path + "/context/preview", headers=headers(), json={"content": " now🌍 "})
    assert preview.status_code == 200 and preview.json()["measurement"] == "characters_not_tokens"
    assert snapshot(db) == before


def test_run_event_failure_and_energy_reservation_failure_rollback_blocks_summary_credits(db, repo, monkeypatch):
    chat = db.create_conversation("alice", ConversationInput())
    for index in range(3):
        pair(db, chat.id, f"q{index}", f"a{index}")
    before = snapshot(db)

    def fail(*args):
        raise RuntimeError("event failure")

    monkeypatch.setattr(db, "_event", fail)
    with pytest.raises(RuntimeError, match="event failure"):
        db.create_run("alice", chat.id, RunInput(clientMessageId=uuid4(), content="new"),
                      120000, recent_turns=1)
    assert snapshot(db) == before
    monkeypatch.undo()
    from allira.economy import EnergyRepository

    def fail_reservation(*args):
        raise APIError(402, "insufficient_energy", "not enough")

    monkeypatch.setattr(EnergyRepository, "reserve", fail_reservation)
    with error_code(402, "insufficient_energy"):
        db.create_run("alice", chat.id, RunInput(clientMessageId=uuid4(), content="new"),
                      120000, recent_turns=1)
    assert snapshot(db) == before


def test_card_block_outer_and_deferred_constraint_rollback_then_cascade(db, repo):
    chat = db.create_conversation("alice", ConversationInput())
    run = pair(db, chat.id, "q", "a")
    before = snapshot(db)
    with pytest.raises(RuntimeError), atomic(db):
        card(repo, chat.id, sourceMessageId=run.userMessageId)
        MemoryRepository(db).summarize("alice", chat.id, 0, 6000)
        raise RuntimeError("outer rollback")
    assert snapshot(db) == before
    db.conn.execute("CREATE TABLE context_constraint_probe (parent TEXT REFERENCES conversations(id) DEFERRABLE INITIALLY DEFERRED)")
    before = snapshot(db)
    with pytest.raises(sqlite3.IntegrityError), atomic(db):
        card(repo, chat.id)
        MemoryRepository(db).summarize("alice", chat.id, 0, 6000)
        db.conn.execute("INSERT INTO context_constraint_probe VALUES ('missing')")
    assert snapshot(db) == before and not db.conn.in_transaction
    saved = card(repo, chat.id, sourceMessageId=run.userMessageId)
    MemoryRepository(db).summarize("alice", chat.id, 0, 6000)
    assert repo.list_blocks("alice", chat.id).items
    db.delete_conversation("alice", chat.id)
    assert db.conn.execute("SELECT COUNT(*) FROM memory_cards").fetchone()[0] == 0
    assert db.conn.execute("SELECT COUNT(*) FROM memory_blocks").fetchone()[0] == 0
    assert saved.id


def test_single_process_concurrent_card_limit_and_preview_no_writes(client, db, repo):
    chat = db.create_conversation("alice", ConversationInput())
    for _ in range(49):
        card(repo, chat.id, content="x", enabled=False)
    app = client.app

    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://isolated") as browser:
            path = f"/api/v1/conversations/{chat.id}/memory/cards"
            return await asyncio.gather(*[browser.post(path, headers=headers(), json={
                "category": "fact", "title": "simultaneous", "content": "x", "enabled": False,
            }) for _ in range(8)])

    responses = asyncio.run(run())
    assert sum(response.status_code == 201 for response in responses) == 1
    assert sum(response.status_code == 409 for response in responses) == 7
    assert len(repo.list_cards("alice", chat.id).items) == 50
    before_hash = hashlib.sha256(json.dumps(snapshot(db), ensure_ascii=False).encode()).hexdigest()
    repo.preview("alice", chat.id, ContextPreviewInput(content="current"), db.settings)
    assert hashlib.sha256(json.dumps(snapshot(db), ensure_ascii=False).encode()).hexdigest() == before_hash
