"""Browser-bound, single-use social login with explicit verified-phone association.

Pending OAuth identity is never an ordinary account/session. SQLite work is synchronous
and atomic; provider HTTP, SMS and password workers never run inside a transaction.
"""

import hashlib
import hmac
import secrets
import sqlite3
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Annotated, Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, Request, Response
from pydantic import Field, field_validator
from starlette.responses import RedirectResponse

from . import accounts
from .accounts import AccountSession, DisplayName, Password, _account_user
from .admin import is_loopback, require_origin
from .browser_sessions import require_browser_context
from .config import Settings
from .db import APIError, Database, now
from .phone_identity import (
    PhoneChallenge,
    PhoneCode,
    PhoneIdentityRepository,
    PhoneNumber,
    issue_phone_challenge,
)
from .schemas import DTO, OK
from .social_providers import (
    SocialProvider,
    TestSocialProvider,
    VerifiedSocialIdentity,
)

COOKIE_NAME = "allira_social"
COOKIE_PATH = "/api/v1/auth/social"
BROWSER_TTL_SECONDS = 1800
MAX_BROWSERS = 4096
CALLBACK_TARGET = "/test/"


class SocialPending(DTO):
    provider: Literal["wechat", "qq"]
    displayName: str
    expiresAt: str
    requiresPhoneBinding: bool


class SocialAuthSession(DTO):
    csrfToken: str = Field(repr=False)
    expiresAt: str
    pending: SocialPending | None


class SocialAuthStart(DTO):
    authorizeUrl: str = Field(repr=False)
    expiresAt: str


class SocialEmpty(DTO):
    pass


class SocialPhoneChallengeInput(DTO):
    phone: PhoneNumber


class SocialBindPhone(DTO):
    challengeId: str = Field(strict=True, min_length=1, max_length=200)
    code: PhoneCode
    password: Password | None = Field(default=None, repr=False)
    displayName: DisplayName | None = None
    acceptedTermsVersion: Literal["2026-11-19"]
    isAdult: Annotated[bool, Field(strict=True)]
    confirmBind: Annotated[bool, Field(strict=True)]

    @field_validator("isAdult", "confirmBind")
    @classmethod
    def require_confirmation(cls, value: bool) -> bool:
        if not value:
            raise ValueError("Explicit confirmation is required.")
        return value


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _stamp(value: float) -> str:
    return datetime.fromtimestamp(value, UTC).isoformat().replace("+00:00", "Z")


def _unavailable() -> APIError:
    return APIError(503, "social_unavailable", "Social sign-in is not available.")


def _session_required() -> APIError:
    return APIError(401, "social_session_required", "Start a new social sign-in session.")


def _pending_required() -> APIError:
    return APIError(401, "social_pending_required", "Start social authorization again.")


def _conflict() -> APIError:
    return APIError(409, "identity_conflict", "This identity cannot be associated with that account.")


def _key(settings: Settings) -> bytes:
    value = settings.phone_identity_hmac_key
    if not isinstance(value, str) or len(value.encode("utf-8")) < 32:
        raise _unavailable()
    return value.encode("utf-8")


def social_csrf_token(settings: Settings, token: str) -> str:
    return hmac.new(_key(settings), b"allira-social-csrf-v1\x00" + token.encode("ascii"),
                    hashlib.sha256).hexdigest()


def initialize_social_identity(db: Database) -> None:
    """Startup-only additive DDL; repository constructors never commit or migrate."""
    with db.atomic():
        db.conn.execute("""CREATE TABLE IF NOT EXISTS social_identities (
            provider TEXT NOT NULL CHECK(provider IN ('wechat','qq')),
            clientId TEXT NOT NULL, subject TEXT NOT NULL,
            userId TEXT NOT NULL REFERENCES accounts(userId) ON DELETE CASCADE,
            linkedAt TEXT NOT NULL, PRIMARY KEY(provider,clientId,subject)
        )""")
        db.conn.execute("CREATE INDEX IF NOT EXISTS social_identities_user ON social_identities(userId)")
        db.conn.execute("""CREATE TABLE IF NOT EXISTS social_revocation_clock (
            singleton INTEGER PRIMARY KEY CHECK(singleton=1),
            revision INTEGER NOT NULL CHECK(revision>=0)
        )""")
        db.conn.execute("INSERT OR IGNORE INTO social_revocation_clock(singleton,revision) VALUES(1,0)")
        db.conn.execute("""CREATE TABLE IF NOT EXISTS social_user_revocations (
            userId TEXT PRIMARY KEY REFERENCES accounts(userId) ON DELETE CASCADE,
            revokedRevision INTEGER NOT NULL CHECK(revokedRevision>0)
        )""")
        db.conn.execute("""CREATE TABLE IF NOT EXISTS social_browsers (
            browserHash TEXT PRIMARY KEY, origin TEXT NOT NULL, expiresAt REAL NOT NULL
        )""")
        db.conn.execute("""CREATE TABLE IF NOT EXISTS social_oauth_states (
            stateHash TEXT PRIMARY KEY,
            browserHash TEXT NOT NULL REFERENCES social_browsers(browserHash) ON DELETE CASCADE,
            provider TEXT NOT NULL CHECK(provider IN ('wechat','qq')),
            clientId TEXT NOT NULL, redirectUri TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('issued','exchanging','consumed')),
            expiresAt REAL NOT NULL,
            flowRevision INTEGER NOT NULL CHECK(flowRevision>=0)
        )""")
        # Schema 6 was not published before these internal columns were introduced.
        # Any pre-revision transient proofs are discarded, never assigned a fresh clock.
        state_columns = {row["name"] for row in db.conn.execute("PRAGMA table_info(social_oauth_states)")}
        if "flowRevision" not in state_columns:
            db.conn.execute("DELETE FROM social_oauth_states")
            db.conn.execute("ALTER TABLE social_oauth_states ADD COLUMN flowRevision INTEGER NOT NULL DEFAULT 0")
        db.conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS social_states_browser ON social_oauth_states(browserHash)")
        db.conn.execute("""CREATE TABLE IF NOT EXISTS social_pending (
            pendingHash TEXT PRIMARY KEY,
            browserHash TEXT NOT NULL UNIQUE REFERENCES social_browsers(browserHash) ON DELETE CASCADE,
            provider TEXT NOT NULL CHECK(provider IN ('wechat','qq')),
            clientId TEXT NOT NULL, subject TEXT NOT NULL, displayName TEXT NOT NULL,
            expiresAt REAL NOT NULL,
            flowRevision INTEGER NOT NULL CHECK(flowRevision>=0)
        )""")
        pending_columns = {row["name"] for row in db.conn.execute("PRAGMA table_info(social_pending)")}
        if "flowRevision" not in pending_columns:
            db.conn.execute("DELETE FROM social_pending")
            db.conn.execute("ALTER TABLE social_pending ADD COLUMN flowRevision INTEGER NOT NULL DEFAULT 0")


def revoke_user_social_flows(db: Database, user: str) -> None:
    """Revoke older OAuth flows atomically with an account credential change.

    At issuance the subject is not known, so unrelated in-flight browser flows are not
    deleted. Their original monotonic revision is checked against the eventual linked
    or OTP-selected account instead. Erasure cascades the per-user watermark; an old
    callback can then only create an unlinked pending flow requiring fresh OTP consent.
    """
    if not db.conn.in_transaction:
        raise RuntimeError("Social flow revocation requires the outer account transaction.")
    if db.conn.execute("SELECT 1 FROM accounts WHERE userId=?", (user,)).fetchone() is None:
        return
    updated = db.conn.execute(
        "UPDATE social_revocation_clock SET revision=revision+1 "
        "WHERE singleton=1 AND revision<9223372036854775807",
    ).rowcount
    if updated != 1:
        raise RuntimeError("Social revocation clock is unavailable.")
    revision = db.conn.execute(
        "SELECT revision FROM social_revocation_clock WHERE singleton=1",
    ).fetchone()["revision"]
    db.conn.execute(
        "INSERT INTO social_user_revocations(userId,revokedRevision) VALUES(?,?) "
        "ON CONFLICT(userId) DO UPDATE SET revokedRevision=excluded.revokedRevision",
        (user, revision),
    )
    db.conn.execute(
        "DELETE FROM phone_challenges WHERE subject IN ("
        "SELECT 'social:' || p.pendingHash FROM social_pending p JOIN social_identities i "
        "ON i.provider=p.provider AND i.clientId=p.clientId AND i.subject=p.subject "
        "WHERE i.userId=?)", (user,),
    )
    db.conn.execute(
        "DELETE FROM social_browsers WHERE browserHash IN ("
        "SELECT p.browserHash FROM social_pending p JOIN social_identities i "
        "ON i.provider=p.provider AND i.clientId=p.clientId AND i.subject=p.subject "
        "WHERE i.userId=?)", (user,),
    )


class SocialIdentityRepository:
    def __init__(self, db: Database, settings: Settings | None = None) -> None:
        self.db = db
        self.settings = settings or db.settings

    def browser(self, token: str, origin: str) -> sqlite3.Row:
        if not token or len(token) > 200 or not token.isascii():
            raise _session_required()
        row = self.db.conn.execute(
            "SELECT * FROM social_browsers WHERE browserHash=? AND expiresAt>? AND origin=?",
            (_hash(token), time.time(), origin),
        ).fetchone()
        if row is None:
            raise _session_required()
        return row

    def new_browser(self, origin: str) -> tuple[str, sqlite3.Row]:
        token = secrets.token_urlsafe(32)
        with self.db.atomic():
            self.db.conn.execute("DELETE FROM social_browsers WHERE expiresAt<=?", (time.time(),))
            if self.db.conn.execute("SELECT COUNT(*) FROM social_browsers").fetchone()[0] >= MAX_BROWSERS:
                raise APIError(429, "rate_limited", "Social sign-in capacity is busy. Try later.")
            self.db.conn.execute(
                "INSERT INTO social_browsers(browserHash,origin,expiresAt) VALUES(?,?,?)",
                (_hash(token), origin, time.time() + BROWSER_TTL_SECONDS),
            )
        return token, self.browser(token, origin)

    def identity_user(self, pending: sqlite3.Row) -> str | None:
        row = self.db.conn.execute(
            "SELECT userId FROM social_identities WHERE provider=? AND clientId=? AND subject=?",
            (pending["provider"], pending["clientId"], pending["subject"]),
        ).fetchone()
        return row["userId"] if row else None

    def require_current_flow(self, flow: sqlite3.Row, user: str) -> None:
        revoked = self.db.conn.execute(
            "SELECT revokedRevision FROM social_user_revocations WHERE userId=?", (user,),
        ).fetchone()
        if revoked is not None and flow["flowRevision"] < revoked["revokedRevision"]:
            raise _pending_required()

    def pending(self, browser: sqlite3.Row) -> sqlite3.Row:
        row = self.db.conn.execute(
            "SELECT * FROM social_pending WHERE browserHash=? AND expiresAt>?",
            (browser["browserHash"], time.time()),
        ).fetchone()
        if row is None:
            raise _pending_required()
        return row

    def session(self, token: str, browser: sqlite3.Row) -> SocialAuthSession:
        pending = self.db.conn.execute(
            "SELECT * FROM social_pending WHERE browserHash=? AND expiresAt>?",
            (browser["browserHash"], time.time()),
        ).fetchone()
        view = None
        if pending is not None:
            user = self.identity_user(pending)
            verified_phone = user is not None and self.db.conn.execute(
                "SELECT 1 FROM phone_phones WHERE userId=?", (user,),
            ).fetchone() is not None
            view = SocialPending(provider=pending["provider"], displayName=pending["displayName"],
                                 expiresAt=_stamp(pending["expiresAt"]),
                                 requiresPhoneBinding=not verified_phone)
        return SocialAuthSession(csrfToken=social_csrf_token(self.settings, token),
                                 expiresAt=_stamp(browser["expiresAt"]), pending=view)

    def start(self, browser: sqlite3.Row, name: str, provider: SocialProvider) -> SocialAuthStart:
        state = secrets.token_urlsafe(32)
        expires = min(time.time() + self.settings.social_state_ttl_seconds, browser["expiresAt"])
        with self.db.atomic():
            self.db.conn.execute("DELETE FROM social_pending WHERE browserHash=?", (browser["browserHash"],))
            self.db.conn.execute("DELETE FROM social_oauth_states WHERE browserHash=?", (browser["browserHash"],))
            revision = self.db.conn.execute(
                "SELECT revision FROM social_revocation_clock WHERE singleton=1",
            ).fetchone()["revision"]
            self.db.conn.execute(
                "INSERT INTO social_oauth_states(stateHash,browserHash,provider,clientId,redirectUri,"
                "status,expiresAt,flowRevision) VALUES(?,?,?,?,?,?,?,?)", (
                    _hash(state), browser["browserHash"], name, provider.client_id, provider.redirect_uri,
                    "issued", expires, revision,
                ),
            )
            url = provider.build_authorize_url(state, provider.redirect_uri)
        return SocialAuthStart(authorizeUrl=url, expiresAt=_stamp(expires))

    def exchange_state(self, browser: sqlite3.Row, name: str, provider: SocialProvider,
                       state: str) -> sqlite3.Row:
        if self.db.conn.in_transaction:
            raise RuntimeError("Social state consumption must commit before provider I/O.")
        with self.db.atomic():
            row = self.db.conn.execute(
                "SELECT * FROM social_oauth_states WHERE stateHash=? AND browserHash=? "
                "AND provider=? AND clientId=? AND redirectUri=? AND status='issued' AND expiresAt>?",
                (_hash(state), browser["browserHash"], name, provider.client_id,
                 provider.redirect_uri, time.time()),
            ).fetchone()
            if row is None:
                raise _pending_required()
            self.db.conn.execute("UPDATE social_oauth_states SET status='exchanging' WHERE stateHash=?",
                                 (row["stateHash"],))
        return row

    def finish_exchange(self, browser: sqlite3.Row, state: sqlite3.Row,
                        identity: VerifiedSocialIdentity) -> None:
        with self.db.atomic():
            valid = self.db.conn.execute(
                "SELECT s.* FROM social_oauth_states s JOIN social_browsers b "
                "ON b.browserHash=s.browserHash WHERE s.stateHash=? AND s.browserHash=? "
                "AND s.status='exchanging' AND s.expiresAt>? AND b.expiresAt>?",
                (state["stateHash"], browser["browserHash"], time.time(), time.time()),
            ).fetchone()
            if (valid is None or valid["flowRevision"] != state["flowRevision"]
                    or (identity.provider, identity.client_id) != (state["provider"], state["clientId"])):
                raise _pending_required()
            linked = self.db.conn.execute(
                "SELECT userId FROM social_identities WHERE provider=? AND clientId=? AND subject=?",
                (identity.provider, identity.client_id, identity.subject),
            ).fetchone()
            if linked is not None:
                self.require_current_flow(valid, linked["userId"])
            self.db.conn.execute("UPDATE social_oauth_states SET status='consumed' WHERE stateHash=?",
                                 (state["stateHash"],))
            self.db.conn.execute(
                "INSERT INTO social_pending(pendingHash,browserHash,provider,clientId,subject,displayName,"
                "expiresAt,flowRevision) VALUES(?,?,?,?,?,?,?,?)", (
                    _hash(secrets.token_urlsafe(32)), browser["browserHash"], identity.provider,
                    identity.client_id, identity.subject, identity.display_name,
                    min(time.time() + self.settings.social_pending_ttl_seconds, browser["expiresAt"]),
                    valid["flowRevision"],
                ),
            )

    def target_account(self, pending: sqlite3.Row, phone: str) -> sqlite3.Row | None:
        linked = self.identity_user(pending)
        by_phone = PhoneIdentityRepository(self.db, self.settings).account_for_phone(phone)
        if linked is not None:
            if by_phone is not None and by_phone["userId"] != linked:
                raise _conflict()
            old_phone = self.db.conn.execute("SELECT phone FROM phone_phones WHERE userId=?", (linked,)).fetchone()
            if old_phone is not None and old_phone["phone"] != phone:
                raise _conflict()
            return accounts._Accounts(self.db).require(linked)
        return by_phone

    def unchanged(self, previous: sqlite3.Row) -> sqlite3.Row:
        row = accounts._Accounts(self.db).unchanged(previous)
        if row["passwordEnabled"] != previous["passwordEnabled"]:
            raise accounts._invalid_credentials()
        return row

    def attach(self, pending: sqlite3.Row, user: str) -> None:
        if not self.db.conn.in_transaction:
            raise RuntimeError("Social association requires an outer account transaction.")
        self.require_current_flow(pending, user)
        previous = self.identity_user(pending)
        if previous is not None and previous != user:
            raise _conflict()
        self.db.conn.execute(
            "INSERT OR IGNORE INTO social_identities(provider,clientId,subject,userId,linkedAt) VALUES(?,?,?,?,?)",
            (pending["provider"], pending["clientId"], pending["subject"], user, now()),
        )
        if self.identity_user(pending) != user:
            raise _conflict()

    def consume(self, pending: sqlite3.Row, user: str) -> AccountSession:
        if not self.db.conn.in_transaction:
            raise RuntimeError("Pending identity consumption requires an outer account transaction.")
        self.require_current_flow(pending, user)
        if self.db.conn.execute("DELETE FROM social_pending WHERE pendingHash=?", (pending["pendingHash"],)).rowcount != 1:
            raise _pending_required()
        self.db.conn.execute("DELETE FROM social_oauth_states WHERE browserHash=?", (pending["browserHash"],))
        token = secrets.token_urlsafe(32)
        self.db.create_session(token, user, self.settings.session_ttl_seconds, label="Social sign-in")
        return AccountSession(accessToken=token, user=_account_user(accounts._Accounts(self.db).require(user)))


def _providers(request: Request) -> dict[str, SocialProvider]:
    return getattr(request.app.state, "social_providers", {})


def _provider(request: Request, name: str) -> SocialProvider:
    if name not in {"wechat", "qq"}:
        raise _unavailable()
    provider = _providers(request).get(name)
    if provider is None:
        raise _unavailable()
    return provider


def _origin(request: Request) -> str:
    return f"{request.url.scheme}://{request.headers.get('host', '')}"


def _regular_context(request: Request, settings: Settings) -> None:
    _key(settings)
    require_browser_context(request)
    origins = {f"{urlsplit(p.redirect_uri).scheme}://{urlsplit(p.redirect_uri).netloc}"
               for p in _providers(request).values()}
    if not origins:
        raise _unavailable()
    if _origin(request) not in origins:
        raise APIError(403, "origin_forbidden", "Use the configured social sign-in origin.")


def _browser(request: Request, repo: SocialIdentityRepository, *, write: bool = False) -> sqlite3.Row:
    _regular_context(request, repo.settings)
    token = request.cookies.get(COOKIE_NAME, "")
    browser = repo.browser(token, _origin(request))
    if write:
        require_origin(request)
        submitted = request.headers.get("x-csrf-token", "")
        if not hmac.compare_digest(submitted.encode("utf-8"), social_csrf_token(repo.settings, token).encode()):
            raise APIError(403, "csrf_forbidden", "A valid social sign-in CSRF token is required.")
    return browser


def _pending(request: Request, repo: SocialIdentityRepository,
             previous: sqlite3.Row | None = None) -> tuple[sqlite3.Row, sqlite3.Row]:
    browser = _browser(request, repo, write=True)
    pending = repo.pending(browser)
    provider = _provider(request, pending["provider"])
    if provider.client_id != pending["clientId"]:
        raise _pending_required()
    if previous is not None and pending["pendingHash"] != previous["pendingHash"]:
        raise _pending_required()
    return browser, pending


def _callback_context(request: Request, provider: SocialProvider) -> None:
    # A cross-site top-level GET is expected; do not call strict cookie context here.
    if _origin(request) + request.url.path != provider.redirect_uri:
        raise _pending_required()
    if request.url.scheme == "http":
        host = urlsplit(provider.redirect_uri).hostname or ""
        if (not provider.simulated or not is_loopback(host) or request.client is None
                or not is_loopback(request.client.host)):
            raise _pending_required()
    elif request.url.scheme != "https":
        raise _pending_required()
    if (request.headers.get("sec-fetch-mode") not in {None, "navigate"}
            or request.headers.get("sec-fetch-dest") not in {None, "document"}):
        raise _pending_required()


def _callback_parameters(request: Request) -> tuple[str, str | None]:
    values = request.query_params
    if set(values) - {"state", "code", "error"}:
        raise _pending_required()
    for key in ("state", "code", "error"):
        if len(values.getlist(key)) > 1:
            raise _pending_required()
    state, code, error = values.get("state", ""), values.get("code"), values.get("error")
    if (not state or len(state) > 128 or not state.isascii()
            or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for c in state)
            or (code is None) == (error is None)):
        raise _pending_required()
    value = code if code is not None else error
    if not value or len(value) > (2048 if code is not None else 256) or any(ord(c) < 32 for c in value):
        raise _pending_required()
    return state, code


def _redirect() -> RedirectResponse:
    return RedirectResponse(CALLBACK_TARGET, status_code=303, headers={
        "Cache-Control": "no-store", "Referrer-Policy": "no-referrer",
    })


def social_identity_router(settings: Settings, current_user: Callable[..., str]) -> APIRouter:
    # current_user is intentionally not used: pending social auth is a separate identity.
    router = APIRouter(prefix="/api/v1/auth/social", tags=["social identity"])

    def repository(request: Request) -> SocialIdentityRepository:
        return SocialIdentityRepository(request.app.state.db, settings)

    @router.get("/session", response_model=SocialAuthSession)
    async def session(request: Request, response: Response):
        _regular_context(request, settings)
        repo = repository(request)
        token = request.cookies.get(COOKIE_NAME, "")
        try:
            browser = repo.browser(token, _origin(request))
        except APIError:
            accounts._auth_tools(request).attempts.check("social_browser", accounts._peer(request), 10)
            token, browser = repo.new_browser(_origin(request))
            response.set_cookie(COOKIE_NAME, token, path=COOKIE_PATH, max_age=BROWSER_TTL_SECONDS,
                                httponly=True, samesite="lax", secure=request.url.scheme == "https")
        response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = "no-referrer"
        return repo.session(token, browser)

    @router.post("/{provider_name}/start", response_model=SocialAuthStart)
    async def start(provider_name: str, body: SocialEmpty, request: Request):
        provider = _provider(request, provider_name)
        repo = repository(request)
        browser = _browser(request, repo, write=True)
        callback_origin = urlsplit(provider.redirect_uri)
        if _origin(request) != f"{callback_origin.scheme}://{callback_origin.netloc}":
            raise _pending_required()
        accounts._auth_tools(request).attempts.check("social_start", accounts._peer(request), 10)
        return repo.start(browser, provider_name, provider)

    @router.get("/{provider_name}/callback", response_class=RedirectResponse)
    async def callback(provider_name: str, request: Request):
        try:
            provider = _provider(request, provider_name)
            _key(settings)
            _callback_context(request, provider)
            state, code = _callback_parameters(request)
            repo = repository(request)
            browser = repo.browser(request.cookies.get(COOKIE_NAME, ""), _origin(request))
            saved = repo.exchange_state(browser, provider_name, provider, state)
            if code is None:
                return _redirect()
            if isinstance(provider, TestSocialProvider) and not provider.matches_state(code, state):
                return _redirect()
            identity = await provider.exchange_code(code, provider.redirect_uri)
            if not isinstance(identity, VerifiedSocialIdentity):
                return _redirect()
            current = _provider(request, provider_name)
            if (current is not provider or current.client_id != saved["clientId"]
                    or current.redirect_uri != saved["redirectUri"]):
                return _redirect()
            repo.finish_exchange(browser, saved, identity)
        except Exception:  # noqa: BLE001 - callback failures must not escape with credentials.
            # No exception/URL/body logging. Failed attempts require a fresh state.
            return _redirect()
        return _redirect()

    @router.post("/phone/challenges", status_code=202, response_model=PhoneChallenge)
    async def phone_challenge(body: SocialPhoneChallengeInput, request: Request):
        repo = repository(request)
        _, pending = _pending(request, repo)
        result = await issue_phone_challenge(request, body.phone, "social_bind", "social:" + pending["pendingHash"])
        _pending(request, repo, pending)
        return result

    @router.post("/bind-phone", response_model=AccountSession)
    async def bind_phone(body: SocialBindPhone, request: Request):
        repo = repository(request)
        _, pending = _pending(request, repo)
        tools = accounts._auth_tools(request)
        tools.attempts.check("social_bind_peer", accounts._peer(request), 10)
        tools.attempts.check("social_bind_browser", pending["browserHash"], 10)
        phones = PhoneIdentityRepository(repo.db, settings)
        subject = "social:" + pending["pendingHash"]
        proof = phones.inspect_challenge(body.challengeId, body.code.get_secret_value(),
                                         purpose="social_bind", subject=subject)
        previous = repo.target_account(pending, proof.phone)
        salt, digest = None, None
        if body.password is not None:
            salt = previous["passwordSalt"] if previous is not None else secrets.token_bytes(accounts.SALT_BYTES)
            digest = await tools.workers.run(lambda: accounts._derive_password(body.password, salt))
            _pending(request, repo, pending)
            if previous is not None:
                repo.unchanged(previous)
                if not previous["passwordEnabled"] or not hmac.compare_digest(digest, previous["passwordHash"]):
                    raise accounts._invalid_credentials()
            # Wrong OTP accounting must commit outside the final success transaction.
            proof = phones.inspect_challenge(body.challengeId, body.code.get_secret_value(),
                                         purpose="social_bind", subject=subject)
        with repo.db.atomic():
            _, current = _pending(request, repo, pending)
            target = repo.target_account(current, proof.phone)
            if previous is not None:
                repo.unchanged(previous)
                if target is None or target["userId"] != previous["userId"]:
                    raise _conflict()
            elif target is not None:
                # Another request registered this phone during the password worker.
                raise _conflict()
            phones.consume_challenge(proof)
            if target is None:
                if not settings.registration_enabled:
                    raise APIError(403, "registration_disabled", "Account registration is disabled.")
                target = phones.create_phone_account(proof.phone, salt, digest,
                                                     body.displayName or current["displayName"])
            else:
                phones.bind_phone(target["userId"], proof.phone)
            repo.attach(current, target["userId"])
            result = repo.consume(current, target["userId"])
        return result

    @router.post("/complete", response_model=AccountSession)
    async def complete(body: SocialEmpty, request: Request):
        repo = repository(request)
        _, pending = _pending(request, repo)
        with repo.db.atomic():
            _, pending = _pending(request, repo, pending)
            user = repo.identity_user(pending)
            if user is None or repo.db.conn.execute("SELECT 1 FROM phone_phones WHERE userId=?", (user,)).fetchone() is None:
                raise APIError(409, "phone_binding_required", "Verify and explicitly associate a phone first.")
            return repo.consume(pending, user)

    @router.post("/cancel", response_model=OK)
    async def cancel(body: SocialEmpty, request: Request, response: Response):
        repo = repository(request)
        browser = _browser(request, repo, write=True)
        with repo.db.atomic():
            repo.db.conn.execute("DELETE FROM social_browsers WHERE browserHash=?", (browser["browserHash"],))
        response.delete_cookie(COOKIE_NAME, path=COOKIE_PATH, httponly=True, samesite="lax",
                               secure=request.url.scheme == "https")
        return OK()

    return router
