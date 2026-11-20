"""Development administration repository. No API accepts a developer identity as an admin."""

import base64
import binascii
import hashlib
import hmac
import json
import time
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from .db import APIError, Database, now
from .schemas import AdminIdentityFilter, normalize_admin_user_query

_USER_CURSOR_DOMAIN = b"allira-admin-users-cursor-v1\0"
_MAX_USER_CURSOR = 2048


def token_hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


class AdminRepository:
    def __init__(self, db: Database) -> None:
        self.db = db
        self.conn = db.conn

    def audit(self, action: str, target_type: str, target_id: str | None = None) -> None:
        # Internal ownership survives resource deletion for account erasure. It is
        # never part of the public audit DTO, nor are codes, tokens or chat bodies.
        owner = None
        if target_id is not None:
            if target_type == "user":
                owner = target_id
            elif target_type == "conversation":
                owner = self.owner(target_id)
            elif target_type == "run":
                owner = self.run(target_id)["ownerId"]
            elif target_type == "character":
                row = self.conn.execute("SELECT ownerId FROM characters WHERE id = ?", (target_id,)).fetchone()
                owner = row["ownerId"] if row else None
        self.conn.execute(
            "INSERT INTO admin_audit (action, targetType, targetId, createdAt, ownerId) VALUES (?, ?, ?, ?, ?)",
            (action, target_type, target_id, now(), owner),
        )

    def issue_login(self, code: str, ttl: int) -> float:
        if self.db.settings.admin_auth_backend != "link":
            raise APIError(403, "admin_link_disabled", "Administrator link authentication is disabled.")
        expires = time.time() + ttl
        with self.db.atomic():
            # Only the most recently issued unused link remains valid.
            self.conn.execute("DELETE FROM admin_login_codes")
            self.conn.execute("INSERT INTO admin_login_codes VALUES (?, ?)", (token_hash(code), expires))
            self.audit("login_link.issued", "admin")
        return expires

    def exchange_login(self, code: str, token: str, ttl: int) -> float:
        if self.db.settings.admin_auth_backend != "link":
            raise APIError(403, "admin_link_disabled", "Administrator link authentication is disabled.")
        current = time.time()
        expires = current + ttl
        with self.db.atomic():
            self.conn.execute("DELETE FROM admin_login_codes WHERE expiresAt <= ?", (current,))
            consumed = self.conn.execute(
                "DELETE FROM admin_login_codes WHERE codeHash = ? AND expiresAt > ?",
                (token_hash(code), current),
            )
            if consumed.rowcount != 1:
                raise APIError(401, "unauthorized", "A valid unused administrator link is required.")
            self.conn.execute("DELETE FROM admin_sessions WHERE expiresAt <= ?", (current,))
            self.conn.execute(
                "INSERT INTO admin_sessions VALUES (?, ?)", (token_hash(token), expires),
            )
            self.audit("session.login", "admin")
        return expires

    def authenticate(self, token: str) -> float:
        if self.db.settings.admin_auth_backend != "link":
            raise APIError(401, "access_unauthorized", "A verified administrator identity is required.")
        row = self.conn.execute(
            "SELECT expiresAt FROM admin_sessions WHERE tokenHash = ? AND expiresAt > ?",
            (token_hash(token), time.time()),
        ).fetchone()
        if row is None:
            raise APIError(401, "unauthorized", "An administrator session is required.")
        return row["expiresAt"]

    def create_access_session(self, token: str, identity, ttl: int) -> float:
        """Store no JWT, email or subject; this token is only an independent CSRF binding."""
        if self.db.settings.admin_auth_backend != "access":
            raise APIError(403, "access_unavailable", "Access administration is unavailable.")
        current = time.time()
        expires = min(current + ttl, identity.expires_at)
        if expires <= current:
            raise APIError(401, "access_unauthorized", "A valid administrator identity is required.")
        with self.db.atomic():
            self.conn.execute("DELETE FROM admin_sessions WHERE expiresAt <= ?", (current,))
            self.conn.execute(
                "INSERT INTO admin_sessions (tokenHash, expiresAt) VALUES (?, ?)",
                (token_hash(token), expires),
            )
            self.conn.execute(
                "INSERT INTO admin_access_sessions VALUES (?, ?, ?, ?, ?)",
                (token_hash(token), token_hash(identity.subject), token_hash(identity.email),
                 identity.assertion_digest, expires),
            )
            self.audit("session.access_login", "admin")
        return expires

    def authenticate_access(self, token: str, identity) -> float:
        row = self.conn.execute(
            "SELECT a.subjectHash, a.emailHash, a.assertionDigest, a.expiresAt "
            "FROM admin_access_sessions a JOIN admin_sessions s USING(tokenHash) "
            "WHERE a.tokenHash = ? AND a.expiresAt > ? AND s.expiresAt > ?",
            (token_hash(token), time.time(), time.time()),
        ).fetchone()
        if (self.db.settings.admin_auth_backend != "access" or row is None
                or not hmac.compare_digest(row["subjectHash"], token_hash(identity.subject))
                or not hmac.compare_digest(row["emailHash"], token_hash(identity.email))
                or not hmac.compare_digest(row["assertionDigest"], identity.assertion_digest)):
            raise APIError(401, "access_unauthorized", "A bound administrator session is required.")
        return min(row["expiresAt"], identity.expires_at)

    def logout(self, token: str) -> None:
        with self.db.atomic():
            self.conn.execute("DELETE FROM admin_sessions WHERE tokenHash = ?", (token_hash(token),))
            self.audit("session.logout", "admin")

    def revoke_admin_sessions(self) -> int:
        with self.db.atomic():
            count = self.conn.execute("DELETE FROM admin_sessions").rowcount
            self.conn.execute("DELETE FROM admin_login_codes")
            self.audit("sessions.revoked", "admin")
        return count

    @staticmethod
    def page(rows: list[Any], limit: int) -> dict[str, Any]:
        return {"items": [dict(row) for row in rows[:limit]],
                "nextCursor": str(rows[limit - 1]["id"]) if len(rows) > limit else None}

    def overview(self) -> dict[str, Any]:
        counts = {status: 0 for status in ("running", "completed", "failed", "cancelled")}
        for row in self.conn.execute("SELECT status, COUNT(*) AS n FROM runs GROUP BY status"):
            counts[row["status"]] = row["n"]
        return {
            "users": self.conn.execute("SELECT COUNT(*) FROM users").fetchone()[0],
            "conversations": self.conn.execute("SELECT COUNT(*) FROM conversations").fetchone()[0],
            "messages": self.conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0],
            "runs": counts,
            "activeSessions": self.conn.execute(
                "SELECT COUNT(*) FROM sessions WHERE expiresAt > ?", (time.time(),),
            ).fetchone()[0],
        }

    def _user_cursor_key(self) -> bytes:
        row = self.conn.execute(
            "SELECT value FROM conversation_tool_state WHERE name = 'cursor_hmac_key'",
        ).fetchone()
        if row is None:
            raise RuntimeError("Conversation tools must be initialized at database startup.")
        return bytes.fromhex(row["value"])

    @staticmethod
    def _cursor_binding(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    @staticmethod
    def _base64(value: bytes) -> str:
        return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")

    def _user_cursor(self, user_id: str, query: str, identity: AdminIdentityFilter) -> str:
        payload = json.dumps({
            "v": 1, "q": self._cursor_binding(query), "f": identity,
            "s": self._cursor_binding(self.db.settings.oidc_issuer_url), "i": user_id,
        }, sort_keys=True, separators=(",", ":")).encode("utf-8")
        signature = hmac.digest(self._user_cursor_key(), _USER_CURSOR_DOMAIN + payload, "sha256")
        return self._base64(payload) + "." + self._base64(signature)

    def _user_keyset(self, cursor: str, query: str, identity: AdminIdentityFilter) -> str:
        try:
            if not 1 <= len(cursor) <= _MAX_USER_CURSOR or not cursor.isascii():
                raise ValueError
            encoded, signature = cursor.split(".")
            payload = base64.b64decode(encoded + "=" * (-len(encoded) % 4), altchars=b"-_", validate=True)
            submitted = base64.b64decode(signature + "=" * (-len(signature) % 4), altchars=b"-_", validate=True)
            expected = hmac.digest(self._user_cursor_key(), _USER_CURSOR_DOMAIN + payload, "sha256")
            if not hmac.compare_digest(submitted, expected):
                raise ValueError
            data = json.loads(payload)
            if (not isinstance(data, dict) or set(data) != {"v", "q", "f", "s", "i"}
                    or type(data["v"]) is not int or data["v"] != 1
                    or data["q"] != self._cursor_binding(query) or data["f"] != identity
                    or data["s"] != self._cursor_binding(self.db.settings.oidc_issuer_url)
                    or not isinstance(data["i"], str) or not 1 <= len(data["i"]) <= 100):
                raise ValueError
            # Deleting or changing the anchor does not invalidate a signed keyset.
            return data["i"]
        except (ValueError, TypeError, UnicodeError, binascii.Error):
            raise APIError(400, "invalid_cursor", "User cursor is not valid for these filters.") from None

    def list_users(
        self, cursor: str | None, limit: int, q: str = "", identity: AdminIdentityFilter = "all",
    ) -> dict[str, Any]:
        try:
            q = normalize_admin_user_query(q)
        except ValueError:
            raise APIError(422, "validation_error", "Request parameters are invalid.") from None
        if identity not in {"all", "oidc", "legacy", "developer"}:
            raise APIError(422, "validation_error", "Request parameters are invalid.")
        filtered = bool(q) or identity != "all"
        after = ""
        if cursor is not None:
            if not 1 <= len(cursor) <= _MAX_USER_CURSOR:
                raise APIError(400, "invalid_cursor", "User cursor is not valid.")
            if filtered:
                after = self._user_keyset(cursor, q, identity)
            elif not self.conn.execute("SELECT 1 FROM users WHERE id = ?", (cursor,)).fetchone():
                raise APIError(400, "invalid_cursor", "User cursor is not valid.")
            else:
                after = cursor
        # Only explicit, non-secret output columns are selected. Neither inspecting
        # an absent wallet nor searching identities creates a wallet or audit row.
        escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        pattern = "%" + escaped + "%"
        rows = self.conn.execute(
            "SELECT u.id AS userId, "
            "(SELECT COUNT(*) FROM conversations c WHERE c.ownerId = u.id) AS conversationCount, "
            "(SELECT COUNT(*) FROM runs r JOIN conversations c ON c.id = r.conversationId "
            " WHERE c.ownerId = u.id) AS runCount, "
            "(SELECT COUNT(*) FROM sessions s WHERE s.userId = u.id AND s.expiresAt > ?) AS activeSessions, "
            "u.createdAt, CASE WHEN a.userId IS NULL THEN 'developer' ELSE 'registered' END AS accountType, "
            "a.username, a.displayName, CASE WHEN a.userId IS NULL THEN 'developer' "
            "WHEN i.userId IS NULL THEN 'legacy' ELSE 'oidc' END AS identityStatus, "
            "CASE WHEN p.phone IS NULL THEN NULL ELSE '+86 ' || substr(p.phone, 4, 3) "
            "|| '****' || substr(p.phone, -4) END AS maskedPhone, "
            "p.phone IS NOT NULL AS phoneVerified, COALESCE(a.passwordEnabled, 0) AS passwordEnabled, "
            "w.balance AS walletBalance FROM users u LEFT JOIN accounts a ON a.userId = u.id "
            "LEFT JOIN oidc_identities i ON i.userId = a.userId AND i.issuer = ? "
            "LEFT JOIN phone_phones p ON p.userId = a.userId "
            "LEFT JOIN energy_wallets w ON w.userId = u.id WHERE u.id > ? "
            "AND (u.id LIKE ? ESCAPE '\\' OR a.username LIKE ? ESCAPE '\\' "
            "OR a.displayName LIKE ? ESCAPE '\\') AND (? = 'all' "
            "OR (? = 'developer' AND a.userId IS NULL) "
            "OR (? = 'legacy' AND a.userId IS NOT NULL AND i.userId IS NULL) "
            "OR (? = 'oidc' AND i.userId IS NOT NULL)) ORDER BY u.id LIMIT ?",
            (time.time(), self.db.settings.oidc_issuer_url, after, pattern, pattern, pattern,
             identity, identity, identity, identity, limit + 1),
        ).fetchall()
        items = [{**dict(row), "phoneVerified": bool(row["phoneVerified"]),
                  "passwordEnabled": bool(row["passwordEnabled"])} for row in rows[:limit]]
        next_cursor = None
        if len(rows) > limit:
            anchor = rows[limit - 1]["userId"]
            next_cursor = self._user_cursor(anchor, q, identity) if filtered else anchor
        return {"items": items, "nextCursor": next_cursor}

    def _require_user(self, user_id: str) -> None:
        if not self.conn.execute("SELECT 1 FROM users WHERE id = ?", (user_id,)).fetchone():
            raise APIError(404, "not_found", "User not found.")

    def list_user_sessions(self, user_id: str, cursor: str | None, limit: int) -> dict[str, Any]:
        self._require_user(user_id)
        current = time.time()
        after = ""
        if cursor is not None:
            try:
                if len(cursor) > 100:
                    raise ValueError
                after = str(UUID(cursor))
            except (ValueError, AttributeError):
                raise APIError(400, "invalid_cursor", "Session cursor is not valid.") from None
            if not self.conn.execute(
                "SELECT 1 FROM sessions WHERE userId = ? AND sessionId = ? AND expiresAt > ?",
                (user_id, after, current),
            ).fetchone():
                raise APIError(400, "invalid_cursor", "Session cursor is not valid.")
        rows = self.conn.execute(
            "SELECT sessionId AS id, createdAt, lastSeenAt, expiresAt, label, kind AS type "
            "FROM sessions WHERE userId = ? AND expiresAt > ? AND sessionId > ? "
            "ORDER BY sessionId LIMIT ?", (user_id, current, after, limit + 1),
        ).fetchall()
        items = [{**dict(row), "expiresAt": datetime.fromtimestamp(row["expiresAt"], UTC).isoformat()
                  .replace("+00:00", "Z")} for row in rows[:limit]]
        return {"items": items, "nextCursor": rows[limit - 1]["id"] if len(rows) > limit else None}

    def revoke_user_session(self, user_id: str, session_id: str) -> int:
        with self.db.atomic():
            self._require_user(user_id)
            current = time.time()
            if not self.conn.execute(
                "SELECT 1 FROM sessions WHERE userId = ? AND sessionId = ? AND expiresAt > ?",
                (user_id, session_id, current),
            ).fetchone():
                return 0
            from .identity_lifecycle import revoke_identity_proofs

            # Proof revisions and this one device deletion are all-or-nothing. Other
            # ordinary device sessions remain valid; this is not upstream SSO logout.
            revoke_identity_proofs(self.db, user_id)
            revoked = self.conn.execute(
                "DELETE FROM sessions WHERE userId = ? AND sessionId = ? AND expiresAt > ?",
                (user_id, session_id, current),
            ).rowcount
            if revoked != 1:
                raise RuntimeError("The selected session changed during atomic revocation.")
            self.audit("user.session_revoked", "user", user_id)
        return revoked

    def revoke_user_sessions(self, user_id: str) -> int:
        if not self.conn.execute("SELECT 1 FROM users WHERE id = ?", (user_id,)).fetchone():
            raise APIError(404, "not_found", "User not found.")
        with self.db.atomic():
            from .identity_lifecycle import revoke_identity_proofs

            revoke_identity_proofs(self.db, user_id)
            revoked = self.conn.execute("DELETE FROM sessions WHERE userId = ?", (user_id,)).rowcount
            self.audit("user.sessions_revoked", "user", user_id)
        return revoked

    def owner(self, conversation_id: str) -> str:
        row = self.conn.execute(
            "SELECT ownerId FROM conversations WHERE id = ?", (conversation_id,),
        ).fetchone()
        if row is None:
            raise APIError(404, "not_found", "Conversation not found.")
        return row["ownerId"]

    def list_conversations(
        self, cursor: str | None, limit: int, user_id: str | None,
    ) -> dict[str, Any]:
        if cursor and not self.conn.execute(
            "SELECT 1 FROM conversations WHERE id = ? AND (? IS NULL OR ownerId = ?)",
            (cursor, user_id, user_id),
        ).fetchone():
            raise APIError(400, "invalid_cursor", "Conversation cursor is not valid.")
        rows = self.conn.execute(
            "SELECT c.*, (SELECT COUNT(*) FROM messages m WHERE m.conversationId = c.id) "
            "AS messageCount FROM conversations c WHERE c.id > ? "
            "AND (? IS NULL OR c.ownerId = ?) ORDER BY c.id LIMIT ?",
            (cursor or "", user_id, user_id, limit + 1),
        ).fetchall()
        return self.page(rows, limit)

    def delete_conversation(self, conversation_id: str) -> None:
        self.db.delete_conversation(self.owner(conversation_id), conversation_id, admin=True)

    def list_messages(
        self, conversation_id: str, cursor: str | None, limit: int,
    ) -> dict[str, Any]:
        result = self.db.list_messages(self.owner(conversation_id), conversation_id, cursor, limit)
        with self.db.atomic():
            self.audit("conversation.messages_viewed", "conversation", conversation_id)
        return result

    def list_runs(
        self, cursor: str | None, limit: int, status: str | None, user_id: str | None,
    ) -> dict[str, Any]:
        condition = "(? IS NULL OR r.status = ?) AND (? IS NULL OR c.ownerId = ?)"
        filters = (status, status, user_id, user_id)
        if cursor and not self.conn.execute(
            "SELECT 1 FROM runs r JOIN conversations c ON c.id = r.conversationId "
            f"WHERE r.id = ? AND {condition}", (cursor, *filters),
        ).fetchone():
            raise APIError(400, "invalid_cursor", "Run cursor is not valid for these filters.")
        rows = self.conn.execute(
            "SELECT r.*, c.ownerId FROM runs r JOIN conversations c ON c.id = r.conversationId "
            f"WHERE r.id > ? AND {condition} ORDER BY r.id LIMIT ?",
            (cursor or "", *filters, limit + 1),
        ).fetchall()
        return self.page(rows, limit)

    def run(self, run_id: str) -> dict[str, Any]:
        row = self.conn.execute(
            "SELECT r.*, c.ownerId FROM runs r JOIN conversations c ON c.id = r.conversationId "
            "WHERE r.id = ?", (run_id,),
        ).fetchone()
        if row is None:
            raise APIError(404, "not_found", "Run not found.")
        return dict(row)

    def list_audit(self, cursor: str | None, limit: int) -> dict[str, Any]:
        sequence = 0
        if cursor:
            if not cursor.isascii() or not cursor.isdecimal() or len(cursor) > 18:
                raise APIError(400, "invalid_cursor", "Audit cursor is not valid.")
            sequence = int(cursor)
            if not self.conn.execute("SELECT 1 FROM admin_audit WHERE id = ?", (sequence,)).fetchone():
                raise APIError(400, "invalid_cursor", "Audit cursor is not valid.")
        rows = self.conn.execute(
            "SELECT CAST(id AS TEXT) AS id, action, targetType, targetId, createdAt "
            "FROM admin_audit WHERE id > ? ORDER BY id LIMIT ?", (sequence, limit + 1),
        ).fetchall()
        return self.page(rows, limit)
