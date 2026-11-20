"""Logto OIDC relying-party glue using Authlib and joserfc, not an issuer.

Logto owns authentication, OTP/social verification and hosted UI. This module owns
only browser-bound authorization transactions, verified-subject/business-ID mapping
and explicit legacy-account linking. Provider tokens live only in request memory.
"""

import asyncio
import hashlib
import hmac
import json
import logging
import math
import re
import secrets
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from time import time
from typing import Annotated, Literal
from urllib.parse import urlsplit

import httpx
from authlib.common.errors import AuthlibBaseError
from authlib.integrations.httpx_client import AsyncOAuth2Client
from authlib.oidc.core import CodeIDToken
from fastapi import APIRouter, Depends, Request, Response
from joserfc import jwk, jwt
from joserfc.errors import JoseError
from pydantic import Field, TypeAdapter, ValidationError, field_validator
from starlette.responses import RedirectResponse

from . import accounts
from .accounts import (
    LOGIN_ATTEMPTS,
    AccountSession,
    DisplayName,
    Password,
    Username,
    _account_user,
    _Accounts,
    _auth_tools,
    _invalid_credentials,
    _peer,
)
from .admin import is_loopback, require_loopback, require_origin
from .browser_sessions import authenticate_browser, clear_cookie
from .config import Settings
from .data_controls import AccountDataRepository
from .db import APIError, Database, now
from .phone_identity import PhoneIdentityRepository, _masked, normalize_phone
from .schemas import DTO, OK

COOKIE_NAME = "allira_oidc"
COOKIE_PATH = "/api/v1/auth/oidc"
BROWSER_TTL_SECONDS = 1800
STATE_TTL_SECONDS = 300
PENDING_TTL_SECONDS = 600
MAX_BROWSERS = 4096
MAX_HTTP_BYTES = 65536
HTTP_DEADLINE_SECONDS = 15
_MAX_REVISION = 9223372036854775807
_TOKEN = re.compile(r"[A-Za-z0-9_-]{43}")
_DISPLAY_NAME = TypeAdapter(DisplayName)
_FRESH_PURPOSES = frozenset({"erase", "phone_migration"})
_DIAGNOSTIC_STAGES = frozenset({
    "callback.context", "callback.browser", "callback.state", "callback.response",
    "exchange.discovery", "exchange.token", "exchange.jwks", "exchange.signature",
    "exchange.claims", "exchange.userinfo", "exchange.identity", "exchange.close",
    "callback.finish", "unknown",
})
_LOGGER = logging.getLogger(__name__)


@dataclass
class _CallbackDiagnostic:
    # This object contains only a stage constant, never request/provider data or
    # exceptions. Keep it request-local so concurrent exchanges cannot mix stages.
    stage: str = "callback.context"


def _callback_failure(diagnostic: _CallbackDiagnostic, error: Exception) -> None:
    """Log only constants; never stringify an exception, identity or HTTP object."""
    stage = diagnostic.stage if diagnostic.stage in _DIAGNOSTIC_STAGES else "unknown"
    if isinstance(error, APIError):
        category = "API"
    elif isinstance(error, TimeoutError):
        category = "TIMEOUT"
    elif isinstance(error, httpx.HTTPError):
        category = "HTTP"
    elif isinstance(error, AuthlibBaseError):
        category = "AUTHLIB"
    elif isinstance(error, JoseError):
        category = "JOSE"
    elif isinstance(error, (ValueError, TypeError)):
        category = "VALUE"
    else:
        category = "OTHER"
    _LOGGER.warning(
        "oidc_callback_failed stage=%s category=%s", stage, category,
        extra={"event": "oidc_callback_failed", "stage": stage, "category": category},
    )


class OidcPending(DTO):
    provider: Literal["logto"] = "logto"
    purpose: Literal["login", "erase", "phone_migration"]
    maskedPhone: str
    expiresAt: str


class OidcSession(DTO):
    csrfToken: str = Field(repr=False)
    pending: OidcPending | None
    enabled: Literal[True] = True
    simulated: bool


class OidcStart(DTO):
    authorizationUrl: str = Field(repr=False)


class OidcComplete(DTO):
    acceptedTermsVersion: Literal["2026-11-19"]
    isAdult: Annotated[bool, Field(strict=True)]
    confirmIdentity: Annotated[bool, Field(strict=True)]
    confirmPhoneMigration: Annotated[bool, Field(strict=True)] = False

    @field_validator("isAdult", "confirmIdentity")
    @classmethod
    def require_confirmation(cls, value: bool) -> bool:
        if not value:
            raise ValueError("Explicit confirmation is required.")
        return value


class OidcMigrate(OidcComplete):
    username: Username
    currentPassword: Password = Field(repr=False)


class OidcStartInput(DTO):
    purpose: Literal["login", "erase", "phone_migration"] = "login"


class OidcErase(DTO):
    confirmation: Literal["DELETE MY ACCOUNT"]


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _iso(value: float) -> str:
    return datetime.fromtimestamp(value, UTC).isoformat().replace("+00:00", "Z")


def _pending_required() -> APIError:
    return APIError(401, "oidc_pending_required", "A current verified identity is required.")


def _session_required() -> APIError:
    return APIError(401, "oidc_session_required", "A current OIDC browser session is required.")


def _unavailable() -> APIError:
    return APIError(503, "oidc_unavailable", "Identity authentication is unavailable.")


def _link_required() -> APIError:
    return APIError(409, "identity_link_required", "Explicit legacy-account linking is required.")


def _claim_phone(value: object) -> str:
    # Logto 1.46's verified userinfo may serialize mainland numbers with the
    # country code but no '+'. Normalize only that exact trusted-claim shape;
    # do not broaden global phone input validation or accept other countries.
    if isinstance(value, str) and re.fullmatch(r"861[3-9][0-9]{9}", value):
        value = "+" + value
    return normalize_phone(value)


def _fresh_auth(auth_time: object, started_at: float) -> bool:
    current = time()
    return (
        not isinstance(auth_time, bool) and isinstance(auth_time, (int, float))
        and math.isfinite(auth_time)
        and max(started_at - 60, current - 300) <= auth_time <= current
    )


def initialize_oidc_identity(db: Database) -> None:
    """Add relying-party state without changing account IDs or existing credentials."""
    with db.atomic():
        # Early schema7 states allowed only login/erase. They are short-lived
        # proofs, not identities: discard them rather than rewrite persisted
        # account mappings, clock watermarks or credentials during this upgrade.
        transient_schema = db.conn.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' "
            "AND name IN ('oidc_states', 'oidc_pending')",
        ).fetchall()
        if any("phone_migration" not in (row["sql"] or "") for row in transient_schema):
            for table in ("oidc_pending", "oidc_states", "oidc_browsers"):
                db.conn.execute(f"DROP TABLE IF EXISTS {table}")
        db.conn.execute("""
            CREATE TABLE IF NOT EXISTS oidc_identities (
                issuer TEXT NOT NULL,
                subject TEXT NOT NULL,
                userId TEXT NOT NULL UNIQUE REFERENCES accounts(userId) ON DELETE CASCADE,
                linkedAt TEXT NOT NULL,
                PRIMARY KEY (issuer, subject)
            )
        """)
        db.conn.execute("""
            CREATE TABLE IF NOT EXISTS oidc_browsers (
                browserHash TEXT PRIMARY KEY,
                expiresAt REAL NOT NULL
            )
        """)
        db.conn.execute("""
            CREATE TABLE IF NOT EXISTS oidc_states (
                stateHash TEXT PRIMARY KEY,
                browserHash TEXT NOT NULL UNIQUE REFERENCES oidc_browsers(browserHash) ON DELETE CASCADE,
                status TEXT NOT NULL CHECK (status IN ('issued', 'exchanging', 'consumed')),
                nonce TEXT NOT NULL,
                codeVerifier TEXT NOT NULL,
                issuer TEXT NOT NULL,
                clientId TEXT NOT NULL,
                redirectUri TEXT NOT NULL,
                purpose TEXT NOT NULL CHECK (purpose IN ('login', 'erase', 'phone_migration')),
                expectedUserId TEXT REFERENCES accounts(userId) ON DELETE CASCADE,
                sessionHash TEXT,
                startedAt REAL NOT NULL,
                expiresAt REAL NOT NULL,
                flowRevision INTEGER NOT NULL CHECK (flowRevision >= 0)
            )
        """)
        db.conn.execute("""
            CREATE TABLE IF NOT EXISTS oidc_pending (
                pendingHash TEXT PRIMARY KEY,
                browserHash TEXT NOT NULL UNIQUE REFERENCES oidc_browsers(browserHash) ON DELETE CASCADE,
                issuer TEXT NOT NULL,
                subject TEXT NOT NULL,
                phone TEXT NOT NULL,
                displayName TEXT NOT NULL,
                purpose TEXT NOT NULL CHECK (purpose IN ('login', 'erase', 'phone_migration')),
                expectedUserId TEXT REFERENCES accounts(userId) ON DELETE CASCADE,
                sessionHash TEXT,
                startedAt REAL NOT NULL,
                authTime REAL,
                expiresAt REAL NOT NULL,
                flowRevision INTEGER NOT NULL CHECK (flowRevision >= 0)
            )
        """)
        db.conn.execute("""
            CREATE TABLE IF NOT EXISTS oidc_revocation_clock (
                singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                revision INTEGER NOT NULL CHECK (revision >= 0)
            )
        """)
        db.conn.execute("INSERT OR IGNORE INTO oidc_revocation_clock VALUES (1, 0)")
        db.conn.execute("""
            CREATE TABLE IF NOT EXISTS oidc_user_revocations (
                userId TEXT PRIMARY KEY REFERENCES accounts(userId) ON DELETE CASCADE,
                revokedRevision INTEGER NOT NULL CHECK (revokedRevision >= 0)
            )
        """)
        for table in ("oidc_browsers", "oidc_states", "oidc_pending"):
            db.conn.execute(f"CREATE INDEX IF NOT EXISTS {table}_expiry ON {table}(expiresAt)")


def clear_transient_oidc_state(db: Database) -> None:
    """Invalidate in-flight login proofs at restart/restore, never persistent mappings."""
    with db.atomic():
        db.conn.execute("DELETE FROM oidc_browsers")


def revoke_user_oidc_flows(db: Database, user: str) -> None:
    """Invalidate even not-yet-resolved callbacks in the caller's credential transaction."""
    if not db.conn.in_transaction:
        raise RuntimeError("OIDC revocation requires an outer account transaction.")
    if db.conn.execute("SELECT 1 FROM accounts WHERE userId = ?", (user,)).fetchone() is None:
        return
    changed = db.conn.execute(
        "UPDATE oidc_revocation_clock SET revision = revision + 1 "
        "WHERE singleton = 1 AND revision < ?", (_MAX_REVISION,),
    ).rowcount
    if changed != 1:
        raise _unavailable()
    revision = db.conn.execute("SELECT revision FROM oidc_revocation_clock WHERE singleton = 1").fetchone()[0]
    db.conn.execute(
        "INSERT INTO oidc_user_revocations VALUES (?, ?) ON CONFLICT(userId) "
        "DO UPDATE SET revokedRevision = excluded.revokedRevision", (user, revision),
    )
    # Issued/exchanging transactions have no known subject yet. Their saved revision
    # is checked once Authlib resolves that subject, and again at final linking.
    db.conn.execute(
        "DELETE FROM oidc_browsers WHERE browserHash IN (SELECT p.browserHash FROM oidc_pending p "
        "JOIN oidc_identities i ON i.issuer = p.issuer AND i.subject = p.subject WHERE i.userId = ?)",
        (user,),
    )


def _origin(value: str) -> str:
    parsed = urlsplit(value)
    return parsed.scheme + "://" + parsed.netloc


def _fixed_url(value: str, *, origin: str | None = None) -> str:
    """Validate only operator-owned endpoints; no caller selects a network destination."""
    if not isinstance(value, str):
        raise _unavailable()
    try:
        parsed = urlsplit(value)
        host = parsed.hostname or ""
        valid = (
            value.isascii() and 1 <= len(value) <= 2048
            and not any(ord(c) <= 32 or ord(c) == 127 for c in value)
            and "\\" not in value and "%" not in parsed.netloc
            and parsed.username is None and parsed.password is None
            and not parsed.query and not parsed.fragment and bool(host)
            and not parsed.netloc.endswith(":")
            and parsed.scheme in {"http", "https"}
            and (parsed.port is None or 1 <= parsed.port <= 65535)
            and (parsed.scheme == "https" or (is_loopback(host) and parsed.port is not None))
            and (origin is None or _origin(value) == origin)
        )
    except (TypeError, ValueError):
        valid = False
    if not valid:
        raise _unavailable()
    return value


def _configuration(settings: Settings) -> tuple[str, str, str, str]:
    issuer = _fixed_url(settings.oidc_issuer_url)
    redirect = _fixed_url(settings.oidc_redirect_uri)
    if urlsplit(redirect).path != COOKIE_PATH + "/callback":
        raise _unavailable()
    client, secret = settings.oidc_client_id, settings.oidc_client_secret
    if not isinstance(client, str) or not 1 <= len(client) <= 256 or not client.isascii():
        raise _unavailable()
    if not isinstance(secret, str) or not 1 <= len(secret.encode("utf-8")) <= 4096:
        raise _unavailable()
    return issuer, client, secret, redirect


def _context(request: Request, settings: Settings, *, callback: bool = False) -> None:
    redirect = _fixed_url(settings.oidc_redirect_uri)
    # Do not trust proxy headers: Host, ASGI scheme and the actual peer are required.
    if request.url.scheme + "://" + request.headers.get("host", "") != _origin(redirect):
        raise APIError(403, "origin_forbidden", "A fixed relying-party origin is required.")
    if is_loopback(urlsplit(redirect).hostname or ""):
        require_loopback(request)
    elif request.url.scheme != "https":
        raise APIError(403, "https_required", "Identity sessions require HTTPS.")
    if not callback and request.headers.get("sec-fetch-site") not in {None, "same-origin", "none"}:
        raise APIError(403, "origin_forbidden", "Cross-origin identity operations are forbidden.")
    if request.headers.get("origin") is not None:
        require_origin(request)


def _csrf(token: str) -> str:
    return hmac.new(token.encode(), b"allira-oidc-csrf-v1", hashlib.sha256).hexdigest()


def _browser(request: Request) -> str:
    token = request.cookies.get(COOKIE_NAME, "")
    if _TOKEN.fullmatch(token) is None:
        raise _session_required()
    browser = _hash(token)
    row = request.app.state.db.conn.execute(
        "SELECT 1 FROM oidc_browsers WHERE browserHash = ? AND expiresAt > ?", (browser, time()),
    ).fetchone()
    if row is None:
        raise _session_required()
    return browser


def _write_context(request: Request, settings: Settings) -> str:
    _context(request, settings)
    require_origin(request)
    browser = _browser(request)
    supplied = request.headers.get("x-allira-oidc-csrf", "")
    token = request.cookies.get(COOKIE_NAME, "")
    if not hmac.compare_digest(supplied.encode(), _csrf(token).encode()):
        raise APIError(403, "csrf_forbidden", "A valid OIDC CSRF token is required.")
    return browser


@dataclass(frozen=True)
class _VerifiedIdentity:
    issuer: str
    subject: str = field(repr=False)
    phone: str = field(repr=False)
    display_name: str
    auth_time: float | None


def _json_object(content: bytes) -> dict:
    def unique_pairs(pairs: list[tuple[str, object]]) -> dict:
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate JSON fields are invalid.")
            result[key] = value
        return result

    def invalid_constant(_: str) -> None:
        raise ValueError("Non-finite JSON values are invalid.")

    parsed = json.loads(content.decode("utf-8"), object_pairs_hook=unique_pairs, parse_constant=invalid_constant)
    if not isinstance(parsed, dict):
        raise TypeError("An object response is required.")
    return parsed


class _BoundedTransport(httpx.AsyncBaseTransport):
    """Bound encoded bodies before Authlib/httpx JSON parsing or decompression."""

    def __init__(self, inner: httpx.AsyncBaseTransport, origin: str) -> None:
        self.inner, self.origin = inner, origin

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        _fixed_url(str(request.url), origin=self.origin)
        request.headers["accept-encoding"] = "identity"
        response = await self.inner.handle_async_request(request)
        try:
            if response.status_code < 200 or response.status_code >= 300:
                raise ValueError("An identity endpoint failed.")
            if response.headers.get("content-encoding", "identity").lower() != "identity":
                raise ValueError("Encoded identity responses are forbidden.")
            declared = response.headers.get("content-length")
            if declared is not None and (not declared.isascii() or not declared.isdigit() or len(declared) > 8 or int(declared) > MAX_HTTP_BYTES):
                raise ValueError("Identity response is oversized.")
            content = bytearray()
            if response.is_stream_consumed:
                # MockTransport may supply an already-buffered synthetic response.
                content.extend(response.content)
            else:
                async for chunk in response.aiter_raw():
                    if len(content) + len(chunk) > MAX_HTTP_BYTES:
                        raise ValueError("Identity response is oversized.")
                    content.extend(chunk)
            if len(content) > MAX_HTTP_BYTES:
                raise ValueError("Identity response is oversized.")
            _json_object(bytes(content))
            return httpx.Response(response.status_code, headers=response.headers, content=bytes(content), request=request)
        finally:
            await response.aclose()

    async def aclose(self) -> None:
        await self.inner.aclose()


def _transport(request: Request, issuer: str) -> _BoundedTransport:
    injected = getattr(request.app.state, "oidc_transport", None)
    if injected is None:
        injected = httpx.AsyncHTTPTransport(retries=0, trust_env=False)
    return _BoundedTransport(injected, _origin(issuer))


async def _metadata(client: AsyncOAuth2Client, issuer: str) -> dict:
    response = await client.request("GET", issuer.rstrip("/") + "/.well-known/openid-configuration", withhold_token=True)
    data = _json_object(response.content)
    if data.get("issuer") != issuer:
        raise ValueError("Issuer metadata does not match.")
    for name in ("authorization_endpoint", "token_endpoint", "jwks_uri"):
        _fixed_url(data.get(name), origin=_origin(issuer))
    if not {"RS256", "ES384"}.intersection(data.get("id_token_signing_alg_values_supported", [])):
        raise ValueError("An allowed signature algorithm is required.")
    if "S256" not in data.get("code_challenge_methods_supported", []):
        raise ValueError("S256 PKCE is required.")
    if data.get("userinfo_endpoint") is not None:
        _fixed_url(data["userinfo_endpoint"], origin=_origin(issuer))
    return data


def _client(settings: Settings, transport: _BoundedTransport, *, state: str | None = None) -> AsyncOAuth2Client:
    _, client_id, secret, redirect = _configuration(settings)
    return AsyncOAuth2Client(
        client_id=client_id, client_secret=secret, scope="openid profile phone", redirect_uri=redirect,
        state=state, code_challenge_method="S256", token_endpoint_auth_method="client_secret_basic",
        transport=transport, trust_env=False, follow_redirects=False, timeout=HTTP_DEADLINE_SECONDS,
    )


async def _exchange(
    request: Request, settings: Settings, flow: sqlite3.Row, code: str,
    diagnostic: _CallbackDiagnostic,
) -> _VerifiedIdentity:
    diagnostic.stage = "exchange.discovery"
    issuer, client_id, _, _ = _configuration(settings)
    async with asyncio.timeout(HTTP_DEADLINE_SECONDS):
        async with _client(settings, _transport(request, issuer)) as client:
            metadata = await _metadata(client, issuer)
            diagnostic.stage = "exchange.token"
            token = await client.fetch_token(
                metadata["token_endpoint"], grant_type="authorization_code", code=code,
                code_verifier=flow["codeVerifier"], redirect_uri=flow["redirectUri"],
            )
            encoded = token.get("id_token")
            access_token = token.get("access_token")
            if not isinstance(encoded, str) or not 1 <= len(encoded) <= 32768:
                raise ValueError("An ID token is required.")
            if not isinstance(access_token, str) or not access_token.isascii() or not 1 <= len(access_token) <= 8192:
                raise ValueError("An access token is required.")
            diagnostic.stage = "exchange.jwks"
            keys = _json_object((await client.request("GET", metadata["jwks_uri"], withhold_token=True)).content)
            key_rows = keys.get("keys")
            if not isinstance(key_rows, list) or not 1 <= len(key_rows) <= 16 or any(
                not isinstance(key, dict) for key in key_rows
            ):
                raise ValueError("A bounded public key set is required.")
            # Logto 1.46 publishes ES384/P-384 by default; older deployments may
            # publish RS256. Never accept symmetric, private or other-curve keys.
            allowed = {"RS256", "ES384"}.intersection(metadata["id_token_signing_alg_values_supported"])
            signature_keys = [key for key in key_rows if key.get("use", "sig") == "sig" and (
                (key.get("kty") == "RSA" and key.get("alg", "RS256") == "RS256" and "RS256" in allowed)
                or (key.get("kty") == "EC" and key.get("crv") == "P-384"
                    and key.get("alg", "ES384") == "ES384" and "ES384" in allowed)
            )]
            if not signature_keys or any(set(key).intersection({"d", "p", "q", "dp", "dq", "qi"}) for key in signature_keys):
                raise ValueError("A supported public signature key is required.")
            diagnostic.stage = "exchange.signature"
            signed = jwt.decode(encoded, jwk.KeySet.import_key_set({"keys": signature_keys}), algorithms=list(allowed))
            diagnostic.stage = "exchange.claims"
            for name in ("exp", "iat"):
                value = signed.claims.get(name)
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                    raise ValueError("A finite numeric token timestamp is required.")
            if signed.claims["exp"] <= signed.claims["iat"]:
                raise ValueError("Token timestamps are inconsistent.")
            hash_length = 32 if signed.header["alg"] == "ES384" else 22
            if "at_hash" in signed.claims and (not isinstance(signed.claims["at_hash"], str) or re.fullmatch(r"[A-Za-z0-9_-]{" + str(hash_length) + "}", signed.claims["at_hash"]) is None):
                raise ValueError("A valid access-token hash is required.")
            verified = CodeIDToken(
                signed.claims, signed.header,
                options={"iss": {"essential": True, "value": issuer}, "aud": {"essential": True, "value": client_id}},
                params={"nonce": flow["nonce"], "client_id": client_id, "access_token": access_token,
                        "max_age": 300 if flow["purpose"] in _FRESH_PURPOSES else None},
            )
            verified.validate(now=time(), leeway=0)
            auth_time = verified.get("auth_time")
            if flow["purpose"] in _FRESH_PURPOSES and not _fresh_auth(auth_time, flow["startedAt"]):
                raise ValueError("Fresh identity authentication is required.")
            subject = verified.get("sub")
            if not isinstance(subject, str) or not subject.isascii() or not 1 <= len(subject) <= 256 or any(ord(c) <= 32 or ord(c) == 127 for c in subject):
                raise ValueError("A bounded subject is required.")
            if "phone_number_verified" in verified and verified["phone_number_verified"] is not True:
                raise ValueError("A verified phone is required.")
            claims = verified
            if "phone_number" not in verified or verified.get("phone_number_verified") is not True:
                diagnostic.stage = "exchange.userinfo"
                endpoint = metadata.get("userinfo_endpoint")
                if not endpoint:
                    raise ValueError("Verified phone information is required.")
                userinfo = _json_object((await client.get(endpoint)).content)
                if userinfo.get("sub") != subject or userinfo.get("phone_number_verified") is not True:
                    raise ValueError("User information does not match the verified subject.")
                claims = userinfo
            diagnostic.stage = "exchange.identity"
            phone = _claim_phone(claims.get("phone_number"))
            if "phone_number" in verified and _claim_phone(verified["phone_number"]) != phone:
                raise ValueError("Phone information is inconsistent.")
            try:
                display = _DISPLAY_NAME.validate_python(claims.get("name", verified.get("name", "新朋友")))
            except ValidationError:
                display = "新朋友"
            identity = _VerifiedIdentity(issuer, subject, phone, display, auth_time)
            diagnostic.stage = "exchange.close"
            return identity


class OidcIdentityRepository:
    def __init__(self, db: Database) -> None:
        self.db, self.conn = db, db.conn

    def cleanup(self) -> None:
        current = time()
        self.conn.execute("DELETE FROM oidc_browsers WHERE expiresAt <= ?", (current,))
        self.conn.execute("DELETE FROM oidc_states WHERE expiresAt <= ?", (current,))
        self.conn.execute("DELETE FROM oidc_pending WHERE expiresAt <= ?", (current,))

    def require_current_flow(self, flow: sqlite3.Row, user: str) -> None:
        revoked = self.conn.execute("SELECT revokedRevision FROM oidc_user_revocations WHERE userId = ?", (user,)).fetchone()
        if revoked is not None and flow["flowRevision"] < revoked["revokedRevision"]:
            raise _pending_required()

    def identity_user(self, pending: sqlite3.Row) -> str | None:
        row = self.conn.execute("SELECT userId FROM oidc_identities WHERE issuer = ? AND subject = ?", (pending["issuer"], pending["subject"])).fetchone()
        return row["userId"] if row else None

    def pending(self, browser: str) -> sqlite3.Row:
        row = self.conn.execute("SELECT * FROM oidc_pending WHERE browserHash = ? AND expiresAt > ?", (browser, time())).fetchone()
        if row is None:
            raise _pending_required()
        user = self.identity_user(row)
        if user is not None:
            self.require_current_flow(row, user)
        return row

    def begin(
        self, browser: str, settings: Settings, *, purpose: str = "login",
        user: str | None = None, session_hash: str | None = None,
    ) -> tuple[str, sqlite3.Row]:
        issuer, client_id, _, redirect = _configuration(settings)
        state = secrets.token_urlsafe(32)
        with self.db.atomic():
            self.conn.execute("DELETE FROM oidc_states WHERE browserHash = ?", (browser,))
            self.conn.execute("DELETE FROM oidc_pending WHERE browserHash = ?", (browser,))
            revision = self.conn.execute("SELECT revision FROM oidc_revocation_clock WHERE singleton = 1").fetchone()[0]
            started = time()
            self.conn.execute(
                "INSERT INTO oidc_states VALUES (?, ?, 'issued', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (_hash(state), browser, secrets.token_urlsafe(32), secrets.token_urlsafe(48), issuer, client_id,
                 redirect, purpose, user, session_hash, started, started + STATE_TTL_SECONDS, revision),
            )
            return state, self.conn.execute("SELECT * FROM oidc_states WHERE stateHash = ?", (_hash(state),)).fetchone()

    def exchanging(self, browser: str, state: str, settings: Settings) -> sqlite3.Row:
        issuer, client, _, redirect = _configuration(settings)
        with self.db.atomic():
            row = self.conn.execute("SELECT * FROM oidc_states WHERE stateHash = ? AND browserHash = ? AND status = 'issued' AND expiresAt > ?", (_hash(state), browser, time())).fetchone()
            if row is None or (row["issuer"], row["clientId"], row["redirectUri"]) != (issuer, client, redirect):
                raise _pending_required()
            self.conn.execute("UPDATE oidc_states SET status = 'exchanging' WHERE stateHash = ?", (row["stateHash"],))
            return row

    def finish(self, previous: sqlite3.Row, identity: _VerifiedIdentity, settings: Settings) -> None:
        issuer, client, _, redirect = _configuration(settings)
        with self.db.atomic():
            row = self.conn.execute("SELECT s.* FROM oidc_states s JOIN oidc_browsers b USING(browserHash) WHERE s.stateHash = ? AND s.status = 'exchanging' AND s.expiresAt > ? AND b.expiresAt > ?", (previous["stateHash"], time(), time())).fetchone()
            if row is None or (row["issuer"], row["clientId"], row["redirectUri"]) != (issuer, client, redirect) or identity.issuer != issuer or row["flowRevision"] != previous["flowRevision"]:
                raise _pending_required()
            mapped = self.conn.execute("SELECT userId FROM oidc_identities WHERE issuer = ? AND subject = ?", (identity.issuer, identity.subject)).fetchone()
            if mapped is not None:
                self.require_current_flow(row, mapped["userId"])
            if row["purpose"] == "erase":
                active = self.conn.execute("SELECT 1 FROM sessions WHERE tokenHash = ? AND userId = ? AND kind = 'browser' AND expiresAt > ?", (row["sessionHash"], row["expectedUserId"], time())).fetchone()
                if mapped is None or mapped["userId"] != row["expectedUserId"] or active is None:
                    raise _pending_required()
            self.conn.execute("DELETE FROM oidc_pending WHERE browserHash = ?", (row["browserHash"],))
            self.conn.execute("INSERT INTO oidc_pending VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                              (_hash(secrets.token_urlsafe(32)), row["browserHash"], identity.issuer,
                               identity.subject, identity.phone, identity.display_name, row["purpose"],
                               row["expectedUserId"], row["sessionHash"], row["startedAt"], identity.auth_time,
                               time() + PENDING_TTL_SECONDS, row["flowRevision"]))
            self.conn.execute("UPDATE oidc_states SET status = 'consumed', nonce = '', codeVerifier = '' WHERE stateHash = ?", (row["stateHash"],))

    def _link(self, pending: sqlite3.Row, user: str) -> None:
        self.require_current_flow(pending, user)
        mapped = self.identity_user(pending)
        existing = self.conn.execute("SELECT issuer, subject FROM oidc_identities WHERE userId = ?", (user,)).fetchone()
        if (mapped is not None and mapped != user) or (existing is not None and (existing["issuer"], existing["subject"]) != (pending["issuer"], pending["subject"])):
            raise _link_required()
        phones = PhoneIdentityRepository(self.db)
        row = self.conn.execute("SELECT phone FROM phone_phones WHERE userId = ?", (user,)).fetchone()
        owner = phones.account_for_phone(pending["phone"])
        if (row is not None and row["phone"] != pending["phone"]) or (owner is not None and owner["userId"] != user):
            raise _link_required()
        phones.bind_phone(user, pending["phone"])
        if existing is None:
            self.conn.execute("INSERT INTO oidc_identities VALUES (?, ?, ?, ?)", (pending["issuer"], pending["subject"], user, now()))

    def consume_proof(
        self, browser: str, pending: sqlite3.Row, user: str, *, purpose: str = "login",
    ) -> None:
        if not self.conn.in_transaction:
            raise RuntimeError("OIDC consumption requires an outer account transaction.")
        current = self.pending(browser)
        if current["purpose"] != purpose or not hmac.compare_digest(current["pendingHash"], pending["pendingHash"]):
            raise _pending_required()
        self.require_current_flow(current, user)
        self.conn.execute("DELETE FROM oidc_browsers WHERE browserHash = ?", (browser,))
        self._link(current, user)

    def new_session(self, user: str, settings: Settings) -> AccountSession:
        token = secrets.token_urlsafe(32)
        self.db.create_session(token, user, settings.session_ttl_seconds)
        return AccountSession(accessToken=token, user=_account_user(_Accounts(self.db).require(user)))

    def migrated_session(self, user: str, settings: Settings) -> AccountSession:
        # Both migration paths consume/link their proof first. These revocations
        # and the replacement session must roll back together with that linking.
        from .account_security import invalidate_recovery_codes
        from .identity_lifecycle import revoke_identity_proofs

        if not self.conn.in_transaction:
            raise RuntimeError("OIDC migration requires an outer account transaction.")
        revoke_identity_proofs(self.db, user)
        invalidate_recovery_codes(self.db, user)
        self.conn.execute("DELETE FROM sessions WHERE userId = ?", (user,))
        return self.new_session(user, settings)

    def complete(
        self, browser: str, settings: Settings, *, confirm_phone_migration: bool = False,
    ) -> AccountSession:
        with self.db.atomic():
            pending = self.pending(browser)
            if pending["purpose"] == "phone_migration":
                if not confirm_phone_migration or not _fresh_auth(pending["authTime"], pending["startedAt"]):
                    raise _pending_required()
                owner = PhoneIdentityRepository(self.db).account_for_phone(pending["phone"])
                # A verified number plus explicit recent OP authentication is
                # permitted only to preserve an existing passwordless owner.
                # Never create an account, merge data or replace a prior link.
                if owner is None or owner["passwordEnabled"] or self.identity_user(pending) is not None:
                    raise _link_required()
                user = owner["userId"]
                if self.conn.execute("SELECT 1 FROM oidc_identities WHERE userId = ?", (user,)).fetchone() is not None:
                    raise _link_required()
                self.consume_proof(browser, pending, user, purpose="phone_migration")
                return self.migrated_session(user, settings)
            if pending["purpose"] != "login" or confirm_phone_migration:
                raise _pending_required()
            user = self.identity_user(pending)
            if user is None:
                if not settings.registration_enabled:
                    raise APIError(403, "registration_disabled", "Account registration is disabled.")
                if PhoneIdentityRepository(self.db).account_for_phone(pending["phone"]) is not None:
                    raise _link_required()
                row = PhoneIdentityRepository(self.db).create_phone_account(pending["phone"], None, None, pending["displayName"])
                user = row["userId"]
            self.consume_proof(browser, pending, user)
            return self.new_session(user, settings)


def _clear_cookie(response: Response, request: Request) -> None:
    response.delete_cookie(COOKIE_NAME, path=COOKIE_PATH, httponly=True, secure=request.url.scheme == "https", samesite="lax")


def oidc_identity_router(settings: Settings, current_user: Callable[..., str]) -> APIRouter:
    """Root supplies shared lifecycle hooks; this cookie never authenticates APIs/admin."""
    router = APIRouter(prefix=COOKIE_PATH, tags=["OIDC identity"])
    User = Annotated[str, Depends(current_user)]

    async def enabled() -> None:
        if settings.auth_backend != "oidc":
            raise APIError(403, "oidc_disabled", "OIDC authentication is disabled.")
        if not settings.oidc_enabled:
            raise _unavailable()
        _configuration(settings)

    @router.get("/session", response_model=OidcSession, dependencies=[Depends(enabled)])
    async def session(request: Request, response: Response):
        _context(request, settings)
        db: Database = request.app.state.db
        repo = OidcIdentityRepository(db)
        token = request.cookies.get(COOKIE_NAME, "")
        with db.atomic():
            repo.cleanup()
            row = None if _TOKEN.fullmatch(token) is None else db.conn.execute("SELECT * FROM oidc_browsers WHERE browserHash = ? AND expiresAt > ?", (_hash(token), time())).fetchone()
            if row is None:
                if db.conn.execute("SELECT COUNT(*) FROM oidc_browsers").fetchone()[0] >= MAX_BROWSERS:
                    raise APIError(429, "rate_limited", "Identity capacity is busy. Try later.")
                token = secrets.token_urlsafe(32)
                db.conn.execute("INSERT INTO oidc_browsers VALUES (?, ?)", (_hash(token), time() + BROWSER_TTL_SECONDS))
            pending = db.conn.execute("SELECT * FROM oidc_pending WHERE browserHash = ? AND expiresAt > ?", (_hash(token), time())).fetchone()
            if pending is not None:
                repo.pending(_hash(token))
        response.set_cookie(COOKIE_NAME, token, max_age=BROWSER_TTL_SECONDS, path=COOKIE_PATH, httponly=True, secure=request.url.scheme == "https", samesite="lax")
        return OidcSession(csrfToken=_csrf(token), pending=None if pending is None else OidcPending(purpose=pending["purpose"], maskedPhone=_masked(pending["phone"]), expiresAt=_iso(pending["expiresAt"])), simulated=settings.oidc_simulated)

    @router.post("/start", response_model=OidcStart, dependencies=[Depends(enabled)])
    async def start(body: OidcStartInput, request: Request):
        browser = _write_context(request, settings)
        _auth_tools(request).attempts.check("oidc_start", _peer(request), LOGIN_ATTEMPTS)
        repo = OidcIdentityRepository(request.app.state.db)
        user, session_hash = None, None
        if body.purpose == "erase":
            # A remembered browser device is required; never fall back from any
            # explicit Authorization header to an ambient ordinary cookie.
            if request.headers.get("authorization") is not None:
                raise APIError(401, "browser_required", "Use a remembered browser device for data erasure.")
            user = authenticate_browser(request)
            if repo.conn.execute("SELECT 1 FROM oidc_identities WHERE userId = ? AND issuer = ?", (user, settings.oidc_issuer_url)).fetchone() is None:
                raise _pending_required()
            session_hash = _hash(request.state.session_token)
        state, flow = repo.begin(browser, settings, purpose=body.purpose, user=user, session_hash=session_hash)
        issuer, _, _, _ = _configuration(settings)
        try:
            async with asyncio.timeout(HTTP_DEADLINE_SECONDS):
                async with _client(settings, _transport(request, issuer), state=state) as client:
                    metadata = await _metadata(client, issuer)
                    fresh = {"prompt": "login", "max_age": 0} if body.purpose in _FRESH_PURPOSES else {}
                    authorization, _ = client.create_authorization_url(metadata["authorization_endpoint"], state=state, nonce=flow["nonce"], code_verifier=flow["codeVerifier"], **fresh)
            # A cancelled/expired/replaced browser transaction cannot be published.
            current = repo.conn.execute("SELECT 1 FROM oidc_states s JOIN oidc_browsers b USING(browserHash) WHERE stateHash = ? AND status = 'issued' AND s.expiresAt > ? AND b.expiresAt > ?", (flow["stateHash"], time(), time())).fetchone()
            if current is None:
                raise _session_required()
            return OidcStart(authorizationUrl=authorization)
        except (APIError, httpx.HTTPError, AuthlibBaseError, JoseError, ValueError, TypeError, RuntimeError, TimeoutError):
            with repo.db.atomic():
                repo.conn.execute("DELETE FROM oidc_states WHERE stateHash = ?", (flow["stateHash"],))
            raise _unavailable() from None

    @router.get("/callback", dependencies=[Depends(enabled)])
    async def callback(request: Request):
        repo = OidcIdentityRepository(request.app.state.db)
        flow = None
        diagnostic = _CallbackDiagnostic()
        try:
            _context(request, settings, callback=True)
            diagnostic.stage = "callback.browser"
            browser = _browser(request)
            diagnostic.stage = "callback.state"
            values = request.query_params
            if len(values.multi_items()) != len(values):
                raise _pending_required()
            state, code = values.get("state", ""), values.get("code", "")
            if _TOKEN.fullmatch(state) is None:
                raise _pending_required()
            # Consume matching state even for a provider error/bad authorization
            # response, preventing a later replay from reviving that transaction.
            flow = repo.exchanging(browser, state, settings)
            diagnostic.stage = "callback.response"
            if values.get("error") is not None or not 1 <= len(code) <= 2048 or not code.isascii() or any(ord(c) <= 32 or ord(c) == 127 for c in code):
                raise _pending_required()
            if "iss" in values and values["iss"] != settings.oidc_issuer_url:
                raise _pending_required()
            identity = await _exchange(request, settings, flow, code, diagnostic)
            diagnostic.stage = "callback.finish"
            repo.finish(flow, identity, settings)
        except (APIError, httpx.HTTPError, AuthlibBaseError, JoseError, ValueError, TypeError, RuntimeError, TimeoutError) as error:
            _callback_failure(diagnostic, error)
            if flow is not None:
                with repo.db.atomic():
                    repo.conn.execute("DELETE FROM oidc_states WHERE stateHash = ?", (flow["stateHash"],))
        return RedirectResponse("/test/", status_code=303, headers={"Cache-Control": "no-store"})

    @router.post("/complete", response_model=AccountSession, dependencies=[Depends(enabled)])
    async def complete(body: OidcComplete, request: Request, response: Response):
        browser = _write_context(request, settings)
        result = OidcIdentityRepository(request.app.state.db).complete(
            browser, settings, confirm_phone_migration=body.confirmPhoneMigration,
        )
        _clear_cookie(response, request)
        return result

    @router.post("/migrate", response_model=AccountSession, dependencies=[Depends(enabled)])
    async def migrate(body: OidcMigrate, request: Request, response: Response):
        browser = _write_context(request, settings)
        db: Database = request.app.state.db
        repo, legacy, tools = OidcIdentityRepository(db), _Accounts(db), _auth_tools(request)
        pending = repo.pending(browser)
        if pending["purpose"] != "login" or body.confirmPhoneMigration:
            raise _pending_required()
        tools.attempts.check("oidc_migrate_peer", _peer(request), LOGIN_ATTEMPTS)
        tools.attempts.check("oidc_migrate_username", body.username, LOGIN_ATTEMPTS)
        previous = legacy.by_username(body.username)
        salt = previous["passwordSalt"] if previous is not None else tools.dummy_salt
        expected = previous["passwordHash"] if previous is not None else tools.dummy_digest
        digest = await tools.workers.run(lambda: accounts._derive_password(body.currentPassword, salt))
        valid = hmac.compare_digest(digest, expected)
        if previous is None or not valid or not previous["passwordEnabled"]:
            raise _invalid_credentials()
        with db.atomic():
            _browser(request)
            current = repo.pending(browser)
            if not hmac.compare_digest(current["pendingHash"], pending["pendingHash"]):
                raise _pending_required()
            row = legacy.unchanged(previous)
            if repo.conn.execute("SELECT 1 FROM oidc_identities WHERE userId = ?", (row["userId"],)).fetchone() is not None:
                raise _link_required()
            repo.consume_proof(browser, pending, row["userId"])
            # Consume first: revocation may delete every linked transition browser.
            # Failure of any subsequent step rolls back proof/link/credential cleanup.
            result = repo.migrated_session(row["userId"], settings)
        _clear_cookie(response, request)
        return result

    @router.post("/erase", response_model=OK, dependencies=[Depends(enabled)])
    async def erase(body: OidcErase, request: Request, response: Response, user: User):
        browser = _write_context(request, settings)
        db: Database = request.app.state.db
        repo = OidcIdentityRepository(db)
        with db.atomic():
            pending = repo.pending(browser)
            if pending["purpose"] != "erase" or pending["expectedUserId"] != user or getattr(request.state, "auth_kind", "bearer") != "browser" or not hmac.compare_digest(pending["sessionHash"] or "", _hash(request.state.session_token)) or repo.identity_user(pending) != user:
                raise _pending_required()
            if not _fresh_auth(pending["authTime"], pending["startedAt"]):
                raise _pending_required()
            # Both dependencies and all synchronous erasure work run on this event
            # loop with no await between final credential/proof checks and mutation.
            accounts.reauthorize_account(request, user)
            previous = _Accounts(db).require(user)
            db.conn.execute("DELETE FROM oidc_browsers WHERE browserHash = ?", (browser,))
            AccountDataRepository(db, settings).erase(user, previous, request.app.state.runtime.in_flight_run_ids())
        clear_cookie(response, secure=request.url.scheme == "https")
        _clear_cookie(response, request)
        return OK()

    @router.post("/cancel", response_model=OK, dependencies=[Depends(enabled)])
    async def cancel(request: Request, response: Response):
        browser = _write_context(request, settings)
        with request.app.state.db.atomic():
            request.app.state.db.conn.execute("DELETE FROM oidc_browsers WHERE browserHash = ?", (browser,))
        _clear_cookie(response, request)
        return OK()

    return router
