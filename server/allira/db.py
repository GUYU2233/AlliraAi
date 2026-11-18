"""SQLite development repository. All methods execute on the application's event loop.

Transactions never span an await. Run state, message text and replay events commit together.
This backend deliberately supports one API process; see the architecture document.
"""

import hashlib
import json
import sqlite3
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from .providers import PromptMessage
from .schemas import Conversation, ConversationInput, Message, Run, RunInput


class APIError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        self.status, self.code, self.message = status, code, message


def now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def identifier() -> str:
    return str(uuid4())


class Database:
    def __init__(self, path: str) -> None:
        if path != ":memory:":
            path = str(Path(path).expanduser().resolve())
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.execute("PRAGMA journal_mode = WAL")
        self.conn.execute("PRAGMA busy_timeout = 5000")
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS sessions (
                tokenHash TEXT PRIMARY KEY, userId TEXT NOT NULL, expiresAt REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS conversations (
                id TEXT PRIMARY KEY, ownerId TEXT NOT NULL, title TEXT NOT NULL,
                instructions TEXT, createdAt TEXT NOT NULL, updatedAt TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS conversations_owner ON conversations(ownerId, id);
            CREATE TABLE IF NOT EXISTS runs (
                id TEXT PRIMARY KEY, conversationId TEXT NOT NULL REFERENCES conversations(id),
                clientMessageId TEXT NOT NULL, userMessageId TEXT NOT NULL,
                assistantMessageId TEXT NOT NULL, status TEXT NOT NULL, errorCode TEXT,
                createdAt TEXT NOT NULL, updatedAt TEXT NOT NULL,
                UNIQUE(conversationId, clientMessageId)
            );
            CREATE UNIQUE INDEX IF NOT EXISTS one_running_run
                ON runs(conversationId) WHERE status = 'running';
            CREATE TABLE IF NOT EXISTS messages (
                id TEXT PRIMARY KEY, conversationId TEXT NOT NULL REFERENCES conversations(id),
                runId TEXT NOT NULL REFERENCES runs(id), sequence INTEGER NOT NULL,
                role TEXT NOT NULL, content TEXT NOT NULL, status TEXT NOT NULL,
                createdAt TEXT NOT NULL, UNIQUE(conversationId, sequence)
            );
            CREATE TABLE IF NOT EXISTS run_events (
                runId TEXT NOT NULL REFERENCES runs(id), eventId INTEGER NOT NULL,
                type TEXT NOT NULL, data TEXT NOT NULL, PRIMARY KEY(runId, eventId)
            );
        """)

    def close(self) -> None:
        self.conn.close()

    def create_session(self, token: str, user: str, ttl: int) -> None:
        with self.conn:
            self.conn.execute("DELETE FROM sessions WHERE expiresAt <= ?", (time.time(),))
            self.conn.execute(
                "INSERT INTO sessions VALUES (?, ?, ?)",
                (hashlib.sha256(token.encode()).hexdigest(), user, time.time() + ttl),
            )

    def authenticate(self, token: str) -> str:
        row = self.conn.execute(
            "SELECT userId FROM sessions WHERE tokenHash = ? AND expiresAt > ?",
            (hashlib.sha256(token.encode()).hexdigest(), time.time()),
        ).fetchone()
        if not row:
            raise APIError(401, "unauthorized", "A valid session is required.")
        return row["userId"]

    def conversation(self, user: str, conversation_id: str) -> Conversation:
        row = self.conn.execute(
            "SELECT id, title, instructions, createdAt, updatedAt FROM conversations "
            "WHERE id = ? AND ownerId = ?", (conversation_id, user),
        ).fetchone()
        if row is None:
            raise APIError(404, "not_found", "Conversation not found.")
        return Conversation(**dict(row))

    def create_conversation(self, user: str, body: ConversationInput) -> Conversation:
        conversation = Conversation(
            id=identifier(), title=body.title, instructions=body.instructions,
            createdAt=now(), updatedAt=now(),
        )
        with self.conn:
            self.conn.execute(
                "INSERT INTO conversations VALUES (?, ?, ?, ?, ?, ?)",
                (conversation.id, user, conversation.title, conversation.instructions,
                 conversation.createdAt, conversation.updatedAt),
            )
        return conversation

    def list_conversations(self, user: str, cursor: str | None, limit: int) -> dict[str, Any]:
        if cursor:
            row = self.conn.execute(
                "SELECT id FROM conversations WHERE id = ? AND ownerId = ?", (cursor, user),
            ).fetchone()
            if row is None:
                raise APIError(400, "invalid_cursor", "Conversation cursor is not valid.")
        rows = self.conn.execute(
            "SELECT id, title, instructions, createdAt, updatedAt FROM conversations "
            "WHERE ownerId = ? AND id > ? ORDER BY id LIMIT ?",
            (user, cursor or "", limit + 1),
        ).fetchall()
        return {"items": [dict(row) for row in rows[:limit]],
                "nextCursor": rows[limit - 1]["id"] if len(rows) > limit else None}

    def list_messages(
        self, user: str, conversation_id: str, cursor: str | None, limit: int,
    ) -> dict[str, Any]:
        self.conversation(user, conversation_id)
        sequence = 0
        if cursor:
            row = self.conn.execute(
                "SELECT sequence FROM messages WHERE id = ? AND conversationId = ?",
                (cursor, conversation_id),
            ).fetchone()
            if row is None:
                raise APIError(400, "invalid_cursor", "Message cursor is not valid.")
            sequence = row["sequence"]
        rows = self.conn.execute(
            "SELECT * FROM messages WHERE conversationId = ? AND sequence > ? "
            "ORDER BY sequence LIMIT ?", (conversation_id, sequence, limit + 1),
        ).fetchall()
        return {"items": [dict(row) for row in rows[:limit]],
                "nextCursor": rows[limit - 1]["id"] if len(rows) > limit else None}

    def run(self, user: str, run_id: str) -> Run:
        row = self.conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        if row is None:
            raise APIError(404, "not_found", "Run not found.")
        self.conversation(user, row["conversationId"])
        return Run(**dict(row))

    def run_internal(self, run_id: str) -> Run:
        row = self.conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        if row is None:
            raise RuntimeError("Run missing from repository.")
        return Run(**dict(row))

    def message(self, message_id: str) -> Message:
        row = self.conn.execute("SELECT * FROM messages WHERE id = ?", (message_id,)).fetchone()
        return Message(**dict(row))

    def prompt(self, conversation: Conversation, content: str) -> list[PromptMessage]:
        result: list[PromptMessage] = []
        if conversation.instructions:
            result.append({"role": "system", "content": conversation.instructions})
        rows = self.conn.execute(
            "SELECT m.role, m.content FROM messages m JOIN runs r ON r.id = m.runId "
            "WHERE m.conversationId = ? AND r.status = 'completed' ORDER BY m.sequence",
            (conversation.id,),
        ).fetchall()
        result.extend({"role": row["role"], "content": row["content"]} for row in rows)
        result.append({"role": "user", "content": content})
        return result

    def create_run(
        self, user: str, conversation_id: str, body: RunInput, max_characters: int,
    ) -> tuple[Run, list[PromptMessage] | None]:
        conversation = self.conversation(user, conversation_id)
        client_id = str(body.clientMessageId)
        with self.conn:
            existing = self.conn.execute(
                "SELECT * FROM runs WHERE conversationId = ? AND clientMessageId = ?",
                (conversation_id, client_id),
            ).fetchone()
            if existing:
                run = Run(**dict(existing))
                if self.message(run.userMessageId).content != body.content:
                    raise APIError(409, "idempotency_conflict", "Message ID has different content.")
                return run, None
            active = self.conn.execute(
                "SELECT 1 FROM runs WHERE conversationId = ? AND status = 'running'",
                (conversation_id,),
            ).fetchone()
            if active:
                raise APIError(409, "run_in_progress", "This conversation has an active run.")
            prompt = self.prompt(conversation, body.content)
            if sum(len(message["content"]) for message in prompt) > max_characters:
                raise APIError(413, "context_too_large", "Conversation exceeds the context safety limit.")
            stamp = now()
            run = Run(
                id=identifier(), conversationId=conversation_id, clientMessageId=client_id,
                userMessageId=identifier(), assistantMessageId=identifier(), status="running",
                errorCode=None, createdAt=stamp, updatedAt=stamp,
            )
            self.conn.execute(
                "INSERT INTO runs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                tuple(run.model_dump().values()),
            )
            sequence = self.conn.execute(
                "SELECT COALESCE(MAX(sequence), 0) FROM messages WHERE conversationId = ?",
                (conversation_id,),
            ).fetchone()[0]
            for offset, message_id, role, text, status in (
                (1, run.userMessageId, "user", body.content, "completed"),
                (2, run.assistantMessageId, "assistant", "", "generating"),
            ):
                self.conn.execute(
                    "INSERT INTO messages VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (message_id, conversation_id, run.id, sequence + offset, role, text, status, stamp),
                )
            self.conn.execute(
                "UPDATE conversations SET updatedAt = ? WHERE id = ?", (stamp, conversation_id),
            )
            self._event(run.id, "run.started", {"run": run.model_dump()})
        return run, prompt

    def _event(self, run_id: str, event_type: str, data: dict[str, Any]) -> None:
        event_id = self.max_event_id(run_id) + 1
        self.conn.execute(
            "INSERT INTO run_events VALUES (?, ?, ?, ?)",
            (run_id, event_id, event_type, json.dumps(data, ensure_ascii=False)),
        )

    def append_delta(self, run_id: str, delta: str) -> None:
        with self.conn:
            run = self.run_internal(run_id)
            if run.status != "running":
                return
            self.conn.execute(
                "UPDATE messages SET content = content || ? WHERE id = ?",
                (delta, run.assistantMessageId),
            )
            self._event(run_id, "message.delta", {"messageId": run.assistantMessageId, "delta": delta})

    def finish(self, run_id: str, status: str, error_code: str | None = None) -> Run:
        with self.conn:
            run = self.run_internal(run_id)
            if run.status != "running":
                return run
            stamp = now()
            self.conn.execute(
                "UPDATE runs SET status = ?, errorCode = ?, updatedAt = ? WHERE id = ?",
                (status, error_code, stamp, run_id),
            )
            self.conn.execute(
                "UPDATE messages SET status = ? WHERE id = ?", (status, run.assistantMessageId),
            )
            self.conn.execute(
                "UPDATE conversations SET updatedAt = ? WHERE id = ?", (stamp, run.conversationId),
            )
            run = self.run_internal(run_id)
            self._event(run_id, f"run.{status}", {
                "run": run.model_dump(), "message": self.message(run.assistantMessageId).model_dump(),
            })
        return run

    def recover_interrupted_runs(self) -> None:
        rows = self.conn.execute("SELECT id FROM runs WHERE status = 'running'").fetchall()
        for row in rows:
            self.finish(row["id"], "failed", "server_restarted")

    def max_event_id(self, run_id: str) -> int:
        return self.conn.execute(
            "SELECT COALESCE(MAX(eventId), 0) FROM run_events WHERE runId = ?", (run_id,),
        ).fetchone()[0]

    def events(self, run_id: str, after: int) -> list[dict[str, Any]]:
        return [dict(row) for row in self.conn.execute(
            "SELECT eventId, type, data FROM run_events WHERE runId = ? AND eventId > ? "
            "ORDER BY eventId LIMIT 100", (run_id, after),
        ).fetchall()]
