"""User-scoped substring search, lossless snapshots and atomic completed-pair forks.

All repository operations are synchronous, use the existing SQLite connection and
never span an await. They do not invoke providers or charge/duplicate energy usage.
"""

import base64
import binascii
import hashlib
import hmac
import json
import re
import secrets
from collections.abc import Callable
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from pydantic import Field, field_validator

from .config import Settings
from .context_memory import ContextMemoryRepository, MemoryBlock, MemoryCard
from .db import APIError, Database, identifier, now
from .imports import ImportSource
from .memory import Character, ConversationMemory, MemoryRepository, MemoryUpdate
from .schemas import DTO, Conversation, Message, Page

MAX_EXPORT_MESSAGES = 50000
MAX_EXPORT_RECORDS = 50000
MAX_EXPORT_BYTES = 16 * 1024 * 1024
MAX_SEARCH_QUERY = 200
SNIPPET_CHARACTERS = 240
ExportFormat = Literal["json", "markdown"]


class ConversationSearchResult(DTO):
    conversation: Conversation
    matchedMessageId: str | None
    snippet: str


class ConversationExport(DTO):
    format: ExportFormat
    filename: str
    content: str


class ConversationArchive(DTO):
    format: Literal["allira.conversation.v1"] = "allira.conversation.v1"
    conversation: Conversation
    character: Character | None
    memory: ConversationMemory
    messages: list[Message]
    importSource: ImportSource | None
    memoryCards: list[MemoryCard] = Field(default_factory=list)
    memoryBlocks: list[MemoryBlock] = Field(default_factory=list)


class ConversationForkInput(DTO):
    clientForkId: UUID
    throughMessageId: str | None = Field(default=None, min_length=1, max_length=100)
    title: str | None = Field(default=None, min_length=1, max_length=200)

    @field_validator("title", "throughMessageId")
    @classmethod
    def valid_text(cls, value: str | None) -> str | None:
        if value is not None:
            if not value.strip():
                raise ValueError("fork parameters must not be blank")
            try:
                value.encode("utf-8")
            except UnicodeEncodeError:
                raise ValueError("fork parameters must contain valid Unicode text") from None
        # Exact original parameters, including whitespace, participate in replay.
        return value


class ConversationForkResult(DTO):
    conversation: Conversation
    replayed: bool
    copiedMessages: int


def initialize_conversation_tools(db: Database) -> None:
    """Startup-only schema initialization; never called by repository constructors."""
    db.conn.executescript("""
        CREATE INDEX IF NOT EXISTS conversations_recent
            ON conversations(ownerId, updatedAt DESC, id DESC);
        CREATE TABLE IF NOT EXISTS conversation_tool_state (
            name TEXT PRIMARY KEY, value TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS conversation_forks (
            ownerId TEXT NOT NULL, clientForkId TEXT NOT NULL, requestHash TEXT NOT NULL,
            sourceConversationId TEXT REFERENCES conversations(id) ON DELETE SET NULL,
            conversationId TEXT UNIQUE REFERENCES conversations(id) ON DELETE SET NULL,
            copiedMessages INTEGER NOT NULL, createdAt TEXT NOT NULL,
            PRIMARY KEY(ownerId, clientForkId)
        );
    """)
    with db.atomic():
        db.conn.execute(
            "INSERT OR IGNORE INTO conversation_tool_state(name, value) VALUES (?, ?)",
            ("cursor_hmac_key", secrets.token_hex(32)),
        )


# Actual run-owned adjacent pairs only; a message's 'completed' flag alone is
# insufficient (cancelled/failed runs retain a completed user-side message).
FORK_PAIR_QUERY = """
    SELECT u.id AS userId, a.id AS assistantId, u.sequence AS userSequence,
           a.sequence AS assistantSequence, u.content AS userContent,
           a.content AS assistantContent, u.createdAt AS userCreatedAt,
           a.createdAt AS assistantCreatedAt, r.createdAt AS runCreatedAt,
           r.updatedAt AS runUpdatedAt
    FROM runs r JOIN messages u ON u.id = r.userMessageId
                JOIN messages a ON a.id = r.assistantMessageId
    WHERE r.conversationId = ? AND r.status = 'completed'
      AND u.conversationId = r.conversationId AND a.conversationId = r.conversationId
      AND u.runId = r.id AND a.runId = r.id
      AND u.role = 'user' AND a.role = 'assistant'
      AND u.status = 'completed' AND a.status = 'completed'
      AND a.sequence = u.sequence + 1
"""


def _too_large(kind: str = "export") -> APIError:
    return APIError(413, f"{kind}_too_large", "At most 50000 messages or combined archive records and 16 MiB are supported; no data was truncated.")


def _encoded_size(value: str) -> int:
    return len(value.encode("utf-8"))


def _base64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _unbase64(value: str) -> bytes:
    return base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)


def _snippet(content: str, query: str) -> str:
    # SQLite LIKE's case-insensitivity is ASCII-only; do not normalize the source.
    match = re.search(re.escape(query), content, flags=re.IGNORECASE | re.ASCII) if query else None
    start = max(0, match.start() - 60) if match else 0
    return content[start:start + SNIPPET_CHARACTERS]


def _fenced(content: str, language: str = "text") -> str:
    """Fence every untrusted value with more backticks than occur in its text."""
    longest = max((len(match.group()) for match in re.finditer(r"`+", content)), default=0)
    fence = "`" * max(3, longest + 1)
    # The added delimiter newline belongs to the container, not the source text.
    return fence + language + "\n" + content + "\n" + fence + "\n"


class ConversationToolsRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def _cursor_key(self) -> bytes:
        row = self.db.conn.execute(
            "SELECT value FROM conversation_tool_state WHERE name = ?", ("cursor_hmac_key",),
        ).fetchone()
        if row is None:
            raise RuntimeError("Conversation tools must be initialized at database startup.")
        return bytes.fromhex(row["value"])

    @staticmethod
    def _binding(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    def _encode_cursor(self, user: str, query: str, conversation: Conversation) -> str:
        payload = json.dumps({
            "v": 1, "o": self._binding(user), "q": self._binding(query),
            "t": conversation.updatedAt, "i": conversation.id,
        }, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return _base64(payload) + "." + _base64(hmac.digest(self._cursor_key(), payload, "sha256"))

    def _decode_cursor(self, user: str, query: str, cursor: str) -> tuple[str, str]:
        try:
            if len(cursor) > 2048:
                raise ValueError
            encoded, signature = cursor.split(".")
            payload = _unbase64(encoded)
            if not hmac.compare_digest(_unbase64(signature), hmac.digest(self._cursor_key(), payload, "sha256")):
                raise ValueError
            data = json.loads(payload)
            if (not isinstance(data, dict) or set(data) != {"v", "o", "q", "t", "i"}
                    or data["v"] != 1 or data["o"] != self._binding(user)
                    or data["q"] != self._binding(query)
                    or not isinstance(data["t"], str) or not 1 <= len(data["t"]) <= 100
                    or not isinstance(data["i"], str) or not 1 <= len(data["i"]) <= 100):
                raise ValueError
            # The signed keyset need not still exist: deleting the anchor is safe.
            return data["t"], data["i"]
        except (ValueError, TypeError, KeyError, UnicodeError, binascii.Error):
            raise APIError(400, "invalid_cursor", "Search cursor is not valid for this user and query.") from None

    def search(
        self, user: str, query: str = "", cursor: str | None = None, limit: int = 20,
    ) -> Page[ConversationSearchResult]:
        try:
            query.encode("utf-8")
        except UnicodeEncodeError:
            raise APIError(422, "invalid_query", "The search query must be valid Unicode.") from None
        if len(query) > MAX_SEARCH_QUERY or "\x00" in query or not 1 <= limit <= 100:
            raise APIError(422, "invalid_query", "Use a query of at most 200 characters without NUL and limit 1..100.")
        pattern = "%" + query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        predicate = ""
        parameters: list[Any] = [user]
        if query:
            predicate += (
                " AND (c.title LIKE ? ESCAPE '\\' OR instr(lower(c.title), lower(?)) > 0 "
                "OR c.instructions LIKE ? ESCAPE '\\' OR instr(lower(c.instructions), lower(?)) > 0 "
                "OR EXISTS (SELECT 1 FROM messages m WHERE m.conversationId = c.id "
                "AND (m.content LIKE ? ESCAPE '\\' OR instr(lower(m.content), lower(?)) > 0)))"
            )
            # LIKE stops at embedded NUL; instr is a literal, ASCII-casefolded
            # fallback that can still search the complete stored Unicode text.
            parameters.extend((pattern, query, pattern, query, pattern, query))
        if cursor is not None:
            stamp, conversation_id = self._decode_cursor(user, query, cursor)
            predicate += " AND (c.updatedAt < ? OR (c.updatedAt = ? AND c.id < ?))"
            parameters.extend((stamp, stamp, conversation_id))
        rows = self.db.conn.execute(
            "SELECT c.id, c.title, c.instructions, c.createdAt, c.updatedAt FROM conversations c "
            "WHERE c.ownerId = ?" + predicate + " ORDER BY c.updatedAt DESC, c.id DESC LIMIT ?",
            (*parameters, limit + 1),
        ).fetchall()
        items = []
        for row in rows[:limit]:
            conversation = Conversation(**dict(row))
            message = None
            if query:
                message = self.db.conn.execute(
                    "SELECT m.id, m.content FROM messages m JOIN conversations c ON c.id = m.conversationId "
                    "WHERE c.ownerId = ? AND c.id = ? "
                    "AND (m.content LIKE ? ESCAPE '\\' OR instr(lower(m.content), lower(?)) > 0) "
                    "ORDER BY m.sequence, m.id LIMIT 1", (user, conversation.id, pattern, query),
                ).fetchone()
            fallback = conversation.title
            if query and not re.search(re.escape(query), fallback, flags=re.IGNORECASE | re.ASCII):
                fallback = conversation.instructions or conversation.title
            items.append(ConversationSearchResult(
                conversation=conversation, matchedMessageId=message["id"] if message else None,
                snippet=_snippet(message["content"] if message else fallback, query),
            ))
        return Page[ConversationSearchResult](
            items=items,
            nextCursor=self._encode_cursor(user, query, items[-1].conversation) if len(rows) > limit else None,
        )

    def _archive(self, user: str, conversation_id: str) -> ConversationArchive:
        conversation = self.db.conversation(user, conversation_id)
        self.db.require_inactive(conversation_id)
        count = self.db.conn.execute(
            "SELECT COUNT(*) FROM messages WHERE conversationId = ?", (conversation_id,),
        ).fetchone()[0]
        context_counts = [self.db.conn.execute(
            f"SELECT COUNT(*) FROM {table} WHERE conversationId = ?", (conversation_id,),
        ).fetchone()[0] for table in ("memory_cards", "memory_blocks")]
        if count > MAX_EXPORT_MESSAGES or count + sum(context_counts) > MAX_EXPORT_RECORDS:
            raise _too_large()
        # Reject oversized content before materializing context-memory DTOs. The
        # final encoded archive is measured again, including JSON escaping.
        context_bytes = self.db.conn.execute(
            "SELECT COALESCE(SUM(length(CAST(content AS BLOB)) + length(CAST(title AS BLOB))), 0) "
            "FROM memory_cards WHERE conversationId = ?", (conversation_id,),
        ).fetchone()[0] + self.db.conn.execute(
            "SELECT COALESCE(SUM(length(CAST(summary AS BLOB)) + length(CAST(sourceMessageIds AS BLOB))), 0) "
            "FROM memory_blocks WHERE conversationId = ?", (conversation_id,),
        ).fetchone()[0]
        if context_bytes > MAX_EXPORT_BYTES:
            raise _too_large()
        state = ContextMemoryRepository(self.db).export_state(user, conversation_id)
        memory_repo = MemoryRepository(self.db)
        memory = memory_repo.memory(user, conversation_id)
        character = memory_repo.character(user, memory.characterId) if memory.characterId else None
        raw = self.db.conn.execute(
            "SELECT format, content, archivedMessages FROM history_imports "
            "WHERE ownerId = ? AND conversationId = ?", (user, conversation_id),
        ).fetchone()
        import_source = ImportSource(**dict(raw)) if raw else None
        total = _encoded_size(conversation.model_dump_json()) + _encoded_size(memory.model_dump_json())
        if character:
            total += _encoded_size(character.model_dump_json())
        if import_source:
            total += _encoded_size(import_source.model_dump_json())
        total += sum(_encoded_size(item.model_dump_json())
                     for group in (state["cards"], state["blocks"]) for item in group)
        if total > MAX_EXPORT_BYTES:
            raise _too_large()
        messages = []
        rows = self.db.conn.execute(
            "SELECT id, conversationId, runId, sequence, role, content, status, createdAt "
            "FROM messages WHERE conversationId = ? ORDER BY sequence, id", (conversation_id,),
        )
        for row in rows:
            message = Message(**dict(row))
            total += _encoded_size(message.model_dump_json())
            if total > MAX_EXPORT_BYTES:
                raise _too_large()
            messages.append(message)
        return ConversationArchive(
            conversation=conversation, character=character, memory=memory,
            messages=messages, importSource=import_source,
            memoryCards=state["cards"], memoryBlocks=state["blocks"],
        )

    @staticmethod
    def _markdown(archive: ConversationArchive) -> str:
        parts = ["# Allira conversation archive v1\n\n", "## Conversation\n\n",
                 _fenced(archive.conversation.model_dump_json(indent=2), "json"),
                 "\n## Character snapshot\n\n",
                 _fenced(archive.character.model_dump_json(indent=2) if archive.character else "null", "json"),
                 "\n## Memory snapshot\n\n", _fenced(archive.memory.model_dump_json(indent=2), "json"),
                  "\n## User-confirmed memory cards\n\n",
                  _fenced(json.dumps([item.model_dump() for item in archive.memoryCards], ensure_ascii=False, indent=2), "json"),
                  "\n## Source-bound extract blocks\n\n",
                  _fenced(json.dumps([item.model_dump() for item in archive.memoryBlocks], ensure_ascii=False, indent=2), "json"),
                 "\n## Messages (original text inside fences)\n"]
        size = sum(_encoded_size(part) for part in parts)
        if size > MAX_EXPORT_BYTES:
            raise _too_large()
        for message in archive.messages:
            chunk = (
                f"\n### Message {message.sequence} | {message.role} | {message.status}\n\n",
                _fenced(message.model_dump_json(exclude={"content"}, indent=2), "json"),
                "\n#### Content\n\n", _fenced(message.content),
            )
            size += sum(_encoded_size(part) for part in chunk)
            if size > MAX_EXPORT_BYTES:
                raise _too_large()
            parts.extend(chunk)
        parts.append("\n## Original import source\n\n")
        if archive.importSource:
            parts.extend((_fenced(archive.importSource.model_dump_json(exclude={"content"}, indent=2), "json"),
                          "\n", _fenced(archive.importSource.content)))
        else:
            parts.append(_fenced("null", "json"))
        return "".join(parts)

    def export(self, user: str, conversation_id: str, format: ExportFormat = "json") -> ConversationExport:
        if format not in ("json", "markdown"):
            raise APIError(422, "invalid_format", "Export format must be json or markdown.")
        with self.db.atomic():
            archive = self._archive(user, conversation_id)
            content = archive.model_dump_json(indent=2) if format == "json" else self._markdown(archive)
            if _encoded_size(content) > MAX_EXPORT_BYTES:
                raise _too_large()
        # Generated IDs, not user titles, determine download filenames.
        safe_id = re.sub(r"[^A-Za-z0-9_-]", "_", conversation_id)[:100]
        return ConversationExport(format=format, filename=f"allira-{safe_id}.{'json' if format == 'json' else 'md'}", content=content)

    def _insert_pair(self, conversation_id: str, pair: Any, sequence: int) -> None:
        run_id, user_id, assistant_id = identifier(), identifier(), identifier()
        self.db.conn.execute(
            "INSERT INTO runs (id, conversationId, clientMessageId, userMessageId, assistantMessageId, "
            "status, errorCode, createdAt, updatedAt) VALUES (?, ?, ?, ?, ?, 'completed', NULL, ?, ?)",
            (run_id, conversation_id, identifier(), user_id, assistant_id,
             pair["runCreatedAt"], pair["runUpdatedAt"]),
        )
        for offset, role, message_id, content, stamp in (
            (1, "user", user_id, pair["userContent"], pair["userCreatedAt"]),
            (2, "assistant", assistant_id, pair["assistantContent"], pair["assistantCreatedAt"]),
        ):
            self.db.conn.execute(
                "INSERT INTO messages (id, conversationId, runId, sequence, role, content, status, createdAt) "
                "VALUES (?, ?, ?, ?, ?, ?, 'completed', ?)",
                (message_id, conversation_id, run_id, sequence + offset, role, content, stamp),
            )
        self.db._event(run_id, "run.completed", {
            "run": self.db.run_internal(run_id).model_dump(),
            "message": self.db.message(assistant_id).model_dump(),
        })

    def fork(self, user: str, conversation_id: str, body: ConversationForkInput) -> ConversationForkResult:
        raw_parameters = {"sourceConversationId": conversation_id,
                          "parameters": body.model_dump(mode="json", exclude_unset=True)}
        request_hash = hashlib.sha256(json.dumps(
            raw_parameters, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")).hexdigest()
        with self.db.atomic():
            previous = self.db.conn.execute(
                "SELECT requestHash, conversationId, copiedMessages FROM conversation_forks "
                "WHERE ownerId = ? AND clientForkId = ?", (user, str(body.clientForkId)),
            ).fetchone()
            if previous is not None:
                if previous["requestHash"] != request_hash:
                    raise APIError(409, "idempotency_conflict", "Fork ID has different source or parameters.")
                return ConversationForkResult(
                    conversation=self.db.conversation(user, previous["conversationId"]),
                    replayed=True, copiedMessages=previous["copiedMessages"],
                )
            source = self.db.conversation(user, conversation_id)
            self.db.require_inactive(conversation_id)
            boundary = 9223372036854775807
            if body.throughMessageId is not None:
                point = self.db.conn.execute(
                    "SELECT id, sequence FROM messages WHERE id = ? AND conversationId = ?",
                    (body.throughMessageId, conversation_id),
                ).fetchone()
                if point is None:
                    raise APIError(404, "not_found", "Fork message not found.")
                valid = self.db.conn.execute(
                    FORK_PAIR_QUERY + " AND a.id = ?", (conversation_id, body.throughMessageId),
                ).fetchone()
                if valid is None:
                    raise APIError(400, "invalid_fork_point", "Fork point must end a completed adjacent user/assistant pair.")
                boundary = point["sequence"]
            pairs = []
            size = 0
            for pair in self.db.conn.execute(
                FORK_PAIR_QUERY + " AND a.sequence <= ? ORDER BY u.sequence", (conversation_id, boundary),
            ):
                if len(pairs) * 2 >= MAX_EXPORT_MESSAGES:
                    raise _too_large("fork")
                size += _encoded_size(pair["userContent"]) + _encoded_size(pair["assistantContent"])
                if size > MAX_EXPORT_BYTES:
                    raise _too_large("fork")
                pairs.append(pair)
            memory_repo = MemoryRepository(self.db)
            memory = memory_repo.memory(user, conversation_id)
            if memory.characterId:
                memory_repo.character(user, memory.characterId)
            stamp, target_id = now(), identifier()
            title = body.title if body.title is not None else (source.title[:196] + "（分叉）")
            self.db.conn.execute("INSERT OR IGNORE INTO users VALUES (?, ?)", (user, stamp))
            self.db.conn.execute(
                "INSERT INTO conversations (id, ownerId, title, instructions, createdAt, updatedAt) "
                "VALUES (?, ?, ?, ?, ?, ?)", (target_id, user, title, source.instructions, stamp, stamp),
            )
            memory_repo.update_memory(user, target_id, MemoryUpdate(
                characterId=memory.characterId, coreMemory=memory.coreMemory, relationship=memory.relationship,
            ))
            # Never copy a source summary, cards or blocks: even a hand-written
            # card can describe facts learned after an earlier fork point. Current
            # role/core/relationship snapshots retain the existing explicit policy.
            for index, pair in enumerate(pairs):
                self._insert_pair(target_id, pair, index * 2)
            self.db.conn.execute(
                "INSERT INTO conversation_forks (ownerId, clientForkId, requestHash, sourceConversationId, "
                "conversationId, copiedMessages, createdAt) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (user, str(body.clientForkId), request_hash, conversation_id, target_id, len(pairs) * 2, stamp),
            )
            return ConversationForkResult(
                conversation=self.db.conversation(user, target_id), replayed=False, copiedMessages=len(pairs) * 2,
            )


def conversation_tools_router(settings: Settings, current_user: Callable[..., str]) -> APIRouter:
    router = APIRouter(prefix="/api/v1", tags=["conversation-tools"])
    User = Annotated[str, Depends(current_user)]

    def repo(request: Request) -> ConversationToolsRepository:
        return ConversationToolsRepository(request.app.state.db)

    @router.get("/conversation-search", response_model=Page[ConversationSearchResult])
    async def search(
        request: Request, user: User,
        q: Annotated[str, Query(max_length=MAX_SEARCH_QUERY)] = "",
        cursor: Annotated[str | None, Query(max_length=2048)] = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 20,
    ):
        return repo(request).search(user, q, cursor, limit)

    @router.get("/conversations/{conversation_id}/export", response_model=ConversationExport)
    async def export(conversation_id: str, request: Request, user: User, format: ExportFormat = "json"):
        return repo(request).export(user, conversation_id, format)

    @router.post("/conversations/{conversation_id}/fork", response_model=ConversationForkResult, status_code=201)
    async def fork(conversation_id: str, body: ConversationForkInput, request: Request, user: User):
        return repo(request).fork(user, conversation_id, body)

    return router
