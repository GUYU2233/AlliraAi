"""Integer-only development credits. No payment processing or asserted model-token pricing."""

import hashlib
import secrets
import time
from collections.abc import Callable
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Request
from pydantic import Field

from .config import Settings
from .db import APIError, Database, now
from .schemas import DTO, Page

MAX_BALANCE = 1_000_000_000


class Wallet(DTO):
    balance: int
    unit: Literal["energy"] = "energy"
    economyCost: int
    fastCost: int


class LedgerEntry(DTO):
    id: str
    delta: int
    balanceAfter: int
    kind: str
    referenceId: str
    createdAt: str


class RedeemInput(DTO):
    code: str = Field(min_length=16, max_length=120)


class CdkIssue(DTO):
    energy: int = Field(ge=1, le=1_000_000)
    count: int = Field(default=1, ge=1, le=50)
    expiresInDays: int = Field(default=30, ge=1, le=365)


class IssuedCdk(DTO):
    code: str
    prefix: str
    energy: int
    expiresAt: float


class CdkSummary(DTO):
    prefix: str
    energy: int
    expiresAt: float
    redeemedBy: str | None
    redeemedAt: str | None
    disabled: bool
    createdAt: str


class RunUsage(DTO):
    runId: str
    requestedMode: Literal["economy", "fast"]
    actualMode: Literal["economy", "fast"]
    cost: int
    state: Literal["reserved", "settled", "refunded"]
    promptCharacters: int
    outputCharacters: int
    firstTokenMs: int | None
    durationMs: int | None
    createdAt: str
    updatedAt: str


class UsageOverview(DTO):
    reservedEnergy: int
    settledEnergy: int
    refundedEnergy: int
    runs: int
    promptCharacters: int
    outputCharacters: int
    firstTokenMsAverage: int | None
    measurement: Literal["characters_not_tokens"] = "characters_not_tokens"


def initialize_economy(db: Database) -> None:
    db.conn.executescript("""
        CREATE TABLE IF NOT EXISTS energy_wallets (
            userId TEXT PRIMARY KEY, balance INTEGER NOT NULL CHECK(balance >= 0 AND balance <= 1000000000)
        );
        CREATE TABLE IF NOT EXISTS energy_ledger (
            id INTEGER PRIMARY KEY AUTOINCREMENT, userId TEXT NOT NULL, delta INTEGER NOT NULL,
            balanceAfter INTEGER NOT NULL, kind TEXT NOT NULL, referenceId TEXT NOT NULL,
            createdAt TEXT NOT NULL, UNIQUE(userId, kind, referenceId)
        );
        CREATE INDEX IF NOT EXISTS ledger_owner ON energy_ledger(userId, id);
        CREATE TABLE IF NOT EXISTS energy_cdks (
            codeHash TEXT PRIMARY KEY, prefix TEXT NOT NULL UNIQUE, energy INTEGER NOT NULL,
            expiresAt REAL NOT NULL, redeemedBy TEXT, redeemedAt TEXT, disabled INTEGER NOT NULL DEFAULT 0,
            createdAt TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS run_usage (
            runId TEXT PRIMARY KEY, userId TEXT NOT NULL, conversationId TEXT NOT NULL,
            requestedMode TEXT NOT NULL, actualMode TEXT NOT NULL, cost INTEGER NOT NULL,
            state TEXT NOT NULL, promptCharacters INTEGER NOT NULL, outputCharacters INTEGER NOT NULL DEFAULT 0,
            firstTokenMs INTEGER, durationMs INTEGER, createdAt TEXT NOT NULL, updatedAt TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS usage_owner ON run_usage(userId, createdAt);
        CREATE TABLE IF NOT EXISTS run_rate_attempts (userId TEXT NOT NULL, createdAt REAL NOT NULL);
        CREATE INDEX IF NOT EXISTS rate_owner ON run_rate_attempts(userId, createdAt);
    """)


class EnergyRepository:
    def __init__(self, db: Database, settings: Settings) -> None:
        self.db, self.conn, self.settings = db, db.conn, settings

    def ensure_wallet(self, user: str) -> None:
        if not self.conn.execute("SELECT 1 FROM energy_wallets WHERE userId = ?", (user,)).fetchone():
            self.conn.execute("INSERT INTO energy_wallets VALUES (?, 0)", (user,))
            self.change(user, self.settings.initial_energy, "welcome", "initial")

    def change(self, user: str, delta: int, kind: str, reference: str) -> None:
        row = self.conn.execute("SELECT balance FROM energy_wallets WHERE userId = ?", (user,)).fetchone()
        balance = row["balance"] + delta
        if balance < 0:
            raise APIError(402, "insufficient_energy", "There is not enough energy for this run.")
        # Reserve headroom for ALL outstanding refunds; funding cannot make a later cancel fail.
        pending_refunds = 0
        if delta > 0 and kind not in {"run.refunded", "run.route_refunded", "welcome"}:
            pending_refunds = self.conn.execute(
                "SELECT COALESCE(SUM(cost), 0) FROM run_usage WHERE userId = ? AND state = 'reserved'", (user,),
            ).fetchone()[0]
        if balance + pending_refunds > MAX_BALANCE:
            raise APIError(409, "balance_limit", "The energy balance limit would be exceeded.")
        self.conn.execute("UPDATE energy_wallets SET balance = ? WHERE userId = ?", (balance, user))
        self.conn.execute(
            "INSERT INTO energy_ledger (userId, delta, balanceAfter, kind, referenceId, createdAt) "
            "VALUES (?, ?, ?, ?, ?, ?)", (user, delta, balance, kind, reference, now()),
        )

    def wallet(self, user: str) -> Wallet:
        with self.db.atomic():
            self.ensure_wallet(user)
            balance = self.conn.execute("SELECT balance FROM energy_wallets WHERE userId = ?", (user,)).fetchone()[0]
        return Wallet(balance=balance, economyCost=self.settings.economy_energy_cost, fastCost=self.settings.fast_energy_cost)

    def ledger(self, user: str, cursor: str | None, limit: int) -> dict:
        position = 0
        if cursor:
            if not cursor.isascii() or not cursor.isdecimal() or len(cursor) > 18:
                raise APIError(400, "invalid_cursor", "Ledger cursor is not valid.")
            position = int(cursor)
            if not self.conn.execute("SELECT 1 FROM energy_ledger WHERE id = ? AND userId = ?", (position, user)).fetchone():
                raise APIError(400, "invalid_cursor", "Ledger cursor is not valid.")
        rows = self.conn.execute(
            "SELECT id, delta, balanceAfter, kind, referenceId, createdAt FROM energy_ledger "
            "WHERE userId = ? AND id > ? ORDER BY id LIMIT ?", (user, position, limit + 1),
        ).fetchall()
        return {"items": [{**dict(row), "id": str(row["id"])} for row in rows[:limit]],
                "nextCursor": str(rows[limit - 1]["id"]) if len(rows) > limit else None}

    def reserve(self, user: str, conversation_id: str, run_id: str, mode: str, prompt_characters: int) -> None:
        # Called inside the SAME run-creation transaction, after strict idempotency checks.
        cutoff = time.time() - 60
        self.conn.execute("DELETE FROM run_rate_attempts WHERE createdAt <= ?", (cutoff,))
        count = self.conn.execute("SELECT COUNT(*) FROM run_rate_attempts WHERE userId = ?", (user,)).fetchone()[0]
        if count >= self.settings.run_rate_per_minute:
            raise APIError(429, "rate_limited", "The per-user generation rate limit is reached.")
        self.ensure_wallet(user)
        cost = self.settings.fast_energy_cost if mode == "fast" else self.settings.economy_energy_cost
        self.change(user, -cost, "run.reserved", run_id)
        self.conn.execute("INSERT INTO run_rate_attempts VALUES (?, ?)", (user, time.time()))
        stamp = now()
        self.conn.execute(
            "INSERT INTO run_usage (runId, userId, conversationId, requestedMode, actualMode, cost, state, "
            "promptCharacters, outputCharacters, firstTokenMs, durationMs, createdAt, updatedAt) "
            "VALUES (?, ?, ?, ?, ?, ?, 'reserved', ?, 0, NULL, NULL, ?, ?)",
            (run_id, user, conversation_id, mode, mode, cost, prompt_characters, stamp, stamp),
        )

    def finish(self, run_id: str, status: str) -> None:
        row = self.conn.execute("SELECT * FROM run_usage WHERE runId = ?", (run_id,)).fetchone()
        if not row or row["state"] != "reserved":
            return
        state = "settled" if status == "completed" else "refunded"
        if state == "refunded":
            self.change(row["userId"], row["cost"], "run.refunded", run_id)
        self.conn.execute("UPDATE run_usage SET state = ?, updatedAt = ? WHERE runId = ?", (state, now(), run_id))

    def select_route(self, run_id: str, mode: str) -> None:
        with self.db.atomic():
            row = self.conn.execute("SELECT * FROM run_usage WHERE runId = ?", (run_id,)).fetchone()
            if not row or row["state"] != "reserved":
                return
            cost = row["cost"]
            if mode == "economy" and cost > self.settings.economy_energy_cost:
                difference = cost - self.settings.economy_energy_cost
                self.change(row["userId"], difference, "run.route_refunded", run_id)
                cost -= difference
            self.conn.execute("UPDATE run_usage SET actualMode = ?, cost = ?, updatedAt = ? WHERE runId = ?",
                              (mode, cost, now(), run_id))

    def metrics(self, run_id: str, *, actual_mode: str | None = None, output: int | None = None,
                first_token_ms: int | None = None, duration_ms: int | None = None) -> None:
        with self.db.atomic():
            self.conn.execute(
                "UPDATE run_usage SET actualMode = COALESCE(?, actualMode), outputCharacters = COALESCE(?, outputCharacters), "
                "firstTokenMs = COALESCE(firstTokenMs, ?), durationMs = COALESCE(?, durationMs), updatedAt = ? WHERE runId = ?",
                (actual_mode, output, first_token_ms, duration_ms, now(), run_id),
            )

    def usage(self, user: str, run_id: str) -> RunUsage:
        row = self.conn.execute("SELECT * FROM run_usage WHERE runId = ? AND userId = ?", (run_id, user)).fetchone()
        if not row:
            raise APIError(404, "not_found", "Run usage not found.")
        return RunUsage(**{key: row[key] for key in RunUsage.model_fields})

    def redeem(self, user: str, code: str) -> Wallet:
        code_hash = hashlib.sha256(code.encode()).hexdigest()
        with self.db.atomic():
            row = self.conn.execute("SELECT * FROM energy_cdks WHERE codeHash = ?", (code_hash,)).fetchone()
            if not row or row["disabled"] or row["expiresAt"] <= time.time():
                raise APIError(400, "invalid_cdk", "The code is invalid, expired or disabled.")
            if row["redeemedBy"] is not None:
                if row["redeemedBy"] != user:
                    raise APIError(409, "cdk_used", "The code has already been redeemed.")
                return self.wallet(user)
            self.ensure_wallet(user)
            self.change(user, row["energy"], "cdk.redeemed", code_hash)
            self.conn.execute("UPDATE energy_cdks SET redeemedBy = ?, redeemedAt = ? WHERE codeHash = ?", (user, now(), code_hash))
        return self.wallet(user)

    def issue(self, body: CdkIssue) -> list[IssuedCdk]:
        issued = []
        with self.db.atomic():
            for _ in range(body.count):
                code = "ALLIRA-" + secrets.token_urlsafe(24)
                prefix = code[:17]
                expires = time.time() + body.expiresInDays * 86400
                self.conn.execute(
                    "INSERT INTO energy_cdks (codeHash, prefix, energy, expiresAt, createdAt) VALUES (?, ?, ?, ?, ?)",
                    (hashlib.sha256(code.encode()).hexdigest(), prefix, body.energy, expires, now()),
                )
                issued.append(IssuedCdk(code=code, prefix=prefix, energy=body.energy, expiresAt=expires))
            self.audit("cdk.issued", "batch", str(body.count))
        return issued

    def list_cdks(self, cursor: str | None, limit: int) -> dict:
        if cursor and not self.conn.execute("SELECT 1 FROM energy_cdks WHERE prefix = ?", (cursor,)).fetchone():
            raise APIError(400, "invalid_cursor", "Code cursor is not valid.")
        rows = self.conn.execute("SELECT * FROM energy_cdks WHERE prefix > ? ORDER BY prefix LIMIT ?", (cursor or "", limit + 1)).fetchall()
        return {"items": [{**{key: row[key] for key in CdkSummary.model_fields}, "disabled": bool(row["disabled"])} for row in rows[:limit]],
                "nextCursor": rows[limit - 1]["prefix"] if len(rows) > limit else None}

    def disable_cdk(self, prefix: str) -> None:
        with self.db.atomic():
            cursor = self.conn.execute("UPDATE energy_cdks SET disabled = 1 WHERE prefix = ?", (prefix,))
            if not cursor.rowcount:
                raise APIError(404, "not_found", "Code not found.")
            self.audit("cdk.disabled", "cdk", prefix)

    def audit(self, action: str, target_type: str, target_id: str) -> None:
        self.conn.execute("INSERT INTO admin_audit (action, targetType, targetId, createdAt) VALUES (?, ?, ?, ?)",
                          (action, target_type, target_id, now()))

    def overview(self) -> UsageOverview:
        row = self.conn.execute(
            "SELECT COUNT(*) AS runs, COALESCE(SUM(promptCharacters), 0) AS promptCharacters, "
            "COALESCE(SUM(outputCharacters), 0) AS outputCharacters, AVG(firstTokenMs) AS firstTokenMsAverage, "
            "COALESCE(SUM(CASE WHEN state = 'reserved' THEN cost ELSE 0 END), 0) AS reservedEnergy, "
            "COALESCE(SUM(CASE WHEN state = 'settled' THEN cost ELSE 0 END), 0) AS settledEnergy, "
            "(SELECT COALESCE(SUM(delta), 0) FROM energy_ledger "
            "WHERE kind IN ('run.refunded', 'run.route_refunded') AND delta > 0) AS refundedEnergy FROM run_usage",
        ).fetchone()
        result = dict(row)
        if result["firstTokenMsAverage"] is not None:
            result["firstTokenMsAverage"] = int(result["firstTokenMsAverage"])
        return UsageOverview(**result)


def economy_router(settings: Settings, current_user: Callable[..., str]) -> APIRouter:
    router = APIRouter(prefix="/api/v1", tags=["energy"])
    User = Annotated[str, Depends(current_user)]

    @router.get("/energy", response_model=Wallet)
    async def wallet(request: Request, user: User):
        return EnergyRepository(request.app.state.db, settings).wallet(user)

    @router.get("/energy/ledger", response_model=Page[LedgerEntry])
    async def ledger(request: Request, user: User, cursor: str | None = None, limit: int = Query(default=50, ge=1, le=100)):
        return EnergyRepository(request.app.state.db, settings).ledger(user, cursor, limit)

    @router.post("/energy/redeem", response_model=Wallet)
    async def redeem(body: RedeemInput, request: Request, user: User):
        return EnergyRepository(request.app.state.db, settings).redeem(user, body.code)

    @router.get("/runs/{run_id}/usage", response_model=RunUsage)
    async def usage(run_id: str, request: Request, user: User):
        return EnergyRepository(request.app.state.db, settings).usage(user, run_id)

    return router
