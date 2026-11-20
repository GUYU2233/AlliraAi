"""Synthetic OIDC RP verification, not a real Logto or channel acceptance test.

Authlib performs authorization-code/PKCE and claims validation; joserfc signs test
JWTs and verifies production paths. All HTTP uses an injected MockTransport.
"""

import asyncio
import base64
import hashlib
import json
import logging
import secrets
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from time import time
from types import SimpleNamespace
from typing import Annotated
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from authlib.common.errors import AuthlibBaseError
from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.testclient import TestClient
from joserfc import jwk, jwt
from joserfc.errors import JoseError

from allira import accounts, oidc_identity
from allira.accounts import AccountRegister, _Accounts
from allira.browser_sessions import authenticate_browser, csrf_token
from allira.config import Settings
from allira.db import APIError, Database, now
from allira.oidc_identity import (
    COOKIE_NAME,
    COOKIE_PATH,
    OidcIdentityRepository,
    clear_transient_oidc_state,
    initialize_oidc_identity,
    oidc_identity_router,
    revoke_user_oidc_flows,
)
from allira.phone_identity import PhoneIdentityRepository, normalize_phone
from allira.schemas import ConversationInput

ORIGIN = "http://127.0.0.1:8000"
ISSUER = "http://127.0.0.1:3001/oidc"
CLIENT_ID = "synthetic-oidc-client"
CLIENT_SECRET = "synthetic-confidential-client-secret"
PHONE = "+8613800138000"
OTHER_PHONE = "+8613900139000"
PASSWORD = "correct-legacy-migration-password"
CONFIRM = {"acceptedTermsVersion": "2026-11-19", "isAdult": True, "confirmIdentity": True}
ACCESS = "synthetic-provider-access-token-not-an-allira-session"
REFRESH = "synthetic-provider-refresh-token-never-stored"


def hashed(value):
    return hashlib.sha256(value.encode()).hexdigest()


def settings_for(path=":memory:", **changes):
    baseline = Settings(_env_file=None, database_path=path, provider="mock").model_dump()
    return SimpleNamespace(**{
        **baseline, "auth_backend": "oidc", "oidc_issuer_url": ISSUER,
        "oidc_client_id": CLIENT_ID, "oidc_client_secret": CLIENT_SECRET,
        "oidc_redirect_uri": ORIGIN + COOKIE_PATH + "/callback", "oidc_simulated": True,
        "session_ttl_seconds": 300, **changes,
    })


@pytest.fixture(scope="session")
def rsa_key():
    return jwk.RSAKey.generate_key(2048, parameters={"kid": "synthetic-rsa", "alg": "RS256", "use": "sig"})


class FakeIssuer:
    def __init__(self, key):
        self.key = key
        self.subject = "synthetic-logto-subject"
        self.phone = PHONE
        self.requests = []
        self.claims = {}
        self.drop_claims = set()
        self.userinfo = None
        self.metadata_changes = {}
        self.token_changes = {}
        self.fail_path = None
        self.response_override = None
        self.algorithm = "RS256"
        self.signing_key = key
        self.public_keys = None
        self.on_token = None
        self.before_response = None
        self.db = None

    def metadata(self):
        return {
            "issuer": ISSUER, "authorization_endpoint": ISSUER + "/auth",
            "token_endpoint": ISSUER + "/token", "jwks_uri": ISSUER + "/jwks",
            "userinfo_endpoint": ISSUER + "/me",
            "id_token_signing_alg_values_supported": ["RS256"],
            "code_challenge_methods_supported": ["S256"], **self.metadata_changes,
        }

    async def __call__(self, request):
        return self.streaming(await self.respond(request))

    def streaming(self, response):
        return response

    async def respond(self, request):
        # Every HTTP operation, including discovery, is outside SQLite transactions.
        assert not self.db.conn.in_transaction
        self.requests.append(request)
        if self.before_response is not None:
            await self.before_response(request)
        if self.response_override is not None:
            response = self.response_override(request)
            if response is not None:
                return response
        if self.fail_path == request.url.path:
            raise httpx.ConnectError("synthetic-provider-error " + ACCESS, request=request)
        if request.url.path.endswith("/.well-known/openid-configuration"):
            return httpx.Response(200, json=self.metadata())
        if request.url.path == "/oidc/jwks":
            return httpx.Response(200, json={"keys": self.public_keys or [self.key.as_dict(private=False)]})
        if request.url.path == "/oidc/me":
            assert request.headers["authorization"] == "Bearer " + ACCESS
            return httpx.Response(200, json=self.userinfo or {
                "sub": self.subject, "phone_number": self.phone,
                "phone_number_verified": True, "name": "手机朋友",
            })
        if request.url.path == "/oidc/token":
            body = parse_qs(request.content.decode())
            assert body["grant_type"] == ["authorization_code"]
            assert body["redirect_uri"] == [ORIGIN + COOKIE_PATH + "/callback"]
            assert request.headers["authorization"] == "Basic " + base64.b64encode(
                (CLIENT_ID + ":" + CLIENT_SECRET).encode(),
            ).decode()
            flow = self.db.conn.execute("SELECT * FROM oidc_states WHERE status = 'exchanging'").fetchone()
            assert body["code_verifier"] == [flow["codeVerifier"]]
            if self.on_token is not None:
                self.on_token(flow)
            claims = {
                "iss": ISSUER, "sub": self.subject, "aud": CLIENT_ID,
                "exp": int(time()) + 300, "iat": int(time()), "nonce": flow["nonce"],
                "phone_number": self.phone, "phone_number_verified": True,
                "name": "手机朋友", "auth_time": int(time()),
                "at_hash": base64.urlsafe_b64encode(
                    (hashlib.sha384(ACCESS.encode()).digest()[:24] if self.algorithm == "ES384"
                     else hashlib.sha256(ACCESS.encode()).digest()[:16]),
                ).rstrip(b"=").decode(),
                **self.claims,
            }
            for name in self.drop_claims:
                claims.pop(name, None)
            encoded = jwt.encode(
                {"alg": self.algorithm, "kid": "synthetic-rsa"}, claims, self.signing_key,
                algorithms=[self.algorithm],
            )
            return httpx.Response(200, json={
                "id_token": encoded, "access_token": ACCESS, "token_type": "Bearer",
                "expires_in": 300, "refresh_token": REFRESH, **self.token_changes,
            })
        raise AssertionError("Unexpected synthetic destination: " + str(request.url))


def make_app(issuer, *, settings=None):
    settings = settings or settings_for()

    @asynccontextmanager
    async def lifespan(app):
        # Old credential survival/rollback is tested independently of root's OIDC
        # current_user gate; the RP router itself still uses the explicit OIDC config.
        db_settings = SimpleNamespace(**{**vars(settings), "auth_backend": "legacy"})
        db = Database(settings.database_path, db_settings)
        initialize_oidc_identity(db)
        app.state.db, app.state.settings = db, settings
        app.state.runtime = SimpleNamespace(in_flight_run_ids=lambda: set())
        issuer.db = db
        app.state.oidc_transport = httpx.MockTransport(issuer)
        try:
            yield
        finally:
            db.close()

    app = FastAPI(lifespan=lifespan)

    @app.exception_handler(APIError)
    async def api_error(request, exc):
        return JSONResponse({"error": {"code": exc.code, "message": exc.message}}, status_code=exc.status)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        return JSONResponse({"error": {"code": "validation_error", "message": "Invalid parameters."}}, status_code=422)

    @app.exception_handler(Exception)
    async def unexpected_error(request, exc):
        return JSONResponse({"error": {"code": "internal_error", "message": "Internal error."}}, status_code=500)

    bearer = HTTPBearer(auto_error=False)

    async def current_user(
        request: Request,
        credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    ):
        if "authorization" not in request.headers:
            return authenticate_browser(request)
        if credentials is None or credentials.scheme.lower() != "bearer":
            raise APIError(401, "unauthorized", "A valid session is required.")
        request.state.session_token, request.state.auth_kind = credentials.credentials, "bearer"
        return request.app.state.db.authenticate(credentials.credentials, kind="bearer")

    app.include_router(oidc_identity_router(settings, current_user))
    return app


@pytest.fixture(autouse=True)
def fast_passwords(monkeypatch):
    monkeypatch.setattr(accounts, "_derive_password", lambda password, salt: hashlib.sha512(
        salt + password.get_secret_value().encode(),
    ).digest())


@pytest.fixture
def client(rsa_key):
    issuer = FakeIssuer(rsa_key)
    with TestClient(make_app(issuer), base_url=ORIGIN, client=("127.0.0.1", 1234),
                    raise_server_exceptions=False) as client:
        client.issuer = issuer
        yield client


def error(response, status, code):
    assert response.status_code == status, response.text
    assert response.json()["error"]["code"] == code
    assert set(response.json()) == {"error"}


def oidc_headers(client, **extra):
    response = client.get(COOKIE_PATH + "/session")
    assert response.status_code == 200, response.text
    return {"Origin": ORIGIN, "X-Allira-OIDC-CSRF": response.json()["csrfToken"], **extra}


def start(client, *, purpose="login", headers=None):
    headers = headers or oidc_headers(client)
    response = client.post(COOKIE_PATH + "/start", json={"purpose": purpose}, headers=headers)
    assert response.status_code == 200, response.text
    query = parse_qs(urlsplit(response.json()["authorizationUrl"]).query)
    return query, headers


def callback(client, query, **extra):
    return client.get(COOKIE_PATH + "/callback", params={"state": query["state"][0], "code": "synthetic-code", **extra}, follow_redirects=False)


def pending(client, *, purpose="login"):
    query, headers = start(client, purpose=purpose)
    response = callback(client, query)
    assert response.status_code == 303 and response.headers["location"] == "/test/"
    view = client.get(COOKIE_PATH + "/session").json()
    assert view["pending"] is not None, view
    return headers


def complete(client):
    headers = pending(client)
    response = client.post(COOKIE_PATH + "/complete", json=CONFIRM, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def legacy(client, username="legacy_user", phone=None):
    db = client.app.state.db
    body = AccountRegister(username=username, password=PASSWORD, acceptedTermsVersion="2026-11-19", isAdult=True)
    salt = secrets.token_bytes(16)
    result = _Accounts(db).register(body, salt, accounts._derive_password(body.password, salt), secrets.token_urlsafe(32), 300)
    if phone:
        with db.atomic():
            db.conn.execute("INSERT INTO phone_phones VALUES (?, ?, ?)", (phone, result.user.id, now()))
    return result


def phone_only(client, phone=PHONE):
    db = client.app.state.db
    with db.atomic():
        row = PhoneIdentityRepository(db).create_phone_account(phone, None, None, "旧手机朋友")
        db.create_session("synthetic-old-phone-bearer", row["userId"], 300)
    return row


def remember(client, user):
    token = secrets.token_urlsafe(32)
    client.app.state.db.create_session(token, user, 300, kind="browser", label="Browser")
    client.cookies.set("allira_user", token, domain="127.0.0.1", path="/api/v1")
    return token


def erase_pending(client, user):
    token = remember(client, user)
    headers = oidc_headers(client, **{"X-CSRF-Token": csrf_token(token)})
    query, _ = start(client, purpose="erase", headers=headers)
    assert query["prompt"] == ["login"] and query["max_age"] == ["0"]
    callback(client, query)
    view = client.get(COOKIE_PATH + "/session").json()
    assert view["pending"]["purpose"] == "erase", view
    return headers, token


def test_native_authlib_pkce_nonce_cookie_and_pending_are_not_login(client, caplog):
    response = client.get(COOKIE_PATH + "/session")
    assert response.json() == {"csrfToken": response.json()["csrfToken"], "pending": None, "enabled": True, "simulated": True}
    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie and "SameSite=lax" in cookie and "Path=" + COOKIE_PATH in cookie
    assert "Domain=" not in cookie and "Secure" not in cookie
    query, headers = start(client)
    assert query["response_type"] == ["code"] and query["scope"] == ["openid profile phone"]
    assert query["code_challenge_method"] == ["S256"]
    flow = client.app.state.db.conn.execute("SELECT * FROM oidc_states").fetchone()
    expected = base64.urlsafe_b64encode(hashlib.sha256(flow["codeVerifier"].encode()).digest()).rstrip(b"=").decode()
    assert query["code_challenge"] == [expected] and query["nonce"] == [flow["nonce"]]
    assert flow["stateHash"] == hashed(query["state"][0])
    callback(client, query)
    db = client.app.state.db
    assert db.conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0
    assert db.conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0] == 0
    view = client.get(COOKIE_PATH + "/session").json()["pending"]
    assert view["provider"] == "logto" and view["purpose"] == "login"
    assert view["maskedPhone"] == "+86 138****8000"
    assert "subject" not in view and "phone" not in view
    assert not db.conn.in_transaction
    for secret in (ACCESS, REFRESH, CLIENT_SECRET):
        dump = "\n".join(str(tuple(row)) for table in ("oidc_browsers", "oidc_states", "oidc_pending") for row in db.conn.execute("SELECT * FROM " + table))
        assert secret not in dump and secret not in caplog.text
    assert client.post(COOKIE_PATH + "/complete", json=CONFIRM, headers=headers).status_code == 200


def test_complete_immutable_subject_mapping_and_existing_other_device_survives(client):
    first = complete(client)
    assert first["user"]["id"].startswith("acct_") and first["user"]["username"].startswith("p_")
    db = client.app.state.db
    row = _Accounts(db).require(first["user"]["id"])
    assert row["passwordEnabled"] == 0 and len(row["passwordSalt"]) == 16 and len(row["passwordHash"]) == 64
    mapping = db.conn.execute("SELECT * FROM oidc_identities").fetchone()
    assert (mapping["issuer"], mapping["subject"], mapping["userId"]) == (ISSUER, client.issuer.subject, row["userId"])
    client.issuer.claims["name"] = "后来的昵称不覆盖"
    second = complete(client)
    assert second["user"] == first["user"]
    assert db.authenticate(first["accessToken"]) == row["userId"]
    assert db.authenticate(second["accessToken"]) == row["userId"]
    assert db.conn.execute("PRAGMA foreign_key_check").fetchall() == []


def test_existing_phone_never_silently_merges_requires_explicit_migration(client):
    old = legacy(client, phone=PHONE)
    headers = pending(client)
    error(client.post(COOKIE_PATH + "/complete", json=CONFIRM, headers=headers), 409, "identity_link_required")
    assert client.app.state.db.conn.execute("SELECT COUNT(*) FROM oidc_identities").fetchone()[0] == 0
    response = client.post(COOKIE_PATH + "/migrate", json={**CONFIRM, "username": "LEGACY_USER", "currentPassword": PASSWORD}, headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()["user"]["id"] == old.user.id
    assert response.json()["user"]["username"] == old.user.username


@pytest.mark.parametrize("changes", [
    {"isAdult": False}, {"isAdult": "true"}, {"isAdult": 1}, {"confirmIdentity": False},
    {"confirmIdentity": "true"}, {"acceptedTermsVersion": "wrong"}, {"extra": "rejected"},
])
def test_terms_adult_explicit_confirmation_required_before_new_account(client, changes):
    headers = pending(client)
    error(client.post(COOKIE_PATH + "/complete", json={**CONFIRM, **changes}, headers=headers), 422, "validation_error")
    assert client.app.state.db.conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0] == 0


@pytest.mark.parametrize("claims", [
    {"iss": "https://wrong.example/oidc"}, {"aud": "other-client"},
    {"aud": [CLIENT_ID, "other"], "azp": "wrong"}, {"aud": [CLIENT_ID, "other"]},
    {"azp": "wrong"}, {"nonce": "wrong"}, {"exp": 1}, {"iat": 99999999999},
    {"exp": True}, {"iat": True}, {"exp": "99999999999"}, {"iat": "1"},
    {"sub": ""}, {"sub": "with space"}, {"sub": "用户"}, {"sub": "x" * 257},
    {"phone_number_verified": False}, {"phone_number_verified": "true"},
    {"phone_number_verified": 1}, {"phone_number": "+12025550123"},
    {"at_hash": "wrong-token-hash-value_"}, {"at_hash": False}, {"at_hash": ""},
])
def test_bad_signed_claims_never_create_pending_or_account(client, claims):
    client.issuer.claims = claims
    query, _ = start(client)
    callback(client, query)
    db = client.app.state.db
    assert db.conn.execute("SELECT COUNT(*) FROM oidc_pending").fetchone()[0] == 0
    assert db.conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0
    assert db.conn.execute("SELECT COUNT(*) FROM oidc_states").fetchone()[0] == 0
    assert not db.conn.in_transaction


@pytest.mark.parametrize("missing", ["iss", "sub", "aud", "exp", "iat", "nonce"])
def test_required_id_token_claims_are_checked_by_authlib(client, missing):
    client.issuer.drop_claims.add(missing)
    query, _ = start(client)
    callback(client, query)
    assert client.app.state.db.conn.execute("SELECT COUNT(*) FROM oidc_pending").fetchone()[0] == 0


def test_real_logto_es384_p384_signature_and_access_hash_supported(client):
    key = jwk.ECKey.generate_key("P-384", parameters={"kid": "synthetic-rsa", "alg": "ES384", "use": "sig"})
    client.issuer.key = client.issuer.signing_key = key
    client.issuer.algorithm = "ES384"
    client.issuer.metadata_changes["id_token_signing_alg_values_supported"] = ["ES384"]
    assert complete(client)["user"]["id"].startswith("acct_")


@pytest.mark.parametrize("change", ["curve", "kty", "alg", "private", "use", "kid"])
def test_es384_wrong_key_metadata_and_private_material_rejected(client, change):
    key = jwk.ECKey.generate_key("P-384", parameters={"kid": "synthetic-rsa", "alg": "ES384", "use": "sig"})
    client.issuer.key = client.issuer.signing_key = key
    client.issuer.algorithm = "ES384"
    client.issuer.metadata_changes["id_token_signing_alg_values_supported"] = ["ES384"]
    public = key.as_dict(private=change == "private")
    if change != "private":
        field, value = {
            "curve": ("crv", "P-256"), "kty": ("kty", "oct"),
            "alg": ("alg", "ES256"), "use": ("use", "enc"), "kid": ("kid", "another"),
        }[change]
        public[field] = value
    client.issuer.public_keys = [public]
    query, _ = start(client)
    callback(client, query)
    assert client.app.state.db.conn.execute("SELECT COUNT(*) FROM oidc_pending").fetchone()[0] == 0


def test_wrong_signature_and_algorithm_rejected(client):
    client.issuer.signing_key = jwk.RSAKey.generate_key(2048)
    query, _ = start(client)
    callback(client, query)
    assert client.app.state.db.conn.execute("SELECT COUNT(*) FROM oidc_pending").fetchone()[0] == 0
    client.issuer.algorithm = "HS256"
    client.issuer.signing_key = jwk.OctKey.import_key(b"x" * 32)
    query, _ = start(client)
    callback(client, query)
    assert client.app.state.db.conn.execute("SELECT COUNT(*) FROM oidc_pending").fetchone()[0] == 0


@pytest.mark.parametrize("userinfo", [
    {"sub": "different", "phone_number": PHONE, "phone_number_verified": True},
    {"sub": "synthetic-logto-subject", "phone_number": PHONE, "phone_number_verified": False},
    {"sub": "synthetic-logto-subject", "phone_number": PHONE, "phone_number_verified": "true"},
    {"sub": "synthetic-logto-subject", "phone_number": "+12025550123", "phone_number_verified": True},
])
def test_userinfo_must_match_verified_sub_and_verified_cn_phone(client, userinfo):
    client.issuer.drop_claims.update({"phone_number", "phone_number_verified"})
    client.issuer.userinfo = userinfo
    query, _ = start(client)
    callback(client, query)
    assert client.app.state.db.conn.execute("SELECT COUNT(*) FROM oidc_pending").fetchone()[0] == 0


def test_standard_logto_userinfo_claims_supported_only_after_id_token_verification(client):
    client.issuer.drop_claims.update({"phone_number", "phone_number_verified"})
    session = complete(client)
    assert session["user"]["displayName"] == "手机朋友"
    assert any(request.url.path == "/oidc/me" for request in client.issuer.requests)


def test_explicit_false_phone_verified_is_not_overridden_by_userinfo(client):
    client.issuer.claims["phone_number_verified"] = False
    query, _ = start(client)
    callback(client, query)
    assert not any(request.url.path == "/oidc/me" for request in client.issuer.requests)


@pytest.mark.parametrize("extra", [{"error": "access_denied"}, {"iss": "https://wrong.example"}, {"code": "bad code"}])
def test_matching_error_callback_consumes_state_and_never_replays(client, extra):
    query, _ = start(client)
    callback(client, query, **extra)
    assert client.app.state.db.conn.execute("SELECT COUNT(*) FROM oidc_states").fetchone()[0] == 0
    count = len(client.issuer.requests)
    callback(client, query)
    assert len(client.issuer.requests) == count


def test_callback_wrong_browser_state_duplicate_parameters_and_single_use(client, rsa_key):
    query, _ = start(client)
    original = client.cookies.get(COOKIE_NAME)
    client.cookies.clear()
    callback(client, query)
    assert client.app.state.db.conn.execute("SELECT COUNT(*) FROM oidc_pending").fetchone()[0] == 0
    client.cookies.set(COOKIE_NAME, original, domain="127.0.0.1", path=COOKIE_PATH)
    client.get(COOKIE_PATH + "/callback", params=[("state", query["state"][0]), ("state", query["state"][0]), ("code", "test")], follow_redirects=False)
    assert client.app.state.db.conn.execute("SELECT status FROM oidc_states").fetchone()[0] == "issued"
    callback(client, query)
    count = len(client.issuer.requests)
    callback(client, query)
    assert len(client.issuer.requests) == count


@pytest.mark.parametrize("headers", [
    {}, {"Origin": "https://foreign.example"}, {"Origin": ORIGIN, "X-Allira-OIDC-CSRF": "bad"},
    {"Origin": ORIGIN, "Sec-Fetch-Site": "cross-site"},
])
def test_start_requires_independent_origin_and_csrf(client, headers):
    client.get(COOKIE_PATH + "/session")
    response = client.post(COOKIE_PATH + "/start", json={}, headers=headers)
    assert response.status_code == 403
    assert not client.issuer.requests


@pytest.mark.parametrize("path,body", [("complete", CONFIRM), ("migrate", {**CONFIRM, "username": "legacy_user", "currentPassword": PASSWORD}), ("cancel", {})])
def test_oidc_cookie_without_pending_or_ordinary_bearer_does_not_authenticate(client, path, body):
    headers = oidc_headers(client)
    response = client.post(COOKIE_PATH + "/" + path, json=body, headers=headers)
    assert response.status_code == (200 if path == "cancel" else 401)
    assert client.app.state.db.conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0


@pytest.mark.parametrize("peer,host,forwarded", [
    ("203.0.113.1", "127.0.0.1:8000", "127.0.0.1"),
    ("127.0.0.1", "localhost:8000", None),
    ("127.0.0.1", "evil.example", None),
])
def test_loopback_fixed_rp_origin_uses_actual_peer_and_host(rsa_key, peer, host, forwarded):
    issuer = FakeIssuer(rsa_key)
    with TestClient(make_app(issuer), base_url=ORIGIN, client=(peer, 1234)) as client:
        headers = {"Host": host}
        if forwarded:
            headers["X-Forwarded-For"] = forwarded
        assert client.get(COOKIE_PATH + "/session", headers=headers).status_code == 403


@pytest.mark.parametrize("metadata", [
    {"issuer": "https://wrong.example"}, {"token_endpoint": "http://127.0.0.1:9000/token"},
    {"authorization_endpoint": "http://evil.example/auth"},
    {"jwks_uri": ISSUER + "/jwks?secret=1"},
    {"userinfo_endpoint": "https://evil.example/userinfo"},
    {"id_token_signing_alg_values_supported": ["HS256"]}, {"code_challenge_methods_supported": ["plain"]},
])
def test_discovery_fixed_origin_and_required_standard_algorithms(client, metadata):
    client.issuer.metadata_changes = metadata
    response = client.post(COOKIE_PATH + "/start", json={}, headers=oidc_headers(client))
    error(response, 503, "oidc_unavailable")
    assert client.app.state.db.conn.execute("SELECT COUNT(*) FROM oidc_states").fetchone()[0] == 0


@pytest.mark.parametrize("response", [
    lambda: httpx.Response(302, headers={"location": "https://evil.example"}),
    lambda: httpx.Response(500, text=ACCESS),
    lambda: httpx.Response(200, content=b"x" * 65537),
    lambda: httpx.Response(200, headers={"content-length": "65537"}, content=b"{}"),
    lambda: httpx.Response(200, headers={"content-encoding": "gzip"}, content=b"{}"),
    lambda: httpx.Response(200, content=b'{"issuer":"one","issuer":"two"}'),
    lambda: httpx.Response(200, content=b'{"key":NaN}'),
    lambda: httpx.Response(200, content=b"\xff"),
    lambda: httpx.Response(200, json=[]),
])
def test_bounded_identity_responses_and_no_redirect_or_secret_errors(client, response, caplog):
    client.issuer.response_override = lambda request: response()
    result = client.post(COOKIE_PATH + "/start", json={}, headers=oidc_headers(client))
    error(result, 503, "oidc_unavailable")
    assert ACCESS not in result.text and ACCESS not in caplog.text


def test_entire_exchange_timeout_leaves_no_pending(client, monkeypatch):
    query, _ = start(client)
    monkeypatch.setattr(oidc_identity, "HTTP_DEADLINE_SECONDS", 0.02)

    async def delay(request):
        await asyncio.sleep(1)

    client.issuer.before_response = delay
    response = callback(client, query)
    assert response.status_code == 303
    assert client.app.state.db.conn.execute("SELECT COUNT(*) FROM oidc_pending").fetchone()[0] == 0


def test_expired_state_and_expired_browser_rejected_before_http(client):
    query, _ = start(client)
    db = client.app.state.db
    with db.atomic():
        db.conn.execute("UPDATE oidc_states SET expiresAt = 1")
    count = len(client.issuer.requests)
    callback(client, query)
    assert len(client.issuer.requests) == count
    with db.atomic():
        db.conn.execute("UPDATE oidc_browsers SET expiresAt = 1")
    old = client.cookies.get(COOKIE_NAME)
    assert client.get(COOKIE_PATH + "/session").status_code == 200
    assert client.cookies.get(COOKIE_NAME) != old
    assert db.conn.execute("SELECT COUNT(*) FROM oidc_states").fetchone()[0] == 0


def test_browser_state_capacity_is_bounded_and_expiry_reclaims(client, monkeypatch):
    monkeypatch.setattr(oidc_identity, "MAX_BROWSERS", 1)
    client.get(COOKIE_PATH + "/session")
    client.cookies.clear()
    error(client.get(COOKIE_PATH + "/session"), 429, "rate_limited")
    with client.app.state.db.atomic():
        client.app.state.db.conn.execute("UPDATE oidc_browsers SET expiresAt = 1")
    assert client.get(COOKIE_PATH + "/session").status_code == 200


def test_start_real_peer_limit_does_not_trust_forwarded(client):
    headers = oidc_headers(client)
    for index in range(10):
        assert client.post(COOKIE_PATH + "/start", json={}, headers={**headers, "X-Forwarded-For": f"192.0.2.{index}"}).status_code == 200
    error(client.post(COOKIE_PATH + "/start", json={}, headers=headers), 429, "rate_limited")


def test_registration_disabled_still_allows_existing_mapping_login(client):
    first = complete(client)
    client.app.state.settings.registration_enabled = False
    assert complete(client)["user"]["id"] == first["user"]["id"]
    client.issuer.subject = "new-subject"
    client.issuer.phone = OTHER_PHONE
    headers = pending(client)
    error(client.post(COOKIE_PATH + "/complete", json=CONFIRM, headers=headers), 403, "registration_disabled")


def test_new_account_session_failure_rolls_back_account_phone_link_and_proof(client, monkeypatch):
    headers = pending(client)
    db = client.app.state.db
    before = tuple(db.conn.execute("SELECT * FROM oidc_pending").fetchone())

    def fail(*args, **kwargs):
        raise RuntimeError("synthetic-session-insert-failed")

    original = db.create_session
    monkeypatch.setattr(db, "create_session", fail)
    error(client.post(COOKIE_PATH + "/complete", json=CONFIRM, headers=headers), 500, "internal_error")
    assert db.conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0] == 0
    assert db.conn.execute("SELECT COUNT(*) FROM phone_phones").fetchone()[0] == 0
    assert db.conn.execute("SELECT COUNT(*) FROM oidc_identities").fetchone()[0] == 0
    assert tuple(db.conn.execute("SELECT * FROM oidc_pending").fetchone()) == before
    assert not db.conn.in_transaction
    monkeypatch.setattr(db, "create_session", original)
    assert client.post(COOKIE_PATH + "/complete", json=CONFIRM, headers=headers).status_code == 200


def test_migration_preserves_business_id_history_credentials_and_revokes_old_devices(client):
    old = legacy(client)
    db = client.app.state.db
    old_row = _Accounts(db).require(old.user.id)
    conversation = db.create_conversation(old.user.id, ConversationInput(title="旧历史仍在"))
    browser = remember(client, old.user.id)
    with db.atomic():
        db.conn.execute("INSERT INTO account_recovery_codes VALUES (?, ?, ?)", ("a" * 64, old.user.id, now()))
    headers = pending(client)
    response = client.post(COOKIE_PATH + "/migrate", json={**CONFIRM, "username": old.user.username, "currentPassword": PASSWORD}, headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()["user"]["id"] == old.user.id
    assert db.conversation(old.user.id, conversation.id).title == "旧历史仍在"
    new_row = _Accounts(db).require(old.user.id)
    assert new_row["passwordHash"] == old_row["passwordHash"] and new_row["passwordSalt"] == old_row["passwordSalt"]
    assert new_row["username"] == old_row["username"] and new_row["passwordEnabled"] == 1
    for token, kind in ((old.accessToken, "bearer"), (browser, "browser")):
        with pytest.raises(APIError):
            db.authenticate(token, kind=kind)
    assert db.authenticate(response.json()["accessToken"]) == old.user.id
    assert db.conn.execute("SELECT COUNT(*) FROM sessions WHERE userId = ?", (old.user.id,)).fetchone()[0] == 1
    assert db.conn.execute("SELECT COUNT(*) FROM account_recovery_codes").fetchone()[0] == 0
    assert db.conn.execute("SELECT COUNT(*) FROM oidc_pending").fetchone()[0] == 0


@pytest.mark.parametrize("username,password", [("missing_user", PASSWORD), ("legacy_user", "wrong-password-value")])
def test_migration_wrong_password_and_unknown_user_share_scrypt_and_envelope(client, monkeypatch, username, password):
    legacy(client)
    calls = []
    original = accounts._derive_password
    monkeypatch.setattr(accounts, "_derive_password", lambda value, salt: (calls.append(salt), original(value, salt))[1])
    headers = pending(client)
    error(client.post(COOKIE_PATH + "/migrate", json={**CONFIRM, "username": username, "currentPassword": password}, headers=headers), 401, "invalid_credentials")
    assert len(calls) == 1
    assert client.app.state.db.conn.execute("SELECT COUNT(*) FROM oidc_identities").fetchone()[0] == 0


@pytest.mark.parametrize("conflict", ["other-phone", "existing-link", "phone-owner", "password-disabled"])
def test_migration_conflicts_do_not_reassign_existing_data(client, conflict):
    old = legacy(client, phone=OTHER_PHONE if conflict == "other-phone" else None)
    db = client.app.state.db
    if conflict == "existing-link":
        with db.atomic():
            db.conn.execute("INSERT INTO oidc_identities VALUES (?, ?, ?, ?)", (ISSUER, "already-linked", old.user.id, now()))
    if conflict == "phone-owner":
        legacy(client, username="phone_owner", phone=PHONE)
    if conflict == "password-disabled":
        with db.atomic():
            db.conn.execute("UPDATE accounts SET passwordEnabled = 0 WHERE userId = ?", (old.user.id,))
    headers = pending(client)
    response = client.post(COOKIE_PATH + "/migrate", json={**CONFIRM, "username": old.user.username, "currentPassword": PASSWORD}, headers=headers)
    error(response, 401 if conflict == "password-disabled" else 409, "invalid_credentials" if conflict == "password-disabled" else "identity_link_required")
    assert db.authenticate(old.accessToken) == old.user.id
    assert not db.conn.in_transaction


def test_migration_partial_revoke_failure_rolls_back_link_phone_sessions_and_proof(client):
    old = legacy(client)
    db = client.app.state.db
    headers = pending(client)
    before = tuple(db.conn.execute("SELECT * FROM oidc_pending").fetchone())
    with db.atomic():
        db.conn.execute("CREATE TRIGGER fail_migration_revoke BEFORE DELETE ON sessions BEGIN SELECT RAISE(ABORT, 'synthetic-revoke-failure'); END")
    error(client.post(COOKIE_PATH + "/migrate", json={**CONFIRM, "username": old.user.username, "currentPassword": PASSWORD}, headers=headers), 500, "internal_error")
    assert tuple(db.conn.execute("SELECT * FROM oidc_pending").fetchone()) == before
    assert db.conn.execute("SELECT COUNT(*) FROM oidc_identities").fetchone()[0] == 0
    assert db.conn.execute("SELECT COUNT(*) FROM phone_phones").fetchone()[0] == 0
    assert db.authenticate(old.accessToken) == old.user.id
    assert not db.conn.in_transaction


@pytest.mark.parametrize("mutation", ["password", "revoke", "delete", "cancel", "replace"])
def test_migration_await_rechecks_credentials_revocation_and_current_pending(client, monkeypatch, mutation):
    old = legacy(client)
    headers = pending(client)
    db = client.app.state.db
    entered, release = threading.Event(), threading.Event()
    original = accounts._derive_password

    def blocking(value, salt):
        entered.set()
        assert release.wait(5)
        return original(value, salt)

    monkeypatch.setattr(accounts, "_derive_password", blocking)
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(client.post, COOKIE_PATH + "/migrate", json={**CONFIRM, "username": old.user.username, "currentPassword": PASSWORD}, headers=headers)
        assert entered.wait(5)
        with db.atomic():
            if mutation == "password":
                db.conn.execute("UPDATE accounts SET passwordHash = ? WHERE userId = ?", (b"x" * 64, old.user.id))
            elif mutation == "revoke":
                revoke_user_oidc_flows(db, old.user.id)
            elif mutation == "delete":
                db.conn.execute("DELETE FROM accounts WHERE userId = ?", (old.user.id,))
            elif mutation == "cancel":
                db.conn.execute("DELETE FROM oidc_browsers")
            else:
                db.conn.execute("UPDATE oidc_pending SET pendingHash = ?", ("b" * 64,))
        release.set()
        response = future.result(timeout=5)
    assert response.status_code == 401, response.text
    assert db.conn.execute("SELECT COUNT(*) FROM oidc_identities").fetchone()[0] == 0
    assert not db.conn.in_transaction


@pytest.mark.parametrize("phase", ["issued", "exchanging", "pending"])
def test_old_callback_and_pending_cannot_reissue_after_user_revocation(client, phase):
    first = complete(client)
    db = client.app.state.db
    headers = oidc_headers(client)
    query, _ = start(client, headers=headers)
    browser = hashed(client.cookies.get(COOKIE_NAME))
    repo = OidcIdentityRepository(db)
    if phase == "exchanging":
        client.issuer.on_token = lambda flow: _revoke(db, first["user"]["id"])
    elif phase == "pending":
        callback(client, query)
    if phase != "exchanging":
        _revoke(db, first["user"]["id"])
    if phase != "pending":
        callback(client, query)
    assert db.conn.execute("SELECT COUNT(*) FROM oidc_pending").fetchone()[0] == 0
    with pytest.raises(APIError):
        repo.pending(browser)
    client.issuer.on_token = None
    assert complete(client)["user"]["id"] == first["user"]["id"]


def _revoke(db, user):
    with db.atomic():
        revoke_user_oidc_flows(db, user)


def test_other_owner_revocation_and_fresh_flow_do_not_break_unrelated_browser(client):
    first = complete(client)
    other = legacy(client, username="unrelated_user", phone=OTHER_PHONE)
    headers = pending(client)
    _revoke(client.app.state.db, other.user.id)
    response = client.post(COOKIE_PATH + "/complete", json=CONFIRM, headers=headers)
    assert response.status_code == 200 and response.json()["user"]["id"] == first["user"]["id"]


def test_oidc_fresh_browser_erase_removes_only_local_business_account(client):
    first = complete(client)
    db = client.app.state.db
    other = legacy(client, username="unrelated_user", phone=OTHER_PHONE)
    conversation = db.create_conversation(first["user"]["id"], ConversationInput(title="remove local"))
    headers, _ = erase_pending(client, first["user"]["id"])
    response = client.post(COOKIE_PATH + "/erase", json={"confirmation": "DELETE MY ACCOUNT"}, headers=headers)
    assert response.status_code == 200, response.text
    assert response.json() == {"ok": True}
    assert db.conn.execute("SELECT COUNT(*) FROM accounts WHERE userId = ?", (first["user"]["id"],)).fetchone()[0] == 0
    assert db.conn.execute("SELECT COUNT(*) FROM oidc_identities").fetchone()[0] == 0
    assert db.conn.execute("SELECT COUNT(*) FROM oidc_user_revocations WHERE userId = ?", (first["user"]["id"],)).fetchone()[0] == 0
    assert db.conn.execute("SELECT COUNT(*) FROM conversations WHERE id = ?", (conversation.id,)).fetchone()[0] == 0
    assert db.authenticate(other.accessToken) == other.user.id
    assert db.conn.execute("PRAGMA foreign_key_check").fetchall() == []
    assert any("allira_user=" in value and "Max-Age=0" in value for value in response.headers.get_list("set-cookie"))
    # No management endpoint, provider-account DELETE, SMS or model is ever called.
    assert all(request.method in {"GET", "POST"} for request in client.issuer.requests)


@pytest.mark.parametrize("authorization", ["Bearer invalid", "Basic malformed", "", "Bearer "])
def test_erase_start_explicit_authorization_never_falls_back_to_browser(client, authorization):
    first = complete(client)
    token = remember(client, first["user"]["id"])
    headers = oidc_headers(client, **{"X-CSRF-Token": csrf_token(token), "Authorization": authorization})
    error(client.post(COOKIE_PATH + "/start", json={"purpose": "erase"}, headers=headers), 401, "browser_required")


def test_erase_start_requires_remembered_device_and_ordinary_csrf(client):
    first = complete(client)
    headers = oidc_headers(client)
    error(client.post(COOKIE_PATH + "/start", json={"purpose": "erase"}, headers=headers), 401, "unauthorized")
    token = remember(client, first["user"]["id"])
    error(client.post(COOKIE_PATH + "/start", json={"purpose": "erase"}, headers=headers), 403, "csrf_forbidden")
    headers["X-CSRF-Token"] = csrf_token(token)
    assert client.post(COOKIE_PATH + "/start", json={"purpose": "erase"}, headers=headers).status_code == 200


@pytest.mark.parametrize("auth_time", [None, False, "fresh", 1, 99999999999])
def test_erasure_requires_real_fresh_auth_time_claim(client, auth_time):
    first = complete(client)
    token = remember(client, first["user"]["id"])
    client.issuer.claims["auth_time"] = auth_time
    headers = oidc_headers(client, **{"X-CSRF-Token": csrf_token(token)})
    query, _ = start(client, purpose="erase", headers=headers)
    callback(client, query)
    assert client.app.state.db.conn.execute("SELECT COUNT(*) FROM oidc_pending").fetchone()[0] == 0


@pytest.mark.parametrize("mutation", ["other-device", "logout", "different-subject", "login-proof", "stale-time"])
def test_erase_exact_session_purpose_identity_and_freshness(client, mutation):
    first = complete(client)
    db = client.app.state.db
    headers, token = erase_pending(client, first["user"]["id"])
    if mutation == "other-device":
        token = remember(client, first["user"]["id"])
        headers["X-CSRF-Token"] = csrf_token(token)
    elif mutation == "logout":
        db.revoke_session(token)
    else:
        with db.atomic():
            if mutation == "different-subject":
                db.conn.execute("UPDATE oidc_pending SET subject = 'different-subject'")
            elif mutation == "login-proof":
                db.conn.execute("UPDATE oidc_pending SET purpose = 'login'")
            else:
                db.conn.execute("UPDATE oidc_pending SET authTime = 1")
    response = client.post(COOKIE_PATH + "/erase", json={"confirmation": "DELETE MY ACCOUNT"}, headers=headers)
    assert response.status_code == 401, response.text
    assert _Accounts(db).require(first["user"]["id"])


def test_erasure_busy_or_forced_failure_rolls_back_pending_proof_and_sessions(client):
    first = complete(client)
    db = client.app.state.db
    conversation = db.create_conversation(first["user"]["id"], ConversationInput(title="busy"))
    from allira.schemas import RunInput

    run, _ = db.create_run(first["user"]["id"], conversation.id, RunInput(clientMessageId="ec35bb4c-edee-4c54-96ea-09150d8306c3", content="synthetic"), 1000)
    headers, token = erase_pending(client, first["user"]["id"])
    before = tuple(db.conn.execute("SELECT * FROM oidc_pending").fetchone())
    response = client.post(COOKIE_PATH + "/erase", json={"confirmation": "DELETE MY ACCOUNT"}, headers=headers)
    error(response, 409, "account_busy")
    assert tuple(db.conn.execute("SELECT * FROM oidc_pending").fetchone()) == before
    db.finish(run.id, "cancelled")
    with db.atomic():
        db.conn.execute("CREATE TRIGGER fail_oidc_erase BEFORE DELETE ON accounts BEGIN SELECT RAISE(ABORT, 'synthetic-erase-failure'); END")
    error(client.post(COOKIE_PATH + "/erase", json={"confirmation": "DELETE MY ACCOUNT"}, headers=headers), 500, "internal_error")
    assert tuple(db.conn.execute("SELECT * FROM oidc_pending").fetchone()) == before
    assert db.authenticate(token, kind="browser") == first["user"]["id"]
    assert not db.conn.in_transaction


def test_login_and_erase_proofs_not_interchangeable_and_wrong_confirmation(client):
    first = complete(client)
    headers, _ = erase_pending(client, first["user"]["id"])
    error(client.post(COOKIE_PATH + "/complete", json=CONFIRM, headers=headers), 401, "oidc_pending_required")
    error(client.post(COOKIE_PATH + "/migrate", json={**CONFIRM, "username": "legacy_user", "currentPassword": PASSWORD}, headers=headers), 401, "oidc_pending_required")
    error(client.post(COOKIE_PATH + "/erase", json={"confirmation": "erase"}, headers=headers), 422, "validation_error")


def test_additive_schema_restart_persistence_clear_transients_and_fk(tmp_path, rsa_key):
    path = str(tmp_path / "oidc-isolated.sqlite3")
    issuer = FakeIssuer(rsa_key)
    settings = settings_for(path)
    with TestClient(make_app(issuer, settings=settings), base_url=ORIGIN, client=("127.0.0.1", 1)) as client:
        client.issuer = issuer
        first = complete(client)
        db = client.app.state.db
        with db.atomic():
            db.conn.execute("CREATE TABLE unrelated_probe (value TEXT)")
            db.conn.execute("INSERT INTO unrelated_probe VALUES ('preserved')")
        pending(client)
        initialize_oidc_identity(db)
        clear_transient_oidc_state(db)
        assert db.conn.execute("SELECT COUNT(*) FROM oidc_pending").fetchone()[0] == 0
        assert db.authenticate(first["accessToken"]) == first["user"]["id"]
    db = Database(path, settings)
    initialize_oidc_identity(db)
    assert db.conn.execute("SELECT value FROM unrelated_probe").fetchone()[0] == "preserved"
    assert db.conn.execute("SELECT userId FROM oidc_identities").fetchone()[0] == first["user"]["id"]
    with db.atomic():
        db.conn.execute("DELETE FROM accounts WHERE userId = ?", (first["user"]["id"],))
    assert db.conn.execute("SELECT COUNT(*) FROM oidc_identities").fetchone()[0] == 0
    assert db.conn.execute("PRAGMA foreign_key_check").fetchall() == []
    db.close()


def test_revocation_requires_outer_transaction_noop_dev_and_overflow_rollback(client):
    db = client.app.state.db
    with pytest.raises(RuntimeError, match="outer account transaction"):
        revoke_user_oidc_flows(db, "developer")
    with db.atomic():
        revoke_user_oidc_flows(db, "developer")
    old = legacy(client)
    with db.atomic():
        db.conn.execute("UPDATE oidc_revocation_clock SET revision = 9223372036854775807")
    with pytest.raises(APIError) as caught, db.atomic():
        revoke_user_oidc_flows(db, old.user.id)
    assert caught.value.code == "oidc_unavailable"
    assert db.conn.execute("SELECT COUNT(*) FROM oidc_user_revocations").fetchone()[0] == 0
    assert not db.conn.in_transaction


def test_legacy_backend_fails_closed_without_provider_calls(rsa_key):
    issuer = FakeIssuer(rsa_key)
    with TestClient(make_app(issuer, settings=settings_for(auth_backend="legacy")), base_url=ORIGIN,
                    client=("127.0.0.1", 1)) as client:
        error(client.get(COOKIE_PATH + "/session"), 403, "oidc_disabled")
        assert not issuer.requests


def test_dtos_openapi_hide_secrets_and_expose_frozen_wire(client):
    schema = client.app.openapi()
    assert schema["components"]["schemas"]["OidcPending"]["properties"]["purpose"]["enum"] == ["login", "erase", "phone_migration"]
    assert schema["components"]["schemas"]["OidcMigrate"]["properties"]["currentPassword"]["writeOnly"] is True
    assert COOKIE_PATH + "/erase" in schema["paths"]
    assert COOKIE_PATH + "/callback" in schema["paths"]
    confirmation = schema["components"]["schemas"]["OidcComplete"]["properties"]["confirmPhoneMigration"]
    assert confirmation["type"] == "boolean" and confirmation["default"] is False


@pytest.mark.parametrize("source,phone", [
    ("id-token", "8613800138000"), ("userinfo", "8613800138000"),
    ("matching-userinfo", PHONE), ("matching-id-token", "8613800138000"),
])
def test_logto_verified_country_code_digits_normalized_only_at_claim_boundary(client, source, phone):
    client.issuer.phone = phone
    if source == "userinfo":
        client.issuer.drop_claims.update({"phone_number", "phone_number_verified"})
    elif source.startswith("matching"):
        # Force standard userinfo fallback while retaining a signed ID-token
        # phone, and compare the canonical form on both sides.
        client.issuer.drop_claims.add("phone_number_verified")
        client.issuer.userinfo = {
            "sub": client.issuer.subject, "phone_number_verified": True,
            "phone_number": "8613800138000" if source == "matching-userinfo" else PHONE,
        }
    result = complete(client)
    row = client.app.state.db.conn.execute("SELECT * FROM phone_phones").fetchone()
    assert row["phone"] == PHONE and row["userId"] == result["user"]["id"]
    with pytest.raises(ValueError, match="mainland China"):
        normalize_phone("8613800138000")


@pytest.mark.parametrize("phone", [
    "8612800138000", "008613800138000", "86138001380001", "861380013800",
    "+85213800138000", "８６13800138000", "8613800138000 ", 8613800138000, None,
])
def test_country_code_claim_compatibility_does_not_expand_phone_validation(client, phone):
    client.issuer.phone = phone
    query, _ = start(client)
    callback(client, query)
    assert client.app.state.db.conn.execute("SELECT COUNT(*) FROM oidc_pending").fetchone()[0] == 0


def test_userinfo_and_id_token_conflicting_country_code_phone_rejected(client):
    client.issuer.phone = "8613800138000"
    client.issuer.drop_claims.add("phone_number_verified")
    client.issuer.userinfo = {
        "sub": client.issuer.subject, "phone_number_verified": True,
        "phone_number": OTHER_PHONE,
    }
    query, _ = start(client)
    callback(client, query)
    assert client.app.state.db.conn.execute("SELECT COUNT(*) FROM oidc_pending").fetchone()[0] == 0


@pytest.mark.parametrize("registration_enabled", [True, False])
def test_explicit_fresh_phone_migration_preserves_passwordless_owner_and_revokes_old_devices(client, monkeypatch, registration_enabled):
    row = phone_only(client)
    db = client.app.state.db
    user = row["userId"]
    browser = remember(client, user)
    conversation = db.create_conversation(user, ConversationInput(title="旧手机历史必须保留"))
    with db.atomic():
        db.conn.execute("INSERT INTO account_recovery_codes VALUES (?, ?, ?)", ("c" * 64, user, now()))
    client.app.state.settings.registration_enabled = registration_enabled
    monkeypatch.setattr(accounts, "_derive_password", lambda *_: pytest.fail("Passwordless migration must not invent a password proof."))
    headers = oidc_headers(client)
    query, _ = start(client, purpose="phone_migration", headers=headers)
    assert query["prompt"] == ["login"] and query["max_age"] == ["0"]
    assert db.conn.execute("SELECT purpose FROM oidc_states").fetchone()[0] == "phone_migration"
    callback(client, query)
    assert client.get(COOKIE_PATH + "/session").json()["pending"]["purpose"] == "phone_migration"
    response = client.post(COOKIE_PATH + "/complete", json={**CONFIRM, "confirmPhoneMigration": True}, headers=headers)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["user"]["id"] == user and result["user"]["username"] == row["username"]
    assert tuple(_Accounts(db).require(user)) == tuple(row)
    assert db.conversation(user, conversation.id).title == "旧手机历史必须保留"
    assert db.conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0] == 1
    for token, kind in (("synthetic-old-phone-bearer", "bearer"), (browser, "browser")):
        with pytest.raises(APIError):
            db.authenticate(token, kind=kind)
    assert db.authenticate(result["accessToken"]) == user
    assert db.conn.execute("SELECT COUNT(*) FROM sessions WHERE userId = ?", (user,)).fetchone()[0] == 1
    assert db.conn.execute("SELECT COUNT(*) FROM account_recovery_codes").fetchone()[0] == 0
    assert db.conn.execute("SELECT COUNT(*) FROM oidc_browsers").fetchone()[0] == 0
    assert db.conn.execute("SELECT revokedRevision FROM oidc_user_revocations WHERE userId = ?", (user,)).fetchone()[0] > 0
    assert db.conn.execute("SELECT revokedRevision FROM social_user_revocations WHERE userId = ?", (user,)).fetchone()[0] > 0
    error(client.post(COOKIE_PATH + "/complete", json={**CONFIRM, "confirmPhoneMigration": True}, headers=headers), 401, "oidc_session_required")
    assert db.conn.execute("PRAGMA foreign_key_check").fetchall() == [] and not db.conn.in_transaction


@pytest.mark.parametrize("value", ["true", 1, None])
def test_phone_migration_confirmation_is_strict_bool(client, value):
    phone_only(client)
    headers = pending(client, purpose="phone_migration")
    error(client.post(COOKIE_PATH + "/complete", json={**CONFIRM, "confirmPhoneMigration": value}, headers=headers), 422, "validation_error")
    assert client.app.state.db.conn.execute("SELECT COUNT(*) FROM oidc_identities").fetchone()[0] == 0


@pytest.mark.parametrize("purpose,confirmation", [("login", True), ("phone_migration", False)])
def test_phone_migration_cannot_be_implicitly_enabled_or_upgrade_regular_login(client, purpose, confirmation):
    phone_only(client)
    headers = pending(client, purpose=purpose)
    body = {**CONFIRM, **({"confirmPhoneMigration": True} if confirmation else {})}
    error(client.post(COOKIE_PATH + "/complete", json=body, headers=headers), 401, "oidc_pending_required")
    db = client.app.state.db
    assert db.conn.execute("SELECT COUNT(*) FROM oidc_identities").fetchone()[0] == 0
    assert db.conn.execute("SELECT COUNT(*) FROM oidc_pending").fetchone()[0] == 1


@pytest.mark.parametrize("purpose,flag", [("phone_migration", False), ("phone_migration", True), ("login", True)])
def test_legacy_password_migrate_rejects_phone_migration_purpose_or_confirmation(client, monkeypatch, purpose, flag):
    legacy(client, phone=PHONE)
    headers = pending(client, purpose=purpose)
    monkeypatch.setattr(accounts, "_derive_password", lambda *_: pytest.fail("Invalid migration purpose must not reach KDF."))
    body = {**CONFIRM, "username": "legacy_user", "currentPassword": PASSWORD, "confirmPhoneMigration": flag}
    error(client.post(COOKIE_PATH + "/migrate", json=body, headers=headers), 401, "oidc_pending_required")
    assert client.app.state.db.conn.execute("SELECT COUNT(*) FROM oidc_identities").fetchone()[0] == 0


@pytest.mark.parametrize("conflict", ["no-owner", "password-enabled", "owner-linked", "subject-linked", "owner-deleted", "owner-phone-changed", "flag-changed", "link-after-callback"])
def test_phone_migration_final_owner_link_and_password_checks_do_not_merge_or_create(client, conflict):
    db = client.app.state.db
    row = None
    if conflict == "password-enabled":
        row = _Accounts(db).require(legacy(client, phone=PHONE).user.id)
    elif conflict != "no-owner":
        row = phone_only(client)
    with db.atomic():
        if conflict == "owner-linked":
            db.conn.execute("INSERT INTO oidc_identities VALUES (?, ?, ?, ?)", ("https://different-issuer.example/oidc", "other-subject", row["userId"], now()))
        elif conflict == "subject-linked":
            other = legacy(client, username="other_owner", phone=OTHER_PHONE)
            db.conn.execute("INSERT INTO oidc_identities VALUES (?, ?, ?, ?)", (ISSUER, client.issuer.subject, other.user.id, now()))
    headers = pending(client, purpose="phone_migration")
    with db.atomic():
        if conflict == "owner-deleted":
            db.conn.execute("DELETE FROM accounts WHERE userId = ?", (row["userId"],))
        elif conflict == "owner-phone-changed":
            db.conn.execute("UPDATE phone_phones SET phone = ? WHERE userId = ?", (OTHER_PHONE, row["userId"]))
        elif conflict == "flag-changed":
            db.conn.execute("UPDATE accounts SET passwordEnabled = 1 WHERE userId = ?", (row["userId"],))
        elif conflict == "link-after-callback":
            db.conn.execute("INSERT INTO oidc_identities VALUES (?, ?, ?, ?)", (ISSUER, "new-linked-subject", row["userId"], now()))
    before_accounts = [tuple(item) for item in db.conn.execute("SELECT * FROM accounts ORDER BY userId")]
    before_links = [tuple(item) for item in db.conn.execute("SELECT * FROM oidc_identities ORDER BY userId")]
    error(client.post(COOKIE_PATH + "/complete", json={**CONFIRM, "confirmPhoneMigration": True}, headers=headers), 409, "identity_link_required")
    assert [tuple(item) for item in db.conn.execute("SELECT * FROM accounts ORDER BY userId")] == before_accounts
    assert [tuple(item) for item in db.conn.execute("SELECT * FROM oidc_identities ORDER BY userId")] == before_links
    assert not db.conn.in_transaction


@pytest.mark.parametrize("age", [None, False, True, "fresh", 1, -120, 99999999999])
def test_phone_migration_callback_requires_explicit_fresh_numeric_auth_time(client, age):
    phone_only(client)
    client.issuer.claims["auth_time"] = int(time()) + age if age == -120 else age
    query, _ = start(client, purpose="phone_migration")
    callback(client, query)
    db = client.app.state.db
    assert db.conn.execute("SELECT COUNT(*) FROM oidc_pending").fetchone()[0] == 0
    assert db.conn.execute("SELECT COUNT(*) FROM oidc_identities").fetchone()[0] == 0


def test_phone_migration_callback_requires_auth_time_claim_not_userinfo_fallback(client):
    phone_only(client)
    client.issuer.drop_claims.add("auth_time")
    query, _ = start(client, purpose="phone_migration")
    callback(client, query)
    assert client.app.state.db.conn.execute("SELECT COUNT(*) FROM oidc_pending").fetchone()[0] == 0


@pytest.mark.parametrize("value", [float("inf"), float("-inf"), float("nan"), False, None, "1"])
def test_fresh_time_helper_does_not_coerce_nonfinite_or_boolean(value):
    assert oidc_identity._fresh_auth(value, time()) is False


def test_phone_migration_complete_rechecks_freshness_after_pending_delay(client, monkeypatch):
    phone_only(client)
    headers = pending(client, purpose="phone_migration")
    current = time()
    monkeypatch.setattr(oidc_identity, "time", lambda: current + 301)
    error(client.post(COOKIE_PATH + "/complete", json={**CONFIRM, "confirmPhoneMigration": True}, headers=headers), 401, "oidc_pending_required")
    assert client.app.state.db.conn.execute("SELECT COUNT(*) FROM oidc_pending").fetchone()[0] == 1
    assert client.app.state.db.conn.execute("SELECT COUNT(*) FROM oidc_identities").fetchone()[0] == 0


@pytest.mark.parametrize("phase", ["issued", "exchanging", "pending"])
def test_old_phone_migration_proof_cannot_link_after_owner_credential_revocation(client, phase):
    row = phone_only(client)
    db = client.app.state.db
    query, headers = start(client, purpose="phone_migration")
    if phase == "exchanging":
        client.issuer.on_token = lambda flow: _revoke(db, row["userId"])
    elif phase == "pending":
        callback(client, query)
    if phase != "exchanging":
        _revoke(db, row["userId"])
    if phase != "pending":
        callback(client, query)
    error(client.post(COOKIE_PATH + "/complete", json={**CONFIRM, "confirmPhoneMigration": True}, headers=headers), 401, "oidc_pending_required")
    assert db.conn.execute("SELECT COUNT(*) FROM oidc_identities").fetchone()[0] == 0
    client.issuer.on_token = None
    headers = pending(client, purpose="phone_migration")
    assert client.post(COOKIE_PATH + "/complete", json={**CONFIRM, "confirmPhoneMigration": True}, headers=headers).status_code == 200


@pytest.mark.parametrize("failure", ["consume", "link", "revocation", "recovery", "sessions", "new-session"])
def test_phone_migration_every_mutation_failure_rolls_back_credentials_owner_and_proof(client, failure):
    row = phone_only(client)
    db = client.app.state.db
    with db.atomic():
        db.conn.execute("INSERT INTO account_recovery_codes VALUES (?, ?, ?)", ("d" * 64, row["userId"], now()))
    headers = pending(client, purpose="phone_migration")
    target = {
        "consume": "BEFORE DELETE ON oidc_browsers", "link": "AFTER INSERT ON oidc_identities",
        "revocation": "BEFORE UPDATE ON oidc_revocation_clock",
        "recovery": "BEFORE DELETE ON account_recovery_codes", "sessions": "BEFORE DELETE ON sessions",
        "new-session": "AFTER INSERT ON sessions",
    }[failure]
    with db.atomic():
        db.conn.execute("CREATE TRIGGER fail_phone_migration " + target + " BEGIN SELECT RAISE(ABORT, 'synthetic-atomic-failure'); END")
    tables = ("users", "accounts", "phone_phones", "sessions", "account_recovery_codes",
              "oidc_browsers", "oidc_states", "oidc_pending", "oidc_identities", "oidc_revocation_clock",
              "oidc_user_revocations", "social_revocation_clock", "social_user_revocations")

    def snapshot():
        return {table: [tuple(item) for item in db.conn.execute("SELECT * FROM " + table + " ORDER BY rowid")] for table in tables}

    before = snapshot()
    error(client.post(COOKIE_PATH + "/complete", json={**CONFIRM, "confirmPhoneMigration": True}, headers=headers), 500, "internal_error")
    assert snapshot() == before and not db.conn.in_transaction
    with db.atomic():
        db.conn.execute("DROP TRIGGER fail_phone_migration")
    response = client.post(COOKIE_PATH + "/complete", json={**CONFIRM, "confirmPhoneMigration": True}, headers=headers)
    assert response.status_code == 200 and response.json()["user"]["id"] == row["userId"]


def test_schema7_legacy_purpose_upgrade_replaces_only_transient_tables(client):
    first = complete(client)
    db = client.app.state.db
    pending(client)
    transient = ("oidc_browsers", "oidc_states", "oidc_pending")
    table_sql = {table: db.conn.execute("SELECT sql FROM sqlite_master WHERE name = ?", (table,)).fetchone()[0] for table in transient}
    old_rows = {table: [tuple(item) for item in db.conn.execute("SELECT * FROM " + table)] for table in transient}
    with db.atomic():
        db.conn.execute("CREATE TABLE unrelated_probe (value TEXT)")
        db.conn.execute("INSERT INTO unrelated_probe VALUES ('must-not-change')")
        db.conn.execute("INSERT INTO oidc_user_revocations VALUES (?, 7)", (first["user"]["id"],))
        db.conn.execute("UPDATE oidc_revocation_clock SET revision = 9")
        db.conn.execute("PRAGMA user_version = 7")
        for table in reversed(transient):
            db.conn.execute("DROP TABLE " + table)
        for table in transient:
            db.conn.execute(table_sql[table].replace("'login', 'erase', 'phone_migration'", "'login', 'erase'"))
            for row in old_rows[table]:
                db.conn.execute("INSERT INTO " + table + " VALUES (" + ",".join("?" for _ in row) + ")", row)
    persistent = ("accounts", "users", "phone_phones", "sessions", "oidc_identities",
                  "oidc_revocation_clock", "oidc_user_revocations", "unrelated_probe")
    before = {table: [tuple(item) for item in db.conn.execute("SELECT * FROM " + table + " ORDER BY rowid")] for table in persistent}
    initialize_oidc_identity(db)
    for table in transient:
        assert db.conn.execute("SELECT COUNT(*) FROM " + table).fetchone()[0] == 0
    assert {table: [tuple(item) for item in db.conn.execute("SELECT * FROM " + table + " ORDER BY rowid")] for table in persistent} == before
    assert db.conn.execute("PRAGMA user_version").fetchone()[0] == 7
    assert db.authenticate(first["accessToken"]) == first["user"]["id"]
    headers = pending(client, purpose="phone_migration")
    proof = tuple(db.conn.execute("SELECT * FROM oidc_pending").fetchone())
    initialize_oidc_identity(db)
    assert tuple(db.conn.execute("SELECT * FROM oidc_pending").fetchone()) == proof
    error(client.post(COOKIE_PATH + "/complete", json={**CONFIRM, "confirmPhoneMigration": True}, headers=headers), 409, "identity_link_required")
    assert db.conn.execute("PRAGMA foreign_key_check").fetchall() == [] and not db.conn.in_transaction


def test_operator_disabled_oidc_gate_is_preserved_without_provider_calls(rsa_key):
    issuer = FakeIssuer(rsa_key)
    with TestClient(make_app(issuer, settings=settings_for(oidc_enabled=False)), base_url=ORIGIN,
                    client=("127.0.0.1", 1)) as client:
        error(client.get(COOKIE_PATH + "/session"), 503, "oidc_unavailable")
        assert not issuer.requests


class ChunkedIdentityStream(httpx.AsyncByteStream):
    def __init__(self, body):
        self.body, self.closed, self.chunks = body, False, 0

    async def __aiter__(self):
        for offset in range(0, len(self.body), 17):
            self.chunks += 1
            yield self.body[offset:offset + 17]

    async def aclose(self):
        self.closed = True


class StreamingIssuer(FakeIssuer):
    """Actual unconsumed AsyncByteStream shapes, never a live issuer/network."""

    def __init__(self, key):
        super().__init__(key)
        self.algorithm = "ES384"
        self.metadata_changes["id_token_signing_alg_values_supported"] = ["ES384"]
        self.drop_claims = {"phone_number", "phone_number_verified"}
        self.userinfo = {"sub": self.subject, "phone_number": PHONE[1:], "phone_number_verified": True}
        self.streams = []

    def streaming(self, response):
        if not response.is_stream_consumed:
            assert isinstance(response.stream, ChunkedIdentityStream)
            self.streams.append(response.stream)
            return response
        stream = ChunkedIdentityStream(response.content)
        headers = dict(response.headers)
        headers.pop("content-length", None)
        headers["transfer-encoding"] = "chunked"
        result = httpx.Response(response.status_code, headers=headers, stream=stream)
        assert not result.is_stream_consumed
        self.streams.append(stream)
        return result


@pytest.fixture
def streaming_client():
    key = jwk.ECKey.generate_key("P-384", parameters={"kid": "synthetic-rsa", "alg": "ES384", "use": "sig"})
    issuer = StreamingIssuer(key)
    with TestClient(make_app(issuer), base_url=ORIGIN, client=("127.0.0.1", 1234),
                    raise_server_exceptions=False) as client:
        client.issuer = issuer
        yield client


def diagnostic_records(caplog):
    return [record for record in caplog.records if record.name == "allira.oidc_identity"]


def test_real_chunked_es384_no_id_phone_userinfo_chain(streaming_client, caplog):
    client = streaming_client
    caplog.set_level(logging.WARNING, logger="allira.oidc_identity")
    query, headers = start(client)
    assert query["code_challenge_method"] == ["S256"]
    response = callback(client, query)
    assert response.status_code == 303 and response.headers["location"] == "/test/"
    assert client.get(COOKIE_PATH + "/session").json()["pending"] is not None
    assert [request.url.path for request in client.issuer.requests] == [
        "/oidc/.well-known/openid-configuration", "/oidc/.well-known/openid-configuration",
        "/oidc/token", "/oidc/jwks", "/oidc/me",
    ]
    assert all(request.headers["accept-encoding"] == "identity" for request in client.issuer.requests)
    assert client.issuer.requests[-1].headers["authorization"] == "Bearer " + ACCESS
    assert all(stream.closed and stream.chunks > 1 for stream in client.issuer.streams)
    assert client.app.state.db.conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0
    result = client.post(COOKIE_PATH + "/complete", json=CONFIRM, headers=headers)
    assert result.status_code == 200 and result.json()["user"]["id"].startswith("acct_")
    assert PhoneIdentityRepository(client.app.state.db).identity(result.json()["user"]["id"]).phone == PHONE
    assert diagnostic_records(caplog) == []


@pytest.mark.parametrize("change,stage,category", [
    ("at_hash", "exchange.claims", "VALUE"),
    ("nonce", "exchange.claims", "JOSE"),
    ("sub", "exchange.userinfo", "VALUE"),
    ("unverified", "exchange.userinfo", "VALUE"),
    ("wrong_phone", "exchange.identity", "VALUE"),
    ("explicit_false", "exchange.claims", "VALUE"),
    ("redirect", "exchange.userinfo", "VALUE"),
    ("encoding", "exchange.userinfo", "VALUE"),
    ("oversize", "exchange.userinfo", "VALUE"),
    ("duplicate", "exchange.userinfo", "VALUE"),
])
def test_streaming_exchange_keeps_all_rejection_rules(streaming_client, caplog, change, stage, category):
    client = streaming_client
    query, _ = start(client)
    if change == "at_hash":
        client.issuer.claims["at_hash"] = "invalid-provider-at-hash"
    elif change == "nonce":
        client.issuer.claims["nonce"] = "wrong-secret-nonce"
    elif change == "sub":
        client.issuer.userinfo["sub"] = "wrong-secret-subject"
    elif change == "unverified":
        client.issuer.userinfo["phone_number_verified"] = False
    elif change == "wrong_phone":
        client.issuer.userinfo["phone_number"] = "+14155552671"
    elif change == "explicit_false":
        client.issuer.drop_claims.remove("phone_number_verified")
        client.issuer.claims["phone_number_verified"] = False
    else:
        responses = {
            "redirect": lambda: httpx.Response(302, headers={"location": "https://secret.invalid/secret"}, json={}),
            "encoding": lambda: httpx.Response(200, headers={"content-encoding": "gzip"}, stream=ChunkedIdentityStream(b"not-compressed-secret")),
            "oversize": lambda: httpx.Response(200, content=b"x" * (oidc_identity.MAX_HTTP_BYTES + 1)),
            "duplicate": lambda: httpx.Response(200, content=b'{"sub":"secret","sub":"duplicate-secret"}'),
        }
        client.issuer.response_override = lambda request: responses[change]() if request.url.path == "/oidc/me" else None
    caplog.set_level(logging.WARNING, logger="allira.oidc_identity")
    response = callback(client, query)
    assert response.status_code == 303 and response.headers["location"] == "/test/"
    assert response.headers["cache-control"] == "no-store"
    db = client.app.state.db
    for table in ("oidc_states", "oidc_pending", "oidc_identities", "sessions"):
        assert db.conn.execute("SELECT COUNT(*) FROM " + table).fetchone()[0] == 0
    assert all(stream.closed for stream in client.issuer.streams)
    records = diagnostic_records(caplog)
    assert len(records) == 1
    assert (records[0].stage, records[0].category) == (stage, category)
    assert ACCESS not in caplog.text and "wrong-secret" not in caplog.text
    assert records[0].exc_info is None and records[0].stack_info is None


@pytest.mark.parametrize("stage", [
    "callback.context", "callback.browser", "callback.state", "callback.response",
    "exchange.discovery", "exchange.token", "exchange.jwks", "exchange.signature",
    "exchange.claims", "exchange.userinfo", "exchange.identity", "callback.finish",
])
def test_callback_diagnostics_report_only_static_stage_and_category(client, caplog, monkeypatch, stage):
    secret = "PRIVATE-EXCEPTION-URL-TOKEN-SUBJECT-PHONE-DO-NOT-LOG"
    query, _ = start(client)

    def fail(*args, **kwargs):
        raise ValueError(secret)

    if stage == "callback.context":
        monkeypatch.setattr(oidc_identity, "_context", fail)
    elif stage == "callback.browser":
        monkeypatch.setattr(oidc_identity, "_browser", fail)
    elif stage == "callback.state":
        monkeypatch.setattr(OidcIdentityRepository, "exchanging", fail)
    elif stage == "callback.response":
        query["code"] = [secret + "\n"]
    elif stage == "callback.finish":
        monkeypatch.setattr(OidcIdentityRepository, "finish", fail)
    elif stage in {"exchange.discovery", "exchange.token", "exchange.jwks", "exchange.userinfo"}:
        if stage == "exchange.userinfo":
            client.issuer.drop_claims.update({"phone_number", "phone_number_verified"})
        path = {
            "exchange.discovery": "/oidc/.well-known/openid-configuration",
            "exchange.token": "/oidc/token", "exchange.jwks": "/oidc/jwks", "exchange.userinfo": "/oidc/me",
        }[stage]
        client.issuer.response_override = lambda request: fail() if request.url.path == path else None
    elif stage == "exchange.signature":
        monkeypatch.setattr(oidc_identity.jwt, "decode", fail)
    elif stage == "exchange.claims":
        monkeypatch.setattr(oidc_identity.CodeIDToken, "validate", fail)
    else:
        monkeypatch.setattr(oidc_identity, "_claim_phone", fail)
    caplog.set_level(logging.WARNING, logger="allira.oidc_identity")
    response = client.get(COOKIE_PATH + "/callback", params={"state": query["state"][0], "code": query.get("code", ["synthetic-code"])[0]}, follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"] == "/test/"
    records = diagnostic_records(caplog)
    assert len(records) == 1
    record = records[0]
    category = "API" if stage == "callback.response" else "VALUE"
    assert (record.event, record.stage, record.category) == ("oidc_callback_failed", stage, category)
    assert record.getMessage() == f"oidc_callback_failed stage={stage} category={category}"
    assert record.args == (stage, category) and record.exc_info is None and record.stack_info is None
    assert secret not in caplog.text and ACCESS not in caplog.text and CLIENT_SECRET not in caplog.text
    assert secret not in json.dumps(record.__dict__, default=str)
    assert not client.app.state.db.conn.in_transaction


@pytest.mark.parametrize("kind,category", [
    ("api", "API"), ("http", "HTTP"), ("authlib", "AUTHLIB"), ("jose", "JOSE"),
    ("value", "VALUE"), ("type", "VALUE"), ("timeout", "TIMEOUT"), ("runtime", "OTHER"),
])
def test_callback_diagnostics_never_format_exception_or_dynamic_class(client, caplog, monkeypatch, kind, category):
    secret = "PRIVATE-CUSTOM-EXCEPTION-CLASS-MESSAGE-NO-LOG"
    bases = {
        "api": APIError, "http": httpx.ConnectError, "authlib": AuthlibBaseError,
        "jose": JoseError, "value": ValueError, "type": TypeError,
        "timeout": TimeoutError, "runtime": RuntimeError,
    }
    error_type = type(secret, (bases[kind],), {"__str__": lambda self: (_ for _ in ()).throw(AssertionError("Exception formatted"))})
    if kind == "api":
        base = error_type(401, secret, secret)
    elif kind == "authlib":
        base = error_type(error=secret, description=secret, uri=secret)
    else:
        base = error_type(secret)
    query, _ = start(client)

    def fail(*args, **kwargs):
        raise base

    monkeypatch.setattr(oidc_identity.CodeIDToken, "validate", fail)
    caplog.set_level(logging.WARNING, logger="allira.oidc_identity")
    response = callback(client, query)
    assert response.status_code == 303
    record, = diagnostic_records(caplog)
    assert (record.stage, record.category) == ("exchange.claims", category)
    assert record.args == ("exchange.claims", category) and record.exc_info is None and record.stack_info is None
    assert secret not in caplog.text and secret not in json.dumps(record.__dict__, default=str)


def test_diagnostic_allowlist_rejects_dynamic_stage_without_logging_exception(caplog):
    secret = "PRIVATE-DYNAMIC-STAGE-DO-NOT-LOG"
    caplog.set_level(logging.WARNING, logger="allira.oidc_identity")
    oidc_identity._callback_failure(oidc_identity._CallbackDiagnostic(stage=secret), ValueError(secret))
    record, = diagnostic_records(caplog)
    assert (record.stage, record.category) == ("unknown", "VALUE")
    assert record.args == ("unknown", "VALUE") and record.exc_info is None and secret not in caplog.text
