"""Explicit account archives and password-confirmed erasure, without credential exports.

Exports are synchronous snapshots with hard bounds, never silently truncated. Erasure
has no await after password verification and checks both logical runs and physical
cleanup tasks. Backups are not rewritten: operators must respect the documented
retention window and must not restore erased accounts into a serving deployment.
"""

import secrets
from collections.abc import Callable
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Request, Response
from pydantic import Field

from .accounts import AccountUser, Password, _account_user, _Accounts, require_current_password
from .browser_sessions import clear_cookie
from .config import Settings
from .conversation_tools import ConversationArchive, ConversationToolsRepository
from .db import APIError, Database, now
from .economy import LedgerEntry, RunUsage, Wallet
from .identity_lifecycle import revoke_identity_proofs
from .memory import Character
from .phone_identity import AccountIdentity, PhoneIdentityRepository
from .schemas import DTO, OK

MAX_ACCOUNT_EXPORT_BYTES = 32 * 1024 * 1024
MAX_ACCOUNT_EXPORT_RECORDS = 100000
MAX_ACCOUNT_EXPORT_CONVERSATIONS = 1000


class AccountDataExport(DTO):
    format: Literal["allira.account.v1"] = "allira.account.v1"
    exportedAt: str
    user: AccountUser
    conversations: list[ConversationArchive]
    characters: list[Character]
    wallet: Wallet | None
    ledger: list[LedgerEntry]
    usage: list[RunUsage]
    warnings: list[str]
    identity: AccountIdentity | None = None


class AccountErase(DTO):
    currentPassword: Password = Field(repr=False)
    confirmation: Literal["DELETE MY ACCOUNT"]


def _export_too_large() -> APIError:
    return APIError(413, "account_export_too_large", "The account archive exceeds its safe limit; no data was truncated.")


class AccountDataRepository:
    def __init__(self, db: Database, settings: Settings) -> None:
        self.db, self.settings = db, settings

    def export(self, user: str) -> AccountDataExport:
        with self.db.atomic():
            account = _account_user(_Accounts(self.db).require(user))
            counts = [self.db.conn.execute(query, (user,)).fetchone()[0] for query in (
                "SELECT COUNT(*) FROM conversations WHERE ownerId = ?",
                "SELECT COUNT(*) FROM messages m JOIN conversations c ON c.id = m.conversationId WHERE c.ownerId = ?",
                "SELECT COUNT(*) FROM characters WHERE ownerId = ?",
                "SELECT COUNT(*) FROM energy_ledger WHERE userId = ?",
                "SELECT COUNT(*) FROM run_usage WHERE userId = ?",
                "SELECT COUNT(*) FROM memory_cards m JOIN conversations c ON c.id = m.conversationId WHERE c.ownerId = ?",
                "SELECT COUNT(*) FROM memory_blocks m JOIN conversations c ON c.id = m.conversationId WHERE c.ownerId = ?",
            )]
            if counts[0] > MAX_ACCOUNT_EXPORT_CONVERSATIONS or sum(counts) > MAX_ACCOUNT_EXPORT_RECORDS:
                raise _export_too_large()
            budget = len(account.model_dump_json().encode("utf-8"))

            def measure(value):
                nonlocal budget
                budget += len(value.model_dump_json().encode("utf-8"))
                if budget > MAX_ACCOUNT_EXPORT_BYTES:
                    raise _export_too_large()
                return value

            tools = ConversationToolsRepository(self.db)
            conversations = []
            for row in self.db.conn.execute(
                "SELECT id FROM conversations WHERE ownerId = ? ORDER BY createdAt, id", (user,),
            ).fetchall():
                # The public per-conversation export enforces its own message/byte
                # limit and refuses active generations, inside this same snapshot.
                exported = tools.export(user, row["id"], format="json")
                conversations.append(measure(ConversationArchive.model_validate_json(exported.content)))
            characters = [measure(Character(**dict(row))) for row in self.db.conn.execute(
                "SELECT id, name, description, instructions, createdAt, updatedAt "
                "FROM characters WHERE ownerId = ? ORDER BY createdAt, id", (user,),
            )]
            balance = self.db.conn.execute(
                "SELECT balance FROM energy_wallets WHERE userId = ?", (user,),
            ).fetchone()
            # Export must not create a wallet or issue welcome energy.
            wallet = measure(Wallet(
                balance=balance["balance"], economyCost=self.settings.economy_energy_cost,
                fastCost=self.settings.fast_energy_cost,
            )) if balance is not None else None
            ledger = [measure(LedgerEntry(**{**dict(row), "id": str(row["id"])}))
                      for row in self.db.conn.execute(
                          "SELECT id, delta, balanceAfter, kind, referenceId, createdAt "
                          "FROM energy_ledger WHERE userId = ? ORDER BY id", (user,),
                      )]
            usage = [measure(RunUsage(**{key: row[key] for key in RunUsage.model_fields}))
                     for row in self.db.conn.execute(
                         "SELECT * FROM run_usage WHERE userId = ? ORDER BY createdAt, runId", (user,),
                     )]
            result = AccountDataExport(
                exportedAt=now(), user=account, conversations=conversations,
                characters=characters, wallet=wallet, ledger=ledger, usage=usage,
                identity=PhoneIdentityRepository(self.db, self.settings).identity(user),
                warnings=[
                    "This is a private data archive, not a full-system backup or automatic account restore format.",
                    "Passwords, tokens, recovery codes, administrator data and provider credentials are excluded.",
                    "Conversation memory cards and extract blocks are private archive data; disabling context use is not erasure.",
                    "Existing protected backups may retain historical records until their documented retention expires.",
                ],
            )
            if len(result.model_dump_json().encode("utf-8")) > MAX_ACCOUNT_EXPORT_BYTES:
                raise _export_too_large()
            return result

    def erase(self, user: str, previous, in_flight: set[str]) -> None:
        with self.db.atomic():
            _Accounts(self.db).unchanged(previous)
            # Deleted conversations may still have terminal tasks closing upstream.
            owned_ids = {row[0] for row in self.db.conn.execute(
                "SELECT id FROM runs WHERE conversationId IN (SELECT id FROM conversations WHERE ownerId = ?) "
                "UNION SELECT runId FROM run_usage WHERE userId = ?", (user, user),
            )}
            if owned_ids & in_flight or self.db.conn.execute(
                "SELECT 1 FROM runs r JOIN conversations c ON c.id = r.conversationId "
                "WHERE c.ownerId = ? AND r.status = 'running' LIMIT 1", (user,),
            ).fetchone():
                raise APIError(409, "account_busy", "Stop active generations and wait for cleanup before erasing the account.")
            # Preserve redemption state without retaining the old account identity.
            tombstone = "erased_" + secrets.token_hex(24)
            self.db.conn.execute("UPDATE energy_cdks SET redeemedBy = ? WHERE redeemedBy = ?", (tombstone, user))
            self.db.conn.execute(
                "DELETE FROM admin_audit WHERE ownerId = ? OR (ownerId IS NULL AND ("
                "(targetType = 'user' AND targetId = ?) "
                "OR (targetType = 'conversation' AND targetId IN "
                "(SELECT id FROM conversations WHERE ownerId = ? "
                "UNION SELECT conversationId FROM run_usage WHERE userId = ?)) "
                "OR (targetType = 'run' AND targetId IN "
                "(SELECT id FROM runs WHERE conversationId IN "
                "(SELECT id FROM conversations WHERE ownerId = ?) "
                "UNION SELECT runId FROM run_usage WHERE userId = ?)) "
                "OR (targetType = 'character' AND targetId IN "
                "(SELECT id FROM characters WHERE ownerId = ?))))",
                (user, user, user, user, user, user, user),
            )
            self.db.conn.execute("DELETE FROM conversation_forks WHERE ownerId = ?", (user,))
            for row in self.db.conn.execute(
                "SELECT id FROM conversations WHERE ownerId = ?", (user,),
            ).fetchall():
                self.db.delete_conversation(user, row["id"])
            self.db.conn.execute("DELETE FROM characters WHERE ownerId = ?", (user,))
            for table in ("run_usage", "energy_ledger", "energy_wallets", "run_rate_attempts", "sessions"):
                self.db.conn.execute(f"DELETE FROM {table} WHERE userId = ?", (user,))
            # Revoke pending proofs while the phone and verified social namespace
            # still identify this account. Persistent links then cascade from accounts.
            revoke_identity_proofs(self.db, user, erasing=True)
            self.db.conn.execute("DELETE FROM accounts WHERE userId = ?", (user,))
            self.db.conn.execute("DELETE FROM users WHERE id = ?", (user,))


def data_controls_router(settings: Settings, current_user: Callable[..., str]) -> APIRouter:
    router = APIRouter(prefix="/api/v1/account", tags=["account-data"])
    User = Annotated[str, Depends(current_user)]

    @router.get("/export", response_model=AccountDataExport)
    async def export(request: Request, user: User):
        return AccountDataRepository(request.app.state.db, settings).export(user)

    @router.post("/erase", response_model=OK)
    async def erase(body: AccountErase, request: Request, response: Response, user: User):
        previous = await require_current_password(request, user, body.currentPassword)
        # No await from reauthorization to the final atomic erasure.
        AccountDataRepository(request.app.state.db, settings).erase(
            user, previous, request.app.state.runtime.in_flight_run_ids(),
        )
        clear_cookie(response, secure=request.url.scheme == "https")
        return OK()

    return router
