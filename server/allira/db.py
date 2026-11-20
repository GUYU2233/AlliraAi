"""SQLite development repository. All methods execute on the application's event loop.

Transactions never span an await. Run state, message text and replay events commit together.
This backend deliberately supports one API process; see the architecture document.
"""

import hashlib
import json
import sqlite3
import time
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from .providers import PromptMessage
from .schemas import Conversation, ConversationInput, ConversationUpdate, Message, Run, RunInput


class APIError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        self.status, self.code, self.message = status, code, message


def now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def identifier() -> str:
    return str(uuid4())


class Database:
    def __init__(self, path: str, settings=None) -> None:
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
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY, createdAt TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS admin_login_codes (
                codeHash TEXT PRIMARY KEY, expiresAt REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS admin_sessions (
                tokenHash TEXT PRIMARY KEY, expiresAt REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS admin_access_sessions (
                tokenHash TEXT PRIMARY KEY REFERENCES admin_sessions(tokenHash) ON DELETE CASCADE,
                subjectHash TEXT NOT NULL, emailHash TEXT NOT NULL,
                assertionDigest TEXT NOT NULL, expiresAt REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS admin_audit (
                id INTEGER PRIMARY KEY AUTOINCREMENT, action TEXT NOT NULL,
                targetType TEXT NOT NULL, targetId TEXT, createdAt TEXT NOT NULL,
                ownerId TEXT
            );
            INSERT OR IGNORE INTO users
                SELECT ownerId, MIN(createdAt) FROM conversations GROUP BY ownerId;
            INSERT OR IGNORE INTO users
                SELECT DISTINCT userId, strftime('%Y-%m-%dT%H:%M:%fZ', 'now') FROM sessions;
        """)
        self._migrate_sessions()
        from .account_security import initialize_account_security
        from .accounts import initialize_accounts
        from .companion import initialize_companion
        from .config import Settings
        from .context_memory import initialize_context_memory
        from .conversation_tools import initialize_conversation_tools
        from .economy import initialize_economy
        from .imports import initialize_imports
        from .oidc_identity import initialize_oidc_identity
        from .phone_identity import initialize_phone_identity
        from .social_identity import initialize_social_identity

        self.settings = settings or Settings(_env_file=None, database_path=path)
        initialize_accounts(self)
        initialize_account_security(self)
        initialize_phone_identity(self)
        initialize_social_identity(self)
        initialize_oidc_identity(self)
        initialize_economy(self)
        initialize_imports(self)
        initialize_context_memory(self)
        initialize_conversation_tools(self)
        initialize_companion(self)
        self._migrate_audit_owners()
        if self.conn.execute("PRAGMA user_version").fetchone()[0] < 8:
            # Existing business accounts and chats are unchanged; new rows only bind
            # an administrator's local CSRF secret to an independently verified JWT.
            self.conn.execute("PRAGMA user_version = 8")

    def close(self) -> None:
        self.conn.close()

    @contextmanager
    def atomic(self):
        """A nested-safe transaction; unlike Connection.__exit__, never commits its caller."""
        name = f"allira_{uuid4().hex}"
        self.conn.execute(f"SAVEPOINT {name}")
        try:
            yield
            self.conn.execute(f"RELEASE SAVEPOINT {name}")
        except BaseException:
            # RELEASE of the outer savepoint can itself fail (e.g. deferred FK checks).
            # Keep the original error, but never leave that failed transaction open.
            if self.conn.in_transaction:
                self.conn.execute(f"ROLLBACK TO SAVEPOINT {name}")
                self.conn.execute(f"RELEASE SAVEPOINT {name}")
            raise

    def _migrate_sessions(self) -> None:
        """Keep old tokens valid while adding non-secret, revocable device identifiers."""
        columns = {row["name"] for row in self.conn.execute("PRAGMA table_info(sessions)")}
        additions = {
            "sessionId": "TEXT", "createdAt": "TEXT", "lastSeenAt": "TEXT",
            "kind": "TEXT NOT NULL DEFAULT 'bearer'", "label": "TEXT NOT NULL DEFAULT 'SDK'",
        }
        with self.atomic():
            for name, definition in additions.items():
                if name not in columns:
                    self.conn.execute(f"ALTER TABLE sessions ADD COLUMN {name} {definition}")
            stamp = now()
            for row in self.conn.execute(
                "SELECT tokenHash FROM sessions WHERE sessionId IS NULL",
            ).fetchall():
                self.conn.execute(
                    "UPDATE sessions SET sessionId = ?, createdAt = COALESCE(createdAt, ?), "
                    "lastSeenAt = COALESCE(lastSeenAt, ?) WHERE tokenHash = ?",
                    (identifier(), stamp, stamp, row["tokenHash"]),
                )
            self.conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS sessions_id ON sessions(sessionId)")
            self.conn.execute("CREATE INDEX IF NOT EXISTS sessions_owner ON sessions(userId, expiresAt)")

    def _migrate_audit_owners(self) -> None:
        """Retain internal ownership through chat deletion, without changing the audit DTO.

        Only recover existing rows from explicit user targets or surviving resources/
        usage. A previously deleted, uncharged legacy chat cannot be attributed safely.
        """
        columns = {row["name"] for row in self.conn.execute("PRAGMA table_info(admin_audit)")}
        with self.atomic():
            if "ownerId" not in columns:
                self.conn.execute("ALTER TABLE admin_audit ADD COLUMN ownerId TEXT")
            self.conn.execute("CREATE INDEX IF NOT EXISTS admin_audit_owner ON admin_audit(ownerId)")
            self.conn.execute("""
                UPDATE admin_audit SET ownerId = CASE targetType
                    WHEN 'user' THEN targetId
                    WHEN 'conversation' THEN COALESCE(
                        (SELECT ownerId FROM conversations WHERE id = admin_audit.targetId),
                        (SELECT userId FROM run_usage WHERE conversationId = admin_audit.targetId LIMIT 1)
                    )
                    WHEN 'run' THEN COALESCE(
                        (SELECT c.ownerId FROM runs r JOIN conversations c ON c.id = r.conversationId
                         WHERE r.id = admin_audit.targetId),
                        (SELECT userId FROM run_usage WHERE runId = admin_audit.targetId)
                    )
                    WHEN 'character' THEN
                        (SELECT ownerId FROM characters WHERE id = admin_audit.targetId)
                END
                WHERE ownerId IS NULL AND targetType IN ('user', 'conversation', 'run', 'character')
            """)

    def create_session(
        self, token: str, user: str, ttl: int, *, kind: str = "bearer", label: str = "SDK",
    ) -> None:
        if kind not in {"bearer", "browser"}:
            raise ValueError("Unsupported session kind.")
        # Labels are display-only and may originate in an untrusted User-Agent header.
        label = "".join(character for character in label if character.isprintable())[:80].strip()
        label = label or ("Browser" if kind == "browser" else "SDK")
        stamp = now()
        with self.atomic():
            self.conn.execute("INSERT OR IGNORE INTO users VALUES (?, ?)", (user, stamp))
            self.conn.execute("DELETE FROM sessions WHERE expiresAt <= ?", (time.time(),))
            self.conn.execute(
                "INSERT INTO sessions (tokenHash, userId, expiresAt, sessionId, createdAt, "
                "lastSeenAt, kind, label) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (hashlib.sha256(token.encode()).hexdigest(), user, time.time() + ttl,
                 identifier(), stamp, stamp, kind, label),
            )

    def authenticate(self, token: str, *, kind: str = "bearer") -> str:
        if kind not in {"bearer", "browser"} or not token or len(token) > 200:
            raise APIError(401, "unauthorized", "A valid session is required.")
        row = self.conn.execute(
            "SELECT userId, lastSeenAt FROM sessions WHERE tokenHash = ? AND expiresAt > ? AND kind = ?",
            (hashlib.sha256(token.encode()).hexdigest(), time.time(), kind),
        ).fetchone()
        if not row:
            raise APIError(401, "unauthorized", "A valid session is required.")
        account = self.conn.execute(
            "SELECT 1 FROM accounts WHERE userId = ?", (row["userId"],),
        ).fetchone()
        if account is None and (
            self.settings.environment != "development" or not self.settings.allow_dev_auth
        ):
            # Disabling the developer route also invalidates previously issued
            # developer credentials, including sessions restored from older backups.
            raise APIError(401, "unauthorized", "A valid session is required.")
        if self.settings.auth_backend == "oidc" and account is not None and not self.conn.execute(
            "SELECT 1 FROM oidc_identities WHERE userId = ? AND issuer = ?",
            (row["userId"], self.settings.oidc_issuer_url),
        ).fetchone():
            # A pre-cutover password/OTP cookie is not proof of the external identity.
            # Explicit migration creates the mapping and revokes old sessions atomically.
            raise APIError(401, "oidc_login_required", "Sign in through the configured identity provider.")
        last_seen = row["lastSeenAt"]
        cutoff = datetime.fromtimestamp(time.time() - 60, UTC).isoformat().replace("+00:00", "Z")
        if last_seen is None or last_seen < cutoff:
            with self.atomic():
                self.conn.execute(
                    "UPDATE sessions SET lastSeenAt = ? WHERE tokenHash = ?",
                    (now(), hashlib.sha256(token.encode()).hexdigest()),
                )
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
        with self.atomic():
            self.conn.execute("INSERT OR IGNORE INTO users VALUES (?, ?)", (user, now()))
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

    def revoke_session(self, token: str) -> None:
        with self.atomic():
            self.conn.execute(
                "DELETE FROM sessions WHERE tokenHash = ?",
                (hashlib.sha256(token.encode()).hexdigest(),),
            )

    def conversation_state(self, user: str, conversation_id: str) -> dict[str, Any]:
        conversation = self.conversation(user, conversation_id)
        active = self.conn.execute(
            "SELECT * FROM runs WHERE conversationId = ? AND status = 'running'",
            (conversation_id,),
        ).fetchone()
        sequence = self.conn.execute(
            "SELECT COALESCE(MAX(sequence), 0) FROM messages WHERE conversationId = ?",
            (conversation_id,),
        ).fetchone()[0]
        return {"conversation": conversation, "activeRun": dict(active) if active else None,
                "lastSequence": sequence}

    def update_conversation(
        self, user: str, conversation_id: str, body: ConversationUpdate,
    ) -> Conversation:
        self.conversation(user, conversation_id)
        with self.atomic():
            self.require_inactive(conversation_id)
            changes = body.model_dump(exclude_unset=True)
            # Keys come only from the validated DTO, never from user-supplied SQL.
            assignments = ", ".join(f"{key} = ?" for key in changes)
            self.conn.execute(
                f"UPDATE conversations SET {assignments}, updatedAt = ? WHERE id = ?",
                (*changes.values(), now(), conversation_id),
            )
        return self.conversation(user, conversation_id)

    def require_inactive(self, conversation_id: str) -> None:
        if self.conn.execute(
            "SELECT 1 FROM runs WHERE conversationId = ? AND status = 'running'",
            (conversation_id,),
        ).fetchone():
            raise APIError(409, "run_in_progress", "This conversation has an active run.")

    def delete_conversation(
        self, user: str, conversation_id: str, *, admin: bool = False,
    ) -> None:
        self.conversation(user, conversation_id)
        with self.atomic():
            self.require_inactive(conversation_id)
            self.conn.execute(
                "DELETE FROM run_events WHERE runId IN "
                "(SELECT id FROM runs WHERE conversationId = ?)", (conversation_id,),
            )
            self.conn.execute("DELETE FROM messages WHERE conversationId = ?", (conversation_id,))
            self.conn.execute("DELETE FROM runs WHERE conversationId = ?", (conversation_id,))
            self.conn.execute("DELETE FROM conversations WHERE id = ?", (conversation_id,))
            if admin:
                self.conn.execute(
                    "INSERT INTO admin_audit (action, targetType, targetId, createdAt, ownerId) "
                    "VALUES ('conversation.deleted', 'conversation', ?, ?, ?)",
                    (conversation_id, now(), user),
                )

    def list_recent_messages(
        self, user: str, conversation_id: str, before: str | None, limit: int,
    ) -> dict[str, Any]:
        self.conversation(user, conversation_id)
        sequence = 9223372036854775807
        if before:
            row = self.conn.execute(
                "SELECT sequence FROM messages WHERE id = ? AND conversationId = ?",
                (before, conversation_id),
            ).fetchone()
            if row is None:
                raise APIError(400, "invalid_cursor", "Message cursor is not valid.")
            sequence = row["sequence"]
        rows = self.conn.execute(
            "SELECT * FROM messages WHERE conversationId = ? AND sequence < ? "
            "ORDER BY sequence DESC LIMIT ?", (conversation_id, sequence, limit + 1),
        ).fetchall()
        items = [dict(row) for row in reversed(rows[:limit])]
        return {"items": items,
                "nextCursor": items[0]["id"] if len(rows) > limit else None}

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
        max_concurrent_runs: int = 8, *, recent_turns: int = 8,
        summary_max_characters: int = 6000, in_flight_run_ids: set[str] | None = None,
    ) -> tuple[Run, list[PromptMessage] | None]:
        self.conversation(user, conversation_id)
        client_id = str(body.clientMessageId)
        with self.atomic():
            existing = self.conn.execute(
                "SELECT * FROM runs WHERE conversationId = ? AND clientMessageId = ?",
                (conversation_id, client_id),
            ).fetchone()
            if existing:
                run = Run(**dict(existing))
                usage = self.conn.execute("SELECT requestedMode FROM run_usage WHERE runId = ?", (run.id,)).fetchone()
                requested_mode = usage["requestedMode"] if usage else "economy"
                if self.message(run.userMessageId).content != body.content or requested_mode != body.mode:
                    raise APIError(409, "idempotency_conflict", "Message ID has different content or mode.")
                return run, None
            active = self.conn.execute(
                "SELECT 1 FROM runs WHERE conversationId = ? AND status = 'running'",
                (conversation_id,),
            ).fetchone()
            if active:
                raise APIError(409, "run_in_progress", "This conversation has an active run.")
            running_ids = {row["id"] for row in self.conn.execute(
                "SELECT id FROM runs WHERE status = 'running'",
            ).fetchall()}
            # Count the union: a cancelled task may still hold an upstream socket while closing.
            occupied = running_ids | (in_flight_run_ids or set())
            if len(occupied) >= max_concurrent_runs:
                raise APIError(429, "capacity_exceeded", "The generation capacity limit is reached.")
            from .safety import check_input
            check_input(body.content, self.settings)
            from .memory import MemoryRepository
            prompt = MemoryRepository(self).build_prompt(
                user, conversation_id, body.content, max_characters,
                recent_turns=recent_turns, summary_max_characters=summary_max_characters,
            )
            for message in prompt:
                check_input(message["content"], self.settings)
            if sum(len(message["content"]) for message in prompt) > max_characters:
                raise APIError(413, "context_too_large", "Conversation exceeds the context safety limit.")
            stamp = now()
            run = Run(
                id=identifier(), conversationId=conversation_id, clientMessageId=client_id,
                userMessageId=identifier(), assistantMessageId=identifier(), status="running",
                errorCode=None, createdAt=stamp, updatedAt=stamp,
            )
            from .economy import EnergyRepository
            EnergyRepository(self, self.settings).reserve(
                user, conversation_id, run.id, body.mode, sum(len(message["content"]) for message in prompt),
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
        with self.atomic():
            run = self.run_internal(run_id)
            if run.status != "running":
                return
            self.conn.execute(
                "UPDATE messages SET content = content || ? WHERE id = ?",
                (delta, run.assistantMessageId),
            )
            self._event(run_id, "message.delta", {"messageId": run.assistantMessageId, "delta": delta})

    def finish(self, run_id: str, status: str, error_code: str | None = None) -> Run:
        with self.atomic():
            run = self.run_internal(run_id)
            if run.status != "running":
                return run
            stamp = now()
            from .economy import EnergyRepository
            EnergyRepository(self, self.settings).finish(run_id, status)
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
