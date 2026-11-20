"""Owned characters and bounded, rule-based conversation memory.

The summary is an extractive rolling note, not semantic long-term memory. Historical
text is never interpreted as message-role metadata. All transactions are synchronous
SAVEPOINTs so callers may compose them with a run/import transaction without an
inner context manager committing the caller's work.
"""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from pydantic import Field, field_validator, model_validator

from .config import Settings
from .db import APIError, Database, identifier, now
from .providers import PromptMessage
from .schemas import DTO, OK, Page


def _unicode_text(value: str) -> str:
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        raise ValueError("text must contain valid Unicode") from None
    return value


class CharacterInput(DTO):
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=20000)
    instructions: str = Field(default="", max_length=20000)

    @field_validator("name", "description", "instructions")
    @classmethod
    def valid_unicode(cls, value: str) -> str:
        return _unicode_text(value)

    @field_validator("name")
    @classmethod
    def nonblank_name(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("name must not be blank")
        return value.strip()


class CharacterUpdate(DTO):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=20000)
    instructions: str | None = Field(default=None, max_length=20000)

    @model_validator(mode="after")
    def valid_changes(self) -> "CharacterUpdate":
        if not self.model_fields_set:
            raise ValueError("at least one field is required")
        if any(getattr(self, key) is None for key in self.model_fields_set):
            raise ValueError("character fields must not be null")
        for key in self.model_fields_set:
            _unicode_text(getattr(self, key))
        if self.name is not None:
            if not self.name.strip():
                raise ValueError("name must not be blank")
            self.name = self.name.strip()
        return self


class Character(CharacterInput):
    id: str
    createdAt: str
    updatedAt: str


class ConversationMemory(DTO):
    conversationId: str
    characterId: str | None
    coreMemory: str
    relationship: str
    summary: str
    summarizedThrough: int
    updatedAt: str


class MemoryUpdate(DTO):
    characterId: str | None = Field(default=None, min_length=1, max_length=100)
    coreMemory: str = Field(default="", max_length=20000)
    relationship: str = Field(default="", max_length=20000)

    @model_validator(mode="after")
    def require_change(self) -> "MemoryUpdate":
        if not self.model_fields_set:
            raise ValueError("at least one field is required")
        for key in self.model_fields_set:
            value = getattr(self, key)
            if value is not None:
                _unicode_text(value)
        return self


@contextmanager
def atomic(db: Database) -> Iterator[None]:
    """Nest safely inside existing SQLite transactions; never span an await."""
    name = "allira_" + identifier().replace("-", "")
    db.conn.execute(f"SAVEPOINT {name}")
    try:
        yield
        # Releasing an outermost SAVEPOINT may itself fail (e.g. deferred FK).
        # Keep that failure inside rollback handling so no partial import remains.
        db.conn.execute(f"RELEASE SAVEPOINT {name}")
    except BaseException:
        if db.conn.in_transaction:
            db.conn.execute(f"ROLLBACK TO SAVEPOINT {name}")
            db.conn.execute(f"RELEASE SAVEPOINT {name}")
        raise


def initialize_memory(db: Database) -> None:
    db.conn.executescript("""
        CREATE TABLE IF NOT EXISTS characters (
            id TEXT PRIMARY KEY, ownerId TEXT NOT NULL, name TEXT NOT NULL,
            description TEXT NOT NULL, instructions TEXT NOT NULL,
            createdAt TEXT NOT NULL, updatedAt TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS characters_owner ON characters(ownerId, id);
        CREATE TABLE IF NOT EXISTS conversation_memory (
            conversationId TEXT PRIMARY KEY REFERENCES conversations(id) ON DELETE CASCADE,
            characterId TEXT REFERENCES characters(id), coreMemory TEXT NOT NULL DEFAULT '',
            relationship TEXT NOT NULL DEFAULT '', summary TEXT NOT NULL DEFAULT '',
            summarizedThrough INTEGER NOT NULL DEFAULT 0, updatedAt TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS conversation_memory_character
            ON conversation_memory(characterId);
    """)


# Joining through the exact run-owned IDs excludes failed/partial, malformed and
# imported unpaired messages even if some unrelated message says "completed".
PAIR_QUERY = """
    SELECT u.id AS userId, a.id AS assistantId,
           u.sequence AS userSequence, a.sequence AS assistantSequence,
           u.content AS userContent, a.content AS assistantContent
    FROM runs r JOIN messages u ON u.id = r.userMessageId
                JOIN messages a ON a.id = r.assistantMessageId
    WHERE r.conversationId = ? AND r.status = 'completed'
      AND u.conversationId = r.conversationId AND a.conversationId = r.conversationId
      AND u.runId = r.id AND a.runId = r.id
      AND u.role = 'user' AND a.role = 'assistant'
      AND u.status = 'completed' AND a.status = 'completed'
      AND a.sequence = u.sequence + 1
"""


class MemoryRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def character(self, user: str, character_id: str) -> Character:
        row = self.db.conn.execute(
            "SELECT id, name, description, instructions, createdAt, updatedAt FROM characters "
            "WHERE id = ? AND ownerId = ?", (character_id, user),
        ).fetchone()
        if row is None:
            raise APIError(404, "not_found", "Character not found.")
        return Character(**dict(row))

    def list_characters(self, user: str, cursor: str | None, limit: int) -> dict[str, Any]:
        if cursor:
            try:
                self.character(user, cursor)
            except APIError:
                raise APIError(400, "invalid_cursor", "Character cursor is not valid.") from None
        rows = self.db.conn.execute(
            "SELECT id, name, description, instructions, createdAt, updatedAt FROM characters "
            "WHERE ownerId = ? AND id > ? ORDER BY id LIMIT ?", (user, cursor or "", limit + 1),
        ).fetchall()
        return {"items": [dict(row) for row in rows[:limit]],
                "nextCursor": rows[limit - 1]["id"] if len(rows) > limit else None}

    def create_character(self, user: str, body: CharacterInput) -> Character:
        stamp = now()
        character = Character(**body.model_dump(), id=identifier(), createdAt=stamp, updatedAt=stamp)
        with atomic(self.db):
            self.db.conn.execute(
                "INSERT INTO characters "
                "(id, ownerId, name, description, instructions, createdAt, updatedAt) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (character.id, user, character.name, character.description,
                 character.instructions, stamp, stamp),
            )
        return character

    def update_character(self, user: str, character_id: str, body: CharacterUpdate) -> Character:
        with atomic(self.db):
            self.character(user, character_id)
            active = self.db.conn.execute(
                "SELECT 1 FROM conversation_memory m JOIN runs r "
                "ON r.conversationId = m.conversationId "
                "WHERE m.characterId = ? AND r.status = 'running' LIMIT 1", (character_id,),
            ).fetchone()
            if active:
                raise APIError(409, "run_in_progress", "This character has an active conversation.")
            changes = body.model_dump(exclude_unset=True)
            assignments = ", ".join(f"{key} = ?" for key in changes)
            self.db.conn.execute(
                f"UPDATE characters SET {assignments}, updatedAt = ? WHERE id = ? AND ownerId = ?",
                (*changes.values(), now(), character_id, user),
            )
        return self.character(user, character_id)

    def delete_character(self, user: str, character_id: str) -> None:
        with atomic(self.db):
            self.character(user, character_id)
            if self.db.conn.execute(
                "SELECT 1 FROM conversation_memory WHERE characterId = ? LIMIT 1", (character_id,),
            ).fetchone():
                raise APIError(409, "character_in_use", "This character is used by a conversation.")
            self.db.conn.execute(
                "DELETE FROM characters WHERE id = ? AND ownerId = ?", (character_id, user),
            )

    def memory(self, user: str, conversation_id: str) -> ConversationMemory:
        conversation = self.db.conversation(user, conversation_id)
        row = self.db.conn.execute(
            "SELECT * FROM conversation_memory WHERE conversationId = ?", (conversation_id,),
        ).fetchone()
        if row is not None:
            return ConversationMemory(**dict(row))
        return ConversationMemory(
            conversationId=conversation_id, characterId=None, coreMemory="", relationship="",
            summary="", summarizedThrough=0, updatedAt=conversation.updatedAt,
        )

    def _store_memory(self, memory: ConversationMemory) -> None:
        self.db.conn.execute(
            "INSERT INTO conversation_memory "
            "(conversationId, characterId, coreMemory, relationship, summary, "
            "summarizedThrough, updatedAt) VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(conversationId) DO UPDATE SET "
            "characterId=excluded.characterId, coreMemory=excluded.coreMemory, "
            "relationship=excluded.relationship, summary=excluded.summary, "
            "summarizedThrough=excluded.summarizedThrough, updatedAt=excluded.updatedAt",
            tuple(memory.model_dump().values()),
        )

    def update_memory(
        self, user: str, conversation_id: str, body: MemoryUpdate,
    ) -> ConversationMemory:
        with atomic(self.db):
            memory = self.memory(user, conversation_id)
            self.db.require_inactive(conversation_id)
            if "characterId" in body.model_fields_set and body.characterId is not None:
                self.character(user, body.characterId)
            memory = memory.model_copy(update={**body.model_dump(exclude_unset=True),
                                              "updatedAt": now()})
            self._store_memory(memory)
        return memory

    def _recent_pairs(self, conversation_id: str, recent_turns: int) -> list[Any]:
        rows = self.db.conn.execute(
            PAIR_QUERY + " ORDER BY u.sequence DESC LIMIT ?", (conversation_id, recent_turns),
        ).fetchall()
        return list(reversed(rows))

    def _summarize(
        self, memory: ConversationMemory, recent_turns: int, summary_max_characters: int,
    ) -> ConversationMemory:
        """The old summary DTO is now a bounded view over durable exact-pair notes."""
        from .context_memory import ContextMemoryRepository

        memory, _ = ContextMemoryRepository(self.db).fold_history(
            memory, recent_turns, summary_max_characters,
        )
        return memory

    @staticmethod
    def _validate_limits(recent_turns: int, summary_max_characters: int) -> None:
        if not 0 <= recent_turns <= 1000 or not 0 <= summary_max_characters <= 1000000:
            raise ValueError("memory limits are outside their supported range")

    def summarize(
        self, user: str, conversation_id: str, recent_turns: int = 8,
        summary_max_characters: int = 6000,
    ) -> ConversationMemory:
        self._validate_limits(recent_turns, summary_max_characters)
        with atomic(self.db):
            memory = self.memory(user, conversation_id)
            self.db.require_inactive(conversation_id)
            return self._summarize(memory, recent_turns, summary_max_characters)

    def build_prompt(
        self, user: str, conversation_id: str, text: str, max_characters: int,
        recent_turns: int = 8, summary_max_characters: int = 6000,
    ) -> list[PromptMessage]:
        from .context_memory import ContextMemoryRepository

        # One shared planner for both actual run creation and a rollback-only preview.
        # The root run's enclosing transaction also owns new blocks and summary writes.
        return ContextMemoryRepository(self.db).plan_prompt(
            user, conversation_id, text, max_characters, recent_turns, summary_max_characters,
        ).messages


def memory_router(settings: Settings, current_user: Callable[..., str]) -> APIRouter:
    router = APIRouter(prefix="/api/v1", tags=["memory"])
    User = Annotated[str, Depends(current_user)]

    def repo(request: Request) -> MemoryRepository:
        return MemoryRepository(request.app.state.db)

    @router.get("/characters", response_model=Page[Character])
    async def list_characters(
        request: Request, user: User, cursor: str | None = None,
        limit: int = Query(default=50, ge=1, le=100),
    ):
        return repo(request).list_characters(user, cursor, limit)

    @router.post("/characters", response_model=Character, status_code=201)
    async def create_character(body: CharacterInput, request: Request, user: User):
        return repo(request).create_character(user, body)

    @router.get("/characters/{character_id}", response_model=Character)
    async def get_character(character_id: str, request: Request, user: User):
        return repo(request).character(user, character_id)

    @router.post("/characters/{character_id}/update", response_model=Character)
    async def update_character(
        character_id: str, body: CharacterUpdate, request: Request, user: User,
    ):
        return repo(request).update_character(user, character_id, body)

    @router.post("/characters/{character_id}/delete", response_model=OK)
    async def delete_character(character_id: str, request: Request, user: User):
        repo(request).delete_character(user, character_id)
        return OK()

    @router.get("/conversations/{conversation_id}/memory", response_model=ConversationMemory)
    async def get_memory(conversation_id: str, request: Request, user: User):
        return repo(request).memory(user, conversation_id)

    @router.post("/conversations/{conversation_id}/memory", response_model=ConversationMemory)
    async def update_memory(
        conversation_id: str, body: MemoryUpdate, request: Request, user: User,
    ):
        return repo(request).update_memory(user, conversation_id, body)

    @router.post("/conversations/{conversation_id}/memory/summarize", response_model=ConversationMemory)
    async def summarize_memory(conversation_id: str, request: Request, user: User):
        return repo(request).summarize(
            user, conversation_id, getattr(settings, "context_recent_turns", 8),
            getattr(settings, "summary_max_characters", 6000),
        )

    return router
