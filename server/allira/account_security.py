"""Single-use recovery codes and revocable account-device sessions.

Recovery codes carry 192 bits of entropy, are shown only on rotation, and are stored
only as SHA-256 digests. All credential work shares the existing bounded account pool.
SQLite reads/writes remain on the event loop; transactions never cross an await.
"""

import hashlib
import hmac
import secrets
import sqlite3
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from pydantic import BeforeValidator, Field, SecretStr

from . import accounts
from .accounts import Password, Username
from .config import Settings
from .db import Database, now
from .schemas import DTO, OK

RECOVERY_CODE_COUNT = 8
RECOVERY_CODE_BYTES = 24
RECOVERY_ATTEMPTS = 10


def _recovery_code(value: object) -> object:
    if not isinstance(value, str) or not 1 <= len(value) <= 128 or not value.isascii():
        raise ValueError("Recovery code must be a bounded ASCII string.")
    return value


RecoveryCode = Annotated[
    SecretStr, Field(min_length=1, max_length=128, repr=False), BeforeValidator(_recovery_code),
]


class AccountRecoveryStatus(DTO):
    enabled: bool
    remainingCodes: int
    updatedAt: str | None


class AccountRecoveryRotate(DTO):
    currentPassword: Password = Field(repr=False)


class AccountRecoveryCodes(DTO):
    codes: list[str] = Field(repr=False)
    createdAt: str


class AccountRecover(DTO):
    username: Username
    recoveryCode: RecoveryCode
    newPassword: Password = Field(repr=False)


class AccountDeviceSession(DTO):
    id: str
    createdAt: str
    lastSeenAt: str
    expiresAt: str
    label: str
    current: bool
    type: Literal["bearer", "browser"]


class AccountDeviceSessions(DTO):
    items: list[AccountDeviceSession]


def initialize_account_security(db: Database) -> None:
    """Add recovery tables after initialize_accounts, without constructing a Database."""
    with db.atomic():
        db.conn.execute("""
            CREATE TABLE IF NOT EXISTS account_recovery_state (
                userId TEXT PRIMARY KEY REFERENCES accounts(userId) ON DELETE CASCADE,
                updatedAt TEXT NOT NULL
            )
        """)
        db.conn.execute("""
            CREATE TABLE IF NOT EXISTS account_recovery_codes (
                codeHash TEXT PRIMARY KEY CHECK (length(codeHash) = 64),
                userId TEXT NOT NULL REFERENCES accounts(userId) ON DELETE CASCADE,
                createdAt TEXT NOT NULL
            )
        """)
        db.conn.execute("""
            CREATE INDEX IF NOT EXISTS account_recovery_codes_user
                ON account_recovery_codes(userId)
        """)


def invalidate_recovery_codes(db: Database, user: str) -> None:
    """Clear all codes inside the caller's password-change/erase transaction."""
    db.conn.execute("DELETE FROM account_recovery_codes WHERE userId = ?", (user,))


def _code_hash(value: str) -> str:
    return hashlib.sha256(value.encode("ascii")).hexdigest()


class _AccountSecurity:
    def __init__(self, db: Database) -> None:
        self.db, self.conn = db, db.conn
        self.accounts = accounts._Accounts(db)

    def status(self, user: str) -> AccountRecoveryStatus:
        self.accounts.require(user)
        state = self.conn.execute(
            "SELECT updatedAt FROM account_recovery_state WHERE userId = ?", (user,),
        ).fetchone()
        count = self.conn.execute(
            "SELECT COUNT(*) FROM account_recovery_codes WHERE userId = ?", (user,),
        ).fetchone()[0]
        return AccountRecoveryStatus(
            enabled=count > 0, remainingCodes=count,
            updatedAt=state["updatedAt"] if state is not None else None,
        )

    def rotate(self, previous: sqlite3.Row, codes: list[str]) -> AccountRecoveryCodes:
        created = now()
        with self.db.atomic():
            user = self.accounts.unchanged(previous)["userId"]
            invalidate_recovery_codes(self.db, user)
            self.conn.execute(
                "INSERT INTO account_recovery_state (userId, updatedAt) VALUES (?, ?) "
                "ON CONFLICT(userId) DO UPDATE SET updatedAt = excluded.updatedAt", (user, created),
            )
            self.conn.executemany(
                "INSERT INTO account_recovery_codes (codeHash, userId, createdAt) VALUES (?, ?, ?)",
                [(_code_hash(code), user, created) for code in codes],
            )
        return AccountRecoveryCodes(codes=codes, createdAt=created)

    def code_hashes(self, user: str | None) -> list[str]:
        return [row[0] for row in self.conn.execute(
            "SELECT codeHash FROM account_recovery_codes WHERE userId = ? LIMIT ?",
            (user, RECOVERY_CODE_COUNT),
        ).fetchall()]

    def recover(
        self, previous: sqlite3.Row, code_hash: str, salt: bytes, digest: bytes,
    ) -> None:
        with self.db.atomic():
            user = self.accounts.unchanged(previous)["userId"]
            # Re-read after the worker await: rotation/recovery may have invalidated it.
            row = self.conn.execute(
                "SELECT codeHash FROM account_recovery_codes WHERE userId = ? AND codeHash = ?",
                (user, code_hash),
            ).fetchone()
            if row is None or not hmac.compare_digest(row["codeHash"], code_hash):
                raise accounts._invalid_credentials()
            # This nested transaction changes the password and deletes every session
            # and recovery code atomically. A failed revoke must not consume the code.
            self.accounts.change_password(previous, salt, digest)

    def sessions(self, user: str, token: str) -> AccountDeviceSessions:
        self.accounts.require(user)
        current_hash = hashlib.sha256(token.encode()).hexdigest()
        rows = self.conn.execute(
            "SELECT sessionId, tokenHash, createdAt, lastSeenAt, expiresAt, label, kind "
            "FROM sessions WHERE userId = ? AND expiresAt > ? "
            "ORDER BY createdAt DESC, sessionId", (user, time.time()),
        ).fetchall()
        return AccountDeviceSessions(items=[AccountDeviceSession(
            id=row["sessionId"], createdAt=row["createdAt"], lastSeenAt=row["lastSeenAt"],
            expiresAt=datetime.fromtimestamp(row["expiresAt"], UTC).isoformat().replace("+00:00", "Z"),
            label=row["label"], current=hmac.compare_digest(row["tokenHash"], current_hash),
            type=row["kind"],
        ) for row in rows])

    def revoke(self, user: str, session_id: str) -> None:
        with self.db.atomic():
            self.accounts.require(user)
            # Unknown and foreign IDs produce the same idempotent result, without
            # revealing another account's session existence or revoking it.
            self.conn.execute(
                "DELETE FROM sessions WHERE userId = ? AND sessionId = ?", (user, session_id),
            )


def account_security_router(settings: Settings, current_user: Callable[..., str]) -> APIRouter:
    """Return routes relative to /api/v1, included once by accounts_router."""
    router = APIRouter(tags=["account-security"])
    User = Annotated[str, Depends(current_user)]

    def repository(request: Request) -> _AccountSecurity:
        return _AccountSecurity(request.app.state.db)

    async def recovery_attempt(request: Request) -> None:
        accounts._auth_tools(request).attempts.check(
            "recover", accounts._peer(request), RECOVERY_ATTEMPTS,
        )

    @router.get("/account/recovery", response_model=AccountRecoveryStatus)
    async def recovery_status(request: Request, user: User):
        return repository(request).status(user)

    @router.post("/account/recovery", response_model=AccountRecoveryCodes)
    async def rotate_recovery(body: AccountRecoveryRotate, request: Request, user: User):
        previous = await accounts.require_current_password(request, user, body.currentPassword)
        codes = [secrets.token_urlsafe(RECOVERY_CODE_BYTES) for _ in range(RECOVERY_CODE_COUNT)]
        return repository(request).rotate(previous, codes)

    @router.post("/auth/recover", response_model=OK, dependencies=[Depends(recovery_attempt)])
    async def recover(body: AccountRecover, request: Request):
        repo = repository(request)
        previous = repo.accounts.by_username(body.username)
        hashes = repo.code_hashes(previous["userId"] if previous is not None else None)
        candidate = _code_hash(body.recoveryCode.get_secret_value())
        # Fixed-width comparisons for existing and missing accounts; no early return.
        matched = False
        for index in range(RECOVERY_CODE_COUNT):
            expected = hashes[index] if index < len(hashes) else "0" * 64
            matched |= hmac.compare_digest(candidate, expected) and index < len(hashes)
        salt = secrets.token_bytes(accounts.SALT_BYTES)
        digest = await accounts._auth_tools(request).workers.run(
            lambda: accounts._derive_password(body.newPassword, salt),
        )
        if previous is None or not matched:
            raise accounts._invalid_credentials()
        repo.recover(previous, candidate, salt, digest)
        return OK()

    @router.get("/account/sessions", response_model=AccountDeviceSessions)
    async def sessions(request: Request, user: User):
        return repository(request).sessions(user, request.state.session_token)

    @router.post("/account/sessions/{id}/revoke", response_model=OK)
    async def revoke_session(id: UUID, request: Request, user: User):
        repository(request).revoke(user, str(id))
        return OK()

    return router
