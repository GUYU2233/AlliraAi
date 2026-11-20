"""CN phone identity and single-use SMS proofs, independent of user/account IDs.

Only keyed OTP digests are persisted. Sending is outside SQLite transactions;
conditional consumption belongs to the caller's atomic account/link/session write.
"""

import hashlib
import hmac
import re
import secrets
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from time import time
from typing import Annotated, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Request
from pydantic import AfterValidator, BeforeValidator, Field, SecretStr, field_validator

from . import accounts
from .accounts import (
    SALT_BYTES,
    SCRYPT_DKLEN,
    TERMS_VERSION,
    AccountSession,
    DisplayName,
    Password,
    _Accounts,
    _auth_tools,
    _invalid_credentials,
    _peer,
    reauthorize_account,
    require_current_password,
)
from .admin import require_origin
from .admin_db import token_hash
from .config import Settings
from .db import APIError, Database, now
from .schemas import DTO
from .sms import DisabledSmsSender

PHONE_CODE_TTL_SECONDS = 300
PHONE_RESEND_SECONDS = 60
PHONE_MAX_ATTEMPTS = 5
PHONE_DAY_SECONDS = 86400
PHONE_HOUR_SECONDS = 3600
_PHONE_PURPOSES = frozenset({"login", "register", "bind", "social_bind", "set_password", "erase"})


def normalize_phone(value: str) -> str:
    """Accept only ASCII mainland mobile numbers, with an optional explicit +86."""
    if not isinstance(value, str) or re.fullmatch(r"(?:\+86)?1[3-9][0-9]{9}", value) is None:
        raise ValueError("A valid mainland China mobile number is required.")
    return value if value.startswith("+86") else "+86" + value


def _phone_code(value: object) -> object:
    if not isinstance(value, str) or re.fullmatch(r"[0-9]{6}", value) is None:
        raise ValueError("A six-digit verification code is required.")
    return value


PhoneNumber = Annotated[str, Field(strict=True, min_length=11, max_length=14),
                        AfterValidator(normalize_phone)]
PhoneCode = Annotated[SecretStr, Field(min_length=6, max_length=6, repr=False),
                      BeforeValidator(_phone_code)]


def _iso(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, UTC).isoformat()


def _masked(phone: str) -> str:
    return "+86 " + phone[3:6] + "****" + phone[-4:]


def _invalid_code() -> APIError:
    return APIError(401, "invalid_phone_code", "The verification code is invalid or expired.")


def _sms_unavailable() -> APIError:
    return APIError(503, "sms_unavailable", "SMS delivery is currently unavailable.")


class PhoneChallenge(DTO):
    challengeId: str
    maskedPhone: str
    expiresAt: str
    resendAfterSeconds: Literal[60] = 60


class SocialIdentity(DTO):
    provider: Literal["wechat", "qq"]
    linkedAt: str


class AccountIdentity(DTO):
    primaryIdentifier: Literal["phone", "legacy_username"]
    phone: str | None
    maskedPhone: str | None
    phoneVerified: bool
    requiresPhoneBinding: bool
    passwordEnabled: bool
    social: list[SocialIdentity]


class PhoneChallengeInput(DTO):
    phone: PhoneNumber
    purpose: Literal["login", "register"]


class PhoneRegisterInput(DTO):
    challengeId: UUID
    code: PhoneCode
    password: Password | None = Field(default=None, repr=False)
    displayName: DisplayName | None = None
    acceptedTermsVersion: Literal["2026-11-19"]
    isAdult: Annotated[bool, Field(strict=True)]

    @field_validator("isAdult")
    @classmethod
    def require_adult(cls, value: bool) -> bool:
        if not value:
            raise ValueError("Adult confirmation is required.")
        return value


class PhoneLoginInput(DTO):
    challengeId: UUID
    code: PhoneCode


class PhonePasswordInput(DTO):
    phone: PhoneNumber
    password: Password


class PhoneBindingChallengeInput(DTO):
    phone: PhoneNumber
    currentPassword: Password


class PhoneBindingInput(PhoneLoginInput):
    currentPassword: Password


@dataclass(frozen=True)
class PhoneProof:
    challenge_id: str
    phone: str
    purpose: str
    subject: str = field(repr=False)
    code_hash: str = field(repr=False)
    expires_at: float


def initialize_phone_identity(db: Database) -> None:
    """Only additive schema; root owns accounts and global schema versions."""
    with db.atomic():
        db.conn.execute("""
            CREATE TABLE IF NOT EXISTS phone_phones (
                phone TEXT PRIMARY KEY,
                userId TEXT NOT NULL UNIQUE REFERENCES accounts(userId) ON DELETE CASCADE,
                verifiedAt TEXT NOT NULL,
                CHECK (length(phone) = 14 AND substr(phone, 1, 4) = '+861'
                    AND substr(phone, 5, 1) BETWEEN '3' AND '9'
                    AND substr(phone, 4) NOT GLOB '*[^0-9]*')
            )
        """)
        db.conn.execute("""
            CREATE TABLE IF NOT EXISTS phone_challenges (
                challengeId TEXT PRIMARY KEY,
                phone TEXT NOT NULL,
                purpose TEXT NOT NULL,
                subject TEXT NOT NULL,
                codeHash TEXT NOT NULL CHECK (length(codeHash) = 64),
                createdAt REAL NOT NULL,
                expiresAt REAL NOT NULL,
                status TEXT NOT NULL CHECK (status IN ('sending','issued','consumed','invalid')),
                attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts BETWEEN 0 AND 5)
            )
        """)
        db.conn.execute("CREATE INDEX IF NOT EXISTS phone_challenges_expiry ON phone_challenges(expiresAt)")
        db.conn.execute("CREATE INDEX IF NOT EXISTS phone_challenges_phone ON phone_challenges(phone)")
        db.conn.execute("""
            CREATE TABLE IF NOT EXISTS phone_send_attempts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                phoneKey TEXT NOT NULL CHECK (length(phoneKey) = 64),
                peerKey TEXT NOT NULL CHECK (length(peerKey) = 64),
                createdAt REAL NOT NULL
            )
        """)
        db.conn.execute("CREATE INDEX IF NOT EXISTS phone_send_attempts_time ON phone_send_attempts(createdAt)")


class PhoneIdentityRepository:
    def __init__(self, db: Database, settings: Settings | None = None) -> None:
        self.db, self.conn = db, db.conn
        self.settings = settings if settings is not None else db.settings

    def _key(self) -> bytes:
        key = getattr(self.settings, "phone_identity_hmac_key", "")
        if not isinstance(key, str) or not 32 <= len(key.encode("utf-8")) <= 4096:
            raise _sms_unavailable()
        return key.encode("utf-8")

    def _hmac(self, *parts: str) -> str:
        # Length-prefixed fields prevent ambiguity even if a trusted subject contains ':'.
        payload = b"allira-phone-v1" + b"".join(
            len(part.encode()).to_bytes(4, "big") + part.encode() for part in parts
        )
        return hmac.new(self._key(), payload, hashlib.sha256).hexdigest()

    def _code_hash(self, challenge_id: str, phone: str, purpose: str, subject: str, code: str) -> str:
        return self._hmac("code", challenge_id, phone, purpose, subject, code)

    def _cleanup(self, current: float) -> None:
        self.conn.execute("DELETE FROM phone_challenges WHERE expiresAt <= ?", (current,))
        self.conn.execute("DELETE FROM phone_send_attempts WHERE createdAt <= ?",
                          (current - PHONE_DAY_SECONDS,))

    def begin_challenge(self, phone: str, purpose: str, subject: str, peer: str) -> tuple[PhoneChallenge, str]:
        phone = normalize_phone(phone)
        if purpose not in _PHONE_PURPOSES or not isinstance(subject, str) or not 1 <= len(subject) <= 200:
            raise ValueError("Invalid phone challenge context.")
        current = time()
        challenge_id, code = str(uuid4()), f"{secrets.randbelow(1000000):06d}"
        phone_key, peer_key = self._hmac("phone", phone), self._hmac("peer", peer)
        limited = False
        with self.db.atomic():
            self._cleanup(current)
            counts = self.conn.execute(
                "SELECT COUNT(*) AS total, "
                "SUM(phoneKey = ? AND createdAt > ?) AS recent, "
                "SUM(phoneKey = ? AND createdAt > ?) AS hourly, "
                "SUM(phoneKey = ?) AS daily, "
                "SUM(peerKey = ? AND createdAt > ?) AS peerHourly FROM phone_send_attempts",
                (phone_key, current - PHONE_RESEND_SECONDS, phone_key, current - PHONE_HOUR_SECONDS,
                 phone_key, peer_key, current - PHONE_HOUR_SECONDS),
            ).fetchone()
            limited = (
                counts["total"] >= 100 or (counts["recent"] or 0) >= 1
                or (counts["hourly"] or 0) >= 5 or (counts["daily"] or 0) >= 10
                or (counts["peerHourly"] or 0) >= 30
            )
            if not limited:
                self.conn.execute(
                    "INSERT INTO phone_send_attempts (phoneKey, peerKey, createdAt) VALUES (?, ?, ?)",
                    (phone_key, peer_key, current),
                )
                self.conn.execute(
                    "UPDATE phone_challenges SET status = 'invalid' WHERE phone = ? "
                    "AND status IN ('sending', 'issued')", (phone,),
                )
                self.conn.execute(
                    "INSERT INTO phone_challenges (challengeId, phone, purpose, subject, codeHash, "
                    "createdAt, expiresAt, status, attempts) VALUES (?, ?, ?, ?, ?, ?, ?, 'sending', 0)",
                    (challenge_id, phone, purpose, subject,
                     self._code_hash(challenge_id, phone, purpose, subject, code),
                     current, current + PHONE_CODE_TTL_SECONDS),
                )
        if limited:
            raise APIError(429, "rate_limited", "Too many verification requests. Try later.")
        return PhoneChallenge(challengeId=challenge_id, maskedPhone=_masked(phone),
                              expiresAt=_iso(current + PHONE_CODE_TTL_SECONDS)), code

    def mark_issued(self, challenge_id: str) -> None:
        with self.db.atomic():
            updated = self.conn.execute(
                "UPDATE phone_challenges SET status = 'issued' WHERE challengeId = ? "
                "AND status = 'sending' AND expiresAt > ?", (challenge_id, time()),
            ).rowcount
        if updated != 1:
            raise _sms_unavailable()

    def invalidate_challenge(self, challenge_id: str) -> None:
        with self.db.atomic():
            self.conn.execute("UPDATE phone_challenges SET status = 'invalid' WHERE challengeId = ?",
                              (challenge_id,))

    def inspect_challenge(self, challenge_id: str, code: str, *, purpose: str, subject: str) -> PhoneProof:
        """Inspect without consuming. Call before the account-write outer transaction.

        Wrong attempts are committed here and only then raised, so callers must not put
        inspection in a surrounding rollback-on-error transaction. Consumption is the
        conditional operation intended for that surrounding transaction instead.
        """
        if self.conn.in_transaction:
            raise RuntimeError("Phone proof inspection must precede the outer account transaction.")
        invalid = False
        proof = None
        with self.db.atomic():
            current = time()
            self._cleanup(current)
            row = self.conn.execute("SELECT * FROM phone_challenges WHERE challengeId = ?",
                                    (challenge_id,)).fetchone()
            expected = row["codeHash"] if row is not None else "0" * 64
            supplied = self._code_hash(
                challenge_id, row["phone"] if row is not None else "", purpose, subject,
                code if isinstance(code, str) and len(code) <= 6 else "",
            )
            valid = hmac.compare_digest(supplied, expected)
            usable = row is not None and row["status"] == "issued" and row["attempts"] < PHONE_MAX_ATTEMPTS
            matches = row is not None and row["purpose"] == purpose and row["subject"] == subject
            if not usable or not matches or not valid or re.fullmatch(r"[0-9]{6}", code) is None:
                invalid = True
                if row is not None and row["status"] == "issued":
                    self.conn.execute(
                        "UPDATE phone_challenges SET attempts = MIN(attempts + 1, 5), "
                        "status = CASE WHEN attempts + 1 >= 5 THEN 'invalid' ELSE status END "
                        "WHERE challengeId = ?", (challenge_id,),
                    )
            else:
                proof = PhoneProof(row["challengeId"], row["phone"], row["purpose"], row["subject"],
                                   row["codeHash"], row["expiresAt"])
        if invalid or proof is None:
            raise _invalid_code()
        return proof

    def consume_challenge(self, proof: PhoneProof) -> None:
        if not self.conn.in_transaction:
            raise RuntimeError("Phone proof consumption requires an outer transaction.")
        updated = self.conn.execute(
            "UPDATE phone_challenges SET status = 'consumed' WHERE challengeId = ? AND phone = ? "
            "AND purpose = ? AND subject = ? AND codeHash = ? AND expiresAt = ? "
            "AND expiresAt > ? AND status = 'issued' AND attempts < 5",
            (proof.challenge_id, proof.phone, proof.purpose, proof.subject, proof.code_hash,
             proof.expires_at, time()),
        ).rowcount
        if updated != 1:
            raise _invalid_code()

    def account_for_phone(self, phone: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT a.* FROM accounts a JOIN phone_phones p ON p.userId = a.userId WHERE p.phone = ?",
            (normalize_phone(phone),),
        ).fetchone()

    def create_phone_account(
        self, phone: str, salt: bytes | None, digest: bytes | None, display_name: str | None = None,
    ) -> sqlite3.Row:
        if not self.conn.in_transaction:
            raise RuntimeError("Phone account creation requires an outer transaction.")
        phone = normalize_phone(phone)
        if (salt is None) != (digest is None):
            raise ValueError("Both password salt and digest are required together.")
        enabled = salt is not None
        if enabled and (len(salt) != SALT_BYTES or len(digest) != SCRYPT_DKLEN):
            raise ValueError("Invalid password digest parameters.")
        if self.account_for_phone(phone) is not None:
            raise APIError(409, "phone_in_use", "This phone number is already registered.")
        user, username, created = "acct_" + uuid4().hex, "p_" + secrets.token_hex(12), now()
        self.conn.execute("INSERT INTO users (id, createdAt) VALUES (?, ?)", (user, created))
        self.conn.execute(
            "INSERT INTO accounts (userId, username, displayName, passwordSalt, passwordHash, "
            "createdAt, termsVersion, isAdult, passwordEnabled) VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?)",
            (user, username, display_name if display_name is not None else "新朋友",
             salt if enabled else secrets.token_bytes(SALT_BYTES),
             digest if enabled else secrets.token_bytes(SCRYPT_DKLEN), created, TERMS_VERSION, int(enabled)),
        )
        self.bind_phone(user, phone)
        return _Accounts(self.db).require(user)

    def bind_phone(self, user: str, phone: str) -> None:
        phone = normalize_phone(phone)
        with self.db.atomic():
            _Accounts(self.db).require(user)
            owned = self.conn.execute("SELECT phone FROM phone_phones WHERE userId = ?", (user,)).fetchone()
            if owned is not None:
                if owned["phone"] == phone:
                    return
                raise APIError(409, "phone_already_bound", "This account already has a verified phone.")
            occupied = self.conn.execute("SELECT userId FROM phone_phones WHERE phone = ?", (phone,)).fetchone()
            if occupied is not None:
                raise APIError(409, "phone_in_use", "This phone number belongs to another account.")
            self.conn.execute("INSERT INTO phone_phones (phone, userId, verifiedAt) VALUES (?, ?, ?)",
                              (phone, user, now()))

    def purge_user_verifications(self, user: str) -> None:
        """Erase only this account's verification state inside the erasure transaction."""
        if not self.conn.in_transaction:
            raise RuntimeError("Phone verification erasure requires an outer transaction.")
        phone = self.conn.execute("SELECT phone FROM phone_phones WHERE userId = ?", (user,)).fetchone()
        prefixes = ("bind:" + user + ":", "security:set_password:" + user + ":",
                    "security:erase:" + user + ":")
        self.conn.execute(
            "DELETE FROM phone_challenges WHERE phone = ? "
            "OR substr(subject, 1, ?) = ? OR substr(subject, 1, ?) = ? "
            "OR substr(subject, 1, ?) = ?",
            (phone["phone"] if phone is not None else None,
             len(prefixes[0]), prefixes[0], len(prefixes[1]), prefixes[1],
             len(prefixes[2]), prefixes[2]),
        )
        key = getattr(self.settings, "phone_identity_hmac_key", "")
        if phone is not None and isinstance(key, str) and 32 <= len(key.encode("utf-8")) <= 4096:
            self.conn.execute("DELETE FROM phone_send_attempts WHERE phoneKey = ?",
                              (self._hmac("phone", phone["phone"]),))
        # Disabled deployments may no longer have the old HMAC key. The anonymous,
        # non-reversible rate rows then expire naturally within 24 hours; erasure of
        # plaintext phone/subject challenges must not be blocked by missing delivery keys.

    def identity(self, user: str) -> AccountIdentity:
        account = _Accounts(self.db).require(user)
        row = self.conn.execute("SELECT phone FROM phone_phones WHERE userId = ?", (user,)).fetchone()
        social = []
        if self.conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'social_identities'",
        ).fetchone():
            social = [SocialIdentity(provider=item["provider"], linkedAt=item["linkedAt"]) for item in
                      self.conn.execute("SELECT provider, linkedAt FROM social_identities WHERE userId = ? "
                                        "ORDER BY provider", (user,))]
        phone = row["phone"] if row is not None else None
        return AccountIdentity(primaryIdentifier="phone" if phone is not None else "legacy_username",
                               phone=phone, maskedPhone=_masked(phone) if phone is not None else None,
                               phoneVerified=phone is not None, requiresPhoneBinding=phone is None,
                               passwordEnabled=bool(account["passwordEnabled"]), social=social)


def _public_context(request: Request) -> None:
    if request.headers.get("sec-fetch-site") not in {None, "same-origin", "none"}:
        raise APIError(403, "origin_forbidden", "Cross-origin verification is not allowed.")
    if request.headers.get("origin") is not None:
        require_origin(request)


async def issue_phone_challenge(request: Request, phone: str, purpose: str, subject: str) -> PhoneChallenge:
    _public_context(request)
    settings: Settings = getattr(request.app.state, "settings", request.app.state.db.settings)
    sender = getattr(request.app.state, "sms_sender", None)
    if sender is None or isinstance(sender, DisabledSmsSender):
        raise _sms_unavailable()
    repo = PhoneIdentityRepository(request.app.state.db, settings)
    challenge, code = repo.begin_challenge(phone, purpose, subject, _peer(request))
    try:
        await sender.send(normalize_phone(phone), code, purpose)
        repo.mark_issued(challenge.challengeId)
    except BaseException as error:
        repo.invalidate_challenge(challenge.challengeId)
        if not isinstance(error, Exception):
            raise
        raise _sms_unavailable() from None
    return challenge


def _binding_subject(request: Request, user: str) -> str:
    return "bind:" + user + ":" + token_hash(request.state.session_token)


def phone_identity_router(settings: Settings, current_user: Callable[..., str]) -> APIRouter:
    router = APIRouter(prefix="/api/v1", tags=["phone identity"])
    User = Annotated[str, Depends(current_user)]

    def repo(request: Request) -> PhoneIdentityRepository:
        return PhoneIdentityRepository(request.app.state.db, settings)

    async def public_attempt(request: Request) -> None:
        _public_context(request)
        _auth_tools(request).attempts.check("phone_public", _peer(request), 10)

    @router.post("/auth/phone/challenges", response_model=PhoneChallenge, status_code=202,
                 dependencies=[Depends(public_attempt)])
    async def challenge(body: PhoneChallengeInput, request: Request):
        if body.purpose == "register" and not settings.registration_enabled:
            raise APIError(403, "registration_disabled", "Account registration is disabled.")
        return await issue_phone_challenge(request, body.phone, body.purpose, "public")

    @router.post("/auth/phone/register", response_model=AccountSession, status_code=201,
                 dependencies=[Depends(public_attempt)])
    async def register(body: PhoneRegisterInput, request: Request):
        if not settings.registration_enabled:
            raise APIError(403, "registration_disabled", "Account registration is disabled.")
        repository = repo(request)
        proof = repository.inspect_challenge(str(body.challengeId), body.code.get_secret_value(),
                                             purpose="register", subject="public")
        salt = secrets.token_bytes(SALT_BYTES) if body.password is not None else None
        digest = await _auth_tools(request).workers.run(
            lambda: accounts._derive_password(body.password, salt),
        ) if body.password is not None else None
        with request.app.state.db.atomic():
            repository.consume_challenge(proof)
            account = repository.create_phone_account(proof.phone, salt, digest, body.displayName)
            return _Accounts(request.app.state.db).login(
                account, secrets.token_urlsafe(32), settings.session_ttl_seconds,
            )

    @router.post("/auth/phone/login", response_model=AccountSession,
                 dependencies=[Depends(public_attempt)])
    async def login(body: PhoneLoginInput, request: Request):
        repository = repo(request)
        proof = repository.inspect_challenge(str(body.challengeId), body.code.get_secret_value(),
                                             purpose="login", subject="public")
        with request.app.state.db.atomic():
            repository.consume_challenge(proof)
            row = repository.account_for_phone(proof.phone)
            if row is None:
                raise _invalid_credentials()
            return _Accounts(request.app.state.db).login(
                row, secrets.token_urlsafe(32), settings.session_ttl_seconds,
            )

    @router.post("/auth/phone/password", response_model=AccountSession,
                 dependencies=[Depends(public_attempt)])
    async def password_login(body: PhonePasswordInput, request: Request):
        repository, tools = repo(request), _auth_tools(request)
        previous = repository.account_for_phone(body.phone)
        expected = previous["passwordHash"] if previous is not None else tools.dummy_digest
        salt = previous["passwordSalt"] if previous is not None else tools.dummy_salt
        digest = await tools.workers.run(lambda: accounts._derive_password(body.password, salt))
        valid = hmac.compare_digest(digest, expected)
        if previous is None or not valid or not previous["passwordEnabled"]:
            raise _invalid_credentials()
        current = repository.account_for_phone(body.phone)
        if current is None or current["userId"] != previous["userId"]:
            raise _invalid_credentials()
        return _Accounts(request.app.state.db).login(
            previous, secrets.token_urlsafe(32), settings.session_ttl_seconds,
        )

    @router.get("/account/identity", response_model=AccountIdentity)
    async def identity(request: Request, user: User):
        return repo(request).identity(user)

    @router.post("/account/phone/challenges", response_model=PhoneChallenge, status_code=202)
    async def binding_challenge(body: PhoneBindingChallengeInput, request: Request, user: User):
        previous = await require_current_password(request, user, body.currentPassword)
        # Sending awaits, so the exact session and password snapshot are rechecked again.
        result = await issue_phone_challenge(request, body.phone, "bind", _binding_subject(request, user))
        try:
            reauthorize_account(request, user)
            _Accounts(request.app.state.db).unchanged(previous)
        except BaseException:
            repo(request).invalidate_challenge(result.challengeId)
            raise
        return result

    @router.post("/account/phone/bind", response_model=AccountIdentity)
    async def bind(body: PhoneBindingInput, request: Request, user: User):
        await require_current_password(request, user, body.currentPassword)
        repository = repo(request)
        proof = repository.inspect_challenge(str(body.challengeId), body.code.get_secret_value(),
                                             purpose="bind", subject=_binding_subject(request, user))
        with request.app.state.db.atomic():
            repository.consume_challenge(proof)
            repository.bind_phone(user, proof.phone)
            request.app.state.db.conn.execute(
                "DELETE FROM sessions WHERE userId = ? AND tokenHash != ?",
                (user, token_hash(request.state.session_token)),
            )
            return repository.identity(user)

    return router
