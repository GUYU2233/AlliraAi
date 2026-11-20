"""Browser-bound OAuth/pending-phone flows: ASGI, fake SMS and simulated OAuth only."""

import asyncio
import hashlib
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Annotated
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx
import pytest
from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.testclient import TestClient
from pydantic import SecretStr

from allira import accounts, phone_identity
from allira.accounts import AccountRegister, _Accounts, accounts_router
from allira.browser_sessions import authenticate_browser, browser_sessions_router
from allira.config import Settings
from allira.db import APIError, Database
from allira.phone_identity import PhoneIdentityRepository, initialize_phone_identity
from allira.social_identity import (
    COOKIE_NAME,
    COOKIE_PATH,
    SocialIdentityRepository,
    initialize_social_identity,
    revoke_user_social_flows,
    social_csrf_token,
    social_identity_router,
)
from allira.social_providers import TestSocialProvider, VerifiedSocialIdentity

ORIGIN = "http://127.0.0.1:8000"
PREFIX = "/api/v1/auth/social"
PHONE = "13800138000"
OTHER_PHONE = "13900139000"
PASSWORD = "social-optional-password-123"
HMAC_KEY = "isolated-social-hmac-key-" + "s" * 40


class FakeSender:
    def __init__(self):
        self.sent = []
        self.before_send = None

    async def send(self, phone, code, purpose):
        if self.before_send is not None:
            self.before_send()
        self.sent.append((phone, code, purpose))


def settings_for(**overrides):
    return SimpleNamespace(**{
        **Settings(_env_file=None, provider="mock", database_path=":memory:").model_dump(),
        "social_mode": "test", "sms_provider": "test-file", "phone_identity_hmac_key": HMAC_KEY,
        "wechat_redirect_uri": ORIGIN + PREFIX + "/wechat/callback",
        "qq_redirect_uri": ORIGIN + PREFIX + "/qq/callback", "session_ttl_seconds": 300,
        "registration_enabled": True, **overrides,
    })


def make_app(settings=None):
    settings = settings or settings_for()

    @asynccontextmanager
    async def lifespan(app):
        db = Database(":memory:", settings)
        initialize_phone_identity(db)
        initialize_social_identity(db)
        app.state.db, app.state.settings, app.state.sms_sender = db, settings, FakeSender()
        app.state.social_providers = {
            name: TestSocialProvider(name, getattr(settings, name + "_redirect_uri")) for name in ("wechat", "qq")
        }
        try:
            yield
        finally:
            db.close()

    app = FastAPI(lifespan=lifespan)

    @app.exception_handler(APIError)
    async def api_error(request, error):
        return JSONResponse({"error": {"code": error.code, "message": error.message}}, status_code=error.status)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, error):
        return JSONResponse({"error": {"code": "validation_error", "message": "Invalid parameters."}}, status_code=422)

    @app.exception_handler(Exception)
    async def internal_error(request, error):
        return JSONResponse({"error": {"code": "internal_error", "message": "Internal error."}}, status_code=500)

    bearer = HTTPBearer(auto_error=False)

    async def current_user(request: Request, credentials: Annotated[
        HTTPAuthorizationCredentials | None, Depends(bearer),
    ]):
        if "authorization" not in request.headers:
            return authenticate_browser(request)
        if credentials is None or credentials.scheme.lower() != "bearer":
            raise APIError(401, "unauthorized", "A valid session is required.")
        request.state.session_token, request.state.auth_kind = credentials.credentials, "bearer"
        return request.app.state.db.authenticate(credentials.credentials, kind="bearer")

    @app.get("/api/v1/private")
    async def private(user: Annotated[str, Depends(current_user)]):
        return {"user": user}

    app.include_router(social_identity_router(settings, current_user))
    app.include_router(accounts_router(settings, current_user))
    app.include_router(browser_sessions_router(settings, current_user))
    return app


@pytest.fixture(autouse=True)
def fast_passwords(monkeypatch):
    monkeypatch.setattr(accounts, "_derive_password", lambda password, salt: hashlib.sha512(
        salt + password.get_secret_value().encode(),
    ).digest())


@pytest.fixture
def client():
    with TestClient(make_app(), base_url=ORIGIN, client=("127.0.0.1", 1234),
                    raise_server_exceptions=False) as client:
        yield client


def error(response, status, code):
    assert response.status_code == status, response.text
    assert response.json()["error"]["code"] == code


def session(client):
    response = client.get(PREFIX + "/session")
    assert response.status_code == 200, response.text
    return response.json()


def headers(client):
    return {"Origin": ORIGIN, "X-CSRF-Token": session(client)["csrfToken"]}


def start(client, name="qq"):
    response = client.post(PREFIX + f"/{name}/start", json={}, headers=headers(client))
    assert response.status_code == 200, response.text
    return response.json()["authorizeUrl"]


def authorize(client, name="qq"):
    response = client.get(start(client, name), headers={"Sec-Fetch-Site": "cross-site",
                          "Sec-Fetch-Mode": "navigate", "Sec-Fetch-Dest": "document"}, follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"] == "/test/"
    view = session(client)
    assert view["pending"] is not None
    return view


def repository(client):
    return SocialIdentityRepository(client.app.state.db, client.app.state.settings)


def pending_row(client):
    return client.app.state.db.conn.execute("SELECT * FROM social_pending").fetchone()


def proof(client, phone=PHONE, *, purpose="social_bind", subject=None):
    repo = PhoneIdentityRepository(client.app.state.db, client.app.state.settings)
    subject = subject or "social:" + pending_row(client)["pendingHash"]
    challenge, code = repo.begin_challenge(phone, purpose, subject, "127.0.0.1")
    repo.mark_issued(challenge.challengeId)
    return challenge, code


def bind_body(challenge, code, **options):
    return {"challengeId": challenge.challengeId, "code": code, "acceptedTermsVersion": "2026-11-19",
            "isAdult": True, "confirmBind": True, **options}


def phone_account(client, phone=PHONE, *, password=None):
    db = client.app.state.db
    salt = b"s" * 16 if password else None
    digest = accounts._derive_password(SecretStr(password), salt) if password else None
    with db.atomic():
        return PhoneIdentityRepository(db, client.app.state.settings).create_phone_account(phone, salt, digest, "已有人")


def legacy(client, username="legacy_social"):
    salt = b"l" * 16
    return _Accounts(client.app.state.db).register(AccountRegister(
        username=username, password=PASSWORD, acceptedTermsVersion="2026-11-19", isAdult=True,
    ), salt, accounts._derive_password(SecretStr(PASSWORD), salt), "legacy-token-" + username, 300)


def attach_pending(client, user):
    repo = repository(client)
    with repo.db.atomic():
        repo.attach(pending_row(client), user)


def test_session_cookie_hash_csrf_resume_and_no_authentication(client):
    response = client.get(PREFIX + "/session")
    data = response.json()
    assert set(data) == {"csrfToken", "expiresAt", "pending"} and data["pending"] is None
    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie and "SameSite=lax" in cookie and f"Path={COOKIE_PATH}" in cookie
    assert "Max-Age=1800" in cookie and "Domain=" not in cookie and "Secure" not in cookie
    raw = client.cookies.get(COOKIE_NAME)
    row = client.app.state.db.conn.execute("SELECT * FROM social_browsers").fetchone()
    assert row["browserHash"] == hashlib.sha256(raw.encode()).hexdigest()
    assert raw not in dict(row).values() and row["origin"] == ORIGIN
    assert data["csrfToken"] == social_csrf_token(client.app.state.settings, raw)
    assert client.get(PREFIX + "/session").json() == data
    assert client.get("/api/v1/private").status_code == 401
    assert client.get("/api/v1/private", headers={"Authorization": "Bearer " + raw}).status_code == 401
    assert client.app.state.db.conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("request_headers", [
    {}, {"Origin": ORIGIN}, {"Origin": ORIGIN, "X-CSRF-Token": "wrong"},
    {"Origin": "https://attacker.example"}, {"Origin": "null"},
    {"Origin": ORIGIN, "Sec-Fetch-Site": "cross-site"},
    {"Origin": ORIGIN, "Sec-Fetch-Site": "same-site"},
])
def test_every_social_post_requires_independent_origin_csrf(client, request_headers):
    authorize(client)
    for route in ("/qq/start", "/phone/challenges", "/bind-phone", "/complete", "/cancel"):
        body = {"phone": PHONE} if route == "/phone/challenges" else {}
        if route == "/bind-phone":
            body = {"challengeId": "missing", "code": "123456", "acceptedTermsVersion": "2026-11-19",
                    "isAdult": True, "confirmBind": True}
        assert client.post(PREFIX + route, json=body, headers=request_headers).status_code == 403
    assert pending_row(client) is not None
    assert client.app.state.db.conn.execute("SELECT COUNT(*) FROM phone_challenges").fetchone()[0] == 0


@pytest.mark.parametrize("request_headers", [
    {"Origin": "https://attacker.example"}, {"Sec-Fetch-Site": "cross-site"},
    {"Host": "attacker.example"}, {"Host": "127.0.0.1:8000@attacker.example"},
])
def test_social_session_read_rejects_cross_origin_or_wrong_host(client, request_headers):
    assert client.get(PREFIX + "/session", headers=request_headers).status_code == 403


def test_http_requires_loopback_peer_and_https_cookie_is_secure():
    with TestClient(make_app(), base_url=ORIGIN, client=("192.0.2.1", 12)) as remote:
        assert remote.get(PREFIX + "/session", headers={"X-Forwarded-For": "127.0.0.1"}).status_code == 403
    secure_origin = "https://127.0.0.1:8000"
    settings = settings_for(qq_redirect_uri=secure_origin + PREFIX + "/qq/callback",
                            wechat_redirect_uri=secure_origin + PREFIX + "/wechat/callback")
    with TestClient(make_app(settings), base_url=secure_origin, client=("127.0.0.1", 12)) as secure:
        assert "Secure" in secure.get(PREFIX + "/session").headers["set-cookie"]


def test_start_hashes_state_rejects_redirect_subject_and_rotates_pending(client):
    url = start(client)
    params = parse_qs(urlsplit(url).query)
    state = params["state"][0]
    row = client.app.state.db.conn.execute("SELECT * FROM social_oauth_states").fetchone()
    assert row["stateHash"] == hashlib.sha256(state.encode()).hexdigest()
    assert state not in dict(row).values() and row["provider"] == "qq" and row["status"] == "issued"
    assert row["redirectUri"] == ORIGIN + PREFIX + "/qq/callback"
    for field in ("redirectUri", "subject", "intent", "phone"):
        assert client.post(PREFIX + "/qq/start", headers=headers(client), json={field: "attacker"}).status_code == 422
    client.get(url, follow_redirects=False)
    assert pending_row(client) is not None
    start(client, "wechat")
    assert pending_row(client) is None
    assert client.app.state.db.conn.execute("SELECT COUNT(*) FROM social_oauth_states").fetchone()[0] == 1
    assert client.get(url, follow_redirects=False).status_code == 303
    assert pending_row(client) is None


@pytest.mark.parametrize("mutation", [
    "missing-state", "missing-code", "both-code-error", "duplicate-state", "duplicate-code",
    "unknown-redirect", "bad-state", "long-code", "wrong-provider", "code-other-state", "expired",
])
def test_callback_untrusted_input_cannot_create_pending(client, mutation):
    url = start(client)
    parsed = urlsplit(url)
    params = parse_qs(parsed.query)
    state, code = params["state"][0], params["code"][0]
    path = parsed.path
    if mutation == "missing-state":
        query = {"code": code}
    elif mutation == "missing-code":
        query = {"state": state}
    elif mutation == "both-code-error":
        query = {"state": state, "code": code, "error": "denied"}
    elif mutation == "duplicate-state":
        query = {"state": [state, state], "code": code}
    elif mutation == "duplicate-code":
        query = {"state": state, "code": [code, code]}
    elif mutation == "unknown-redirect":
        query = {"state": state, "code": code, "redirect": "https://attacker.example"}
    elif mutation == "bad-state":
        query = {"state": "not-the-issued-state", "code": code}
    elif mutation == "long-code":
        query = {"state": state, "code": "x" * 2049}
    elif mutation == "wrong-provider":
        query = {"state": state, "code": code}
        path = path.replace("/qq/", "/wechat/")
    elif mutation == "code-other-state":
        second = parse_qs(urlsplit(start(client)).query)
        query = {"state": second["state"][0], "code": code}
    else:
        with client.app.state.db.atomic():
            client.app.state.db.conn.execute("UPDATE social_oauth_states SET expiresAt=0")
        query = {"state": state, "code": code}
    response = client.get(path + "?" + urlencode(query, doseq=True), follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"] == "/test/"
    assert response.headers["cache-control"] == "no-store" and response.headers["referrer-policy"] == "no-referrer"
    assert "code" not in response.headers["location"] and pending_row(client) is None
    assert client.app.state.db.conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0


def test_callback_requires_same_browser_and_top_level_get_but_allows_cross_site_navigation(client):
    url = start(client)
    cookie = client.cookies.get(COOKIE_NAME)
    client.cookies.clear()
    assert client.get(url, follow_redirects=False).status_code == 303 and pending_row(client) is None
    client.cookies.set(COOKIE_NAME, cookie, domain="127.0.0.1", path=COOKIE_PATH)
    client.get(url, headers={"Sec-Fetch-Mode": "cors", "Sec-Fetch-Dest": "empty"}, follow_redirects=False)
    assert pending_row(client) is None
    response = client.get(url, headers={"Origin": "https://graph.qq.com", "Sec-Fetch-Site": "cross-site",
                          "Sec-Fetch-Mode": "navigate", "Sec-Fetch-Dest": "document"}, follow_redirects=False)
    assert response.status_code == 303
    pending = pending_row(client)
    assert pending is not None
    assert set(session(client)["pending"]) == {"provider", "displayName", "expiresAt", "requiresPhoneBinding"}
    before = pending["pendingHash"]
    client.get(url, follow_redirects=False)
    assert pending_row(client)["pendingHash"] == before
    error(client.post(PREFIX + "/complete", json={}, headers=headers(client)), 409, "phone_binding_required")
    assert client.get("/api/v1/private").status_code == 401


def test_provider_denial_consumes_state_and_never_issues_session(client):
    url = start(client)
    state = parse_qs(urlsplit(url).query)["state"][0]
    client.get(PREFIX + "/qq/callback?" + urlencode({"state": state, "error": "access_denied"}), follow_redirects=False)
    row = client.app.state.db.conn.execute("SELECT status FROM social_oauth_states").fetchone()
    assert row[0] == "exchanging"
    client.get(url, follow_redirects=False)
    assert pending_row(client) is None


def test_new_social_phone_account_can_have_no_password_and_atomic_bearer_then_browser_exchange(client):
    authorize(client)
    challenge = client.post(PREFIX + "/phone/challenges", json={"phone": PHONE}, headers=headers(client))
    assert challenge.status_code == 202 and "code" not in challenge.json()
    code = client.app.state.sms_sender.sent[-1][1]
    body = {"challengeId": challenge.json()["challengeId"], "code": code,
            "acceptedTermsVersion": "2026-11-19", "isAdult": True, "confirmBind": True}
    response = client.post(PREFIX + "/bind-phone", json=body, headers=headers(client))
    assert response.status_code == 200, response.text
    result = response.json()
    db = client.app.state.db
    row = db.conn.execute("SELECT * FROM accounts").fetchone()
    assert row["passwordEnabled"] == 0 and len(row["passwordSalt"]) == 16 and len(row["passwordHash"]) == 64
    assert db.conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 1
    assert db.conn.execute("SELECT phone FROM phone_phones").fetchone()[0] == "+86" + PHONE
    assert db.conn.execute("SELECT provider,userId FROM social_identities").fetchone()["userId"] == result["user"]["id"]
    assert pending_row(client) is None and session(client)["pending"] is None
    assert client.get("/api/v1/private", headers={"Authorization": "Bearer " + result["accessToken"]}).status_code == 200
    browser = client.post("/api/v1/auth/browser/session", headers={"Origin": ORIGIN,
                          "Authorization": "Bearer " + result["accessToken"]})
    assert browser.status_code == 200 and client.get("/api/v1/private").status_code == 200
    assert client.get("/api/v1/private", headers={"Authorization": "Bearer " + result["accessToken"]}).status_code == 401
    assert "accessToken" not in str(session(client))


@pytest.mark.parametrize("existing_password,supplied", [(None, None), (PASSWORD, None), (PASSWORD, PASSWORD)])
def test_existing_verified_phone_explicitly_links_same_account_without_data_merge(client, existing_password, supplied):
    account = phone_account(client, password=existing_password)
    authorize(client)
    challenge, code = proof(client)
    body = bind_body(challenge, code, **({"password": supplied} if supplied else {}))
    response = client.post(PREFIX + "/bind-phone", json=body, headers=headers(client))
    assert response.status_code == 200 and response.json()["user"]["id"] == account["userId"]
    assert client.app.state.db.conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0] == 1
    assert client.app.state.db.conn.execute("SELECT COUNT(*) FROM social_identities").fetchone()[0] == 1


@pytest.mark.parametrize("existing_password", [None, PASSWORD])
def test_if_password_provided_it_is_verified_and_wrong_does_not_consume_otp(client, existing_password):
    phone_account(client, password=existing_password)
    authorize(client)
    challenge, code = proof(client)
    response = client.post(PREFIX + "/bind-phone", headers=headers(client),
                           json=bind_body(challenge, code, password="wrong-password-123"))
    error(response, 401, "invalid_credentials")
    assert pending_row(client) is not None
    assert client.app.state.db.conn.execute("SELECT status FROM phone_challenges").fetchone()[0] == "issued"
    assert client.app.state.db.conn.execute("SELECT COUNT(*) FROM social_identities").fetchone()[0] == 0


def test_new_account_optional_password_is_created_enabled(client):
    authorize(client)
    challenge, code = proof(client)
    response = client.post(PREFIX + "/bind-phone", headers=headers(client),
                           json=bind_body(challenge, code, password=PASSWORD, displayName="选择的名字"))
    assert response.status_code == 200 and response.json()["user"]["displayName"] == "选择的名字"
    account = client.app.state.db.conn.execute("SELECT * FROM accounts").fetchone()
    assert account["passwordEnabled"] == 1
    assert account["passwordHash"] == accounts._derive_password(SecretStr(PASSWORD), account["passwordSalt"])


@pytest.mark.parametrize("field,value", [
    ("confirmBind", False), ("confirmBind", "true"), ("confirmBind", 1), ("isAdult", False),
    ("isAdult", "true"), ("acceptedTermsVersion", "old"), ("subject", "attacker"),
    ("code", "１２３４５６"), ("code", "12345"), ("password", "tiny"),
])
def test_binding_requires_explicit_adult_terms_confirm_and_strict_otp(client, field, value):
    authorize(client)
    challenge, code = proof(client)
    body = {**bind_body(challenge, code), field: value}
    response = client.post(PREFIX + "/bind-phone", headers=headers(client), json=body)
    error(response, 422, "validation_error")
    assert client.app.state.db.conn.execute("SELECT status FROM phone_challenges").fetchone()[0] == "issued"
    assert client.app.state.db.conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0] == 0


@pytest.mark.parametrize("purpose,subject", [("login", "public"), ("social_bind", "social:other-browser")])
def test_phone_challenge_is_bound_to_exact_pending_and_purpose(client, purpose, subject):
    authorize(client)
    challenge, code = proof(client, purpose=purpose, subject=subject)
    error(client.post(PREFIX + "/bind-phone", headers=headers(client), json=bind_body(challenge, code)),
          401, "invalid_phone_code")
    assert client.app.state.db.conn.execute("SELECT attempts FROM phone_challenges").fetchone()[0] == 1
    assert client.app.state.db.conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0] == 0


def test_wrong_otp_attempts_persist_outside_success_transaction_and_lock_at_five(client):
    authorize(client)
    challenge, code = proof(client)
    wrong = "000000" if code != "000000" else "000001"
    for _ in range(5):
        error(client.post(PREFIX + "/bind-phone", headers=headers(client), json=bind_body(challenge, wrong)),
              401, "invalid_phone_code")
    row = client.app.state.db.conn.execute("SELECT status,attempts FROM phone_challenges").fetchone()
    assert tuple(row) == ("invalid", 5)
    error(client.post(PREFIX + "/bind-phone", headers=headers(client), json=bind_body(challenge, code)),
          401, "invalid_phone_code")


def test_existing_social_legacy_without_phone_binds_original_account_no_shadow(client):
    account = legacy(client)
    authorize(client)
    attach_pending(client, account.user.id)
    challenge, code = proof(client)
    result = client.post(PREFIX + "/bind-phone", headers=headers(client), json=bind_body(challenge, code))
    assert result.status_code == 200 and result.json()["user"]["id"] == account.user.id
    assert client.app.state.db.conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0] == 1
    assert client.app.state.db.conn.execute("SELECT userId FROM phone_phones").fetchone()[0] == account.user.id


@pytest.mark.parametrize("old_has_phone", [False, True])
def test_social_bound_to_different_account_cannot_switch_to_existing_phone(client, old_has_phone):
    old = phone_account(client, OTHER_PHONE) if old_has_phone else _Accounts(client.app.state.db).require(legacy(client).user.id)
    target = phone_account(client)
    authorize(client)
    attach_pending(client, old["userId"])
    challenge, code = proof(client)
    error(client.post(PREFIX + "/bind-phone", headers=headers(client), json=bind_body(challenge, code)),
          409, "identity_conflict")
    assert client.app.state.db.conn.execute("SELECT userId FROM social_identities").fetchone()[0] == old["userId"]
    assert target["userId"] != old["userId"] and pending_row(client) is not None
    assert client.app.state.db.conn.execute("SELECT status FROM phone_challenges").fetchone()[0] == "issued"


def test_complete_requires_link_and_current_verified_phone_and_consumes_exactly_once(client):
    account = phone_account(client)
    authorize(client)
    attach_pending(client, account["userId"])
    assert session(client)["pending"]["requiresPhoneBinding"] is False
    first = client.post(PREFIX + "/complete", headers=headers(client), json={})
    assert first.status_code == 200 and first.json()["user"]["id"] == account["userId"]
    error(client.post(PREFIX + "/complete", headers=headers(client), json={}), 401, "social_pending_required")
    authorize(client)
    with client.app.state.db.atomic():
        client.app.state.db.conn.execute("DELETE FROM phone_phones")
    error(client.post(PREFIX + "/complete", headers=headers(client), json={}), 409, "phone_binding_required")


def test_cancel_expires_both_state_and_pending_cookie_and_prevents_old_callback(client):
    url = start(client)
    old = client.cookies.get(COOKIE_NAME)
    result = client.post(PREFIX + "/cancel", json={}, headers=headers(client))
    assert result.status_code == 200 and result.json() == {"ok": True}
    assert "Max-Age=0" in result.headers["set-cookie"] and client.cookies.get(COOKIE_NAME) is None
    client.cookies.set(COOKIE_NAME, old, domain="127.0.0.1", path=COOKIE_PATH)
    client.get(url, follow_redirects=False)
    db = client.app.state.db
    assert db.conn.execute("SELECT COUNT(*) FROM social_browsers").fetchone()[0] == 0
    assert db.conn.execute("SELECT COUNT(*) FROM social_oauth_states").fetchone()[0] == 0
    assert pending_row(client) is None


@pytest.mark.parametrize("stage", ["session", "link", "account"])
def test_atomic_binding_rolls_back_otp_account_identity_pending_and_session(client, monkeypatch, stage):
    authorize(client)
    challenge, code = proof(client)
    db = client.app.state.db
    if stage == "session":
        monkeypatch.setattr(db, "create_session", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("synthetic")))
    elif stage == "link":
        with db.atomic():
            db.conn.execute("CREATE TRIGGER fail_social BEFORE INSERT ON social_identities BEGIN SELECT RAISE(ABORT,'synthetic'); END")
    else:
        with db.atomic():
            db.conn.execute("CREATE TRIGGER fail_account BEFORE INSERT ON accounts BEGIN SELECT RAISE(ABORT,'synthetic'); END")
    response = client.post(PREFIX + "/bind-phone", headers=headers(client), json=bind_body(challenge, code))
    error(response, 500, "internal_error")
    for table in ("accounts", "phone_phones", "social_identities", "sessions", "users"):
        assert db.conn.execute("SELECT COUNT(*) FROM " + table).fetchone()[0] == 0
    assert pending_row(client) is not None
    assert db.conn.execute("SELECT status FROM phone_challenges").fetchone()[0] == "issued"
    assert not db.conn.in_transaction


def test_pending_rotation_during_password_worker_refuses_old_challenge_and_writes(client):
    authorize(client)
    challenge, code = proof(client)
    db = client.app.state.db
    tools = accounts._AccountAuthTools()

    class RotateWorker:
        async def run(self, operation):
            assert not db.conn.in_transaction
            result = operation()
            with db.atomic():
                db.conn.execute("DELETE FROM social_pending")
            return result

    tools.workers = RotateWorker()
    client.app.state.account_auth_tools = tools
    error(client.post(PREFIX + "/bind-phone", headers=headers(client), json=bind_body(challenge, code, password=PASSWORD)),
          401, "social_pending_required")
    assert db.conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0] == 0
    assert db.conn.execute("SELECT status FROM phone_challenges").fetchone()[0] == "issued"


def test_account_password_snapshot_is_rechecked_after_worker(client):
    account = phone_account(client, password=PASSWORD)
    authorize(client)
    challenge, code = proof(client)
    db = client.app.state.db
    tools = accounts._AccountAuthTools()

    class ChangeWorker:
        async def run(self, operation):
            assert not db.conn.in_transaction
            result = operation()
            with db.atomic():
                db.conn.execute("UPDATE accounts SET passwordHash=? WHERE userId=?", (b"x" * 64, account["userId"]))
            return result

    tools.workers = ChangeWorker()
    client.app.state.account_auth_tools = tools
    error(client.post(PREFIX + "/bind-phone", headers=headers(client), json=bind_body(challenge, code, password=PASSWORD)),
          401, "invalid_credentials")
    assert client.app.state.db.conn.execute("SELECT COUNT(*) FROM social_identities").fetchone()[0] == 0
    assert db.conn.execute("SELECT status FROM phone_challenges").fetchone()[0] == "issued"


def test_provider_await_has_no_transaction_rechecks_rotated_state_and_never_logs_exception(client, caplog):
    url = start(client)
    provider = client.app.state.social_providers["qq"]
    db = client.app.state.db

    class RotatingProvider:
        client_id, redirect_uri, simulated = provider.client_id, provider.redirect_uri, True

        async def exchange_code(self, code, redirect):
            assert not db.conn.in_transaction
            with db.atomic():
                db.conn.execute("DELETE FROM social_oauth_states")
            return VerifiedSocialIdentity("qq", self.client_id, "verified", "name")

    client.app.state.social_providers["qq"] = RotatingProvider()
    client.get(url, follow_redirects=False)
    assert pending_row(client) is None
    assert "verified" not in caplog.text


def test_sms_await_revalidates_pending_without_account_writes(client):
    authorize(client)
    db = client.app.state.db
    def remove_pending():
        assert not db.conn.in_transaction
        with db.atomic():
            db.conn.execute("DELETE FROM social_pending")
    client.app.state.sms_sender.before_send = remove_pending
    error(client.post(PREFIX + "/phone/challenges", json={"phone": PHONE}, headers=headers(client)),
          401, "social_pending_required")
    assert db.conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0] == 0


def test_disabled_registration_never_consumes_otp_or_creates_account(client):
    client.app.state.settings.registration_enabled = False
    authorize(client)
    challenge, code = proof(client)
    error(client.post(PREFIX + "/bind-phone", json=bind_body(challenge, code), headers=headers(client)),
          403, "registration_disabled")
    assert client.app.state.db.conn.execute("SELECT status FROM phone_challenges").fetchone()[0] == "issued"
    assert pending_row(client) is not None


def test_initializer_idempotence_constructor_no_commit_and_fk_cascade(client):
    db = client.app.state.db
    initialize_social_identity(db)
    with pytest.raises(RuntimeError, match="rollback"), db.atomic():
        db.conn.execute("INSERT INTO social_browsers(browserHash,origin,expiresAt) VALUES('rollback','http://localhost',9999999999)")
        SocialIdentityRepository(db)
        raise RuntimeError("rollback")
    assert db.conn.execute("SELECT 1 FROM social_browsers WHERE browserHash='rollback'").fetchone() is None
    account = phone_account(client)
    authorize(client)
    attach_pending(client, account["userId"])
    with db.atomic():
        db.conn.execute("DELETE FROM accounts WHERE userId=?", (account["userId"],))
    assert db.conn.execute("SELECT COUNT(*) FROM social_identities").fetchone()[0] == 0
    error(client.post(PREFIX + "/complete", headers=headers(client), json={}), 409, "phone_binding_required")


@pytest.mark.parametrize("mutation", ["browser-expired", "state-expired", "provider-replaced", "provider-mutated", "cancelled"])
def test_provider_await_revalidates_all_state_and_configuration(client, mutation):
    url = start(client)
    provider = client.app.state.social_providers["qq"]
    db = client.app.state.db

    class ChangingProvider:
        client_id, redirect_uri, simulated = provider.client_id, provider.redirect_uri, True

        async def exchange_code(self, code, redirect):
            assert not db.conn.in_transaction
            if mutation == "provider-replaced":
                client.app.state.social_providers["qq"] = provider
            elif mutation == "provider-mutated":
                self.client_id = "different-client"
            else:
                with db.atomic():
                    if mutation == "browser-expired":
                        db.conn.execute("UPDATE social_browsers SET expiresAt=0")
                    elif mutation == "state-expired":
                        db.conn.execute("UPDATE social_oauth_states SET expiresAt=0")
                    else:
                        db.conn.execute("DELETE FROM social_browsers")
            return VerifiedSocialIdentity("qq", provider.client_id, "verified", "name")

    client.app.state.social_providers["qq"] = ChangingProvider()
    client.get(url, follow_redirects=False)
    assert pending_row(client) is None
    assert db.conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0


def test_provider_error_consumes_state_without_leaking_exception(client, caplog):
    url = start(client)
    original = client.app.state.social_providers["qq"]

    class FailingProvider:
        client_id, redirect_uri, simulated = original.client_id, original.redirect_uri, True
        calls = 0

        async def exchange_code(self, code, redirect):
            self.calls += 1
            assert not client.app.state.db.conn.in_transaction
            raise RuntimeError("sensitive-OAuth-code-and-token")

    provider = FailingProvider()
    client.app.state.social_providers["qq"] = provider
    for _ in range(2):
        response = client.get(url, follow_redirects=False)
        assert response.status_code == 303 and "sensitive" not in response.text
    assert provider.calls == 1 and pending_row(client) is None and "sensitive-OAuth" not in caplog.text


def test_simultaneous_callback_replay_performs_one_exchange_only():
    async def scenario():
        app = make_app()
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 99)),
                              base_url=ORIGIN) as client,
        ):
            view = (await client.get(PREFIX + "/session")).json()
            auth = {"Origin": ORIGIN, "X-CSRF-Token": view["csrfToken"]}
            url = (await client.post(PREFIX + "/qq/start", json={}, headers=auth)).json()["authorizeUrl"]
            original = app.state.social_providers["qq"]
            entered, release = asyncio.Event(), asyncio.Event()

            class SlowProvider:
                client_id, redirect_uri, simulated = original.client_id, original.redirect_uri, True
                calls = 0

                async def exchange_code(self, code, redirect):
                    self.calls += 1
                    assert not app.state.db.conn.in_transaction
                    entered.set()
                    await release.wait()
                    return VerifiedSocialIdentity("qq", self.client_id, "slow-verified", "name")

            provider = SlowProvider()
            app.state.social_providers["qq"] = provider
            first = asyncio.create_task(client.get(url))
            await asyncio.wait_for(entered.wait(), 2)
            second = await client.get(url)
            assert second.status_code == 303 and provider.calls == 1
            release.set()
            assert (await first).status_code == 303
            assert app.state.db.conn.execute("SELECT COUNT(*) FROM social_pending").fetchone()[0] == 1
            assert app.state.db.conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0] == 0
    asyncio.run(scenario())


@pytest.mark.parametrize("mutation", ["resend", "expired", "taken-phone", "flag-disabled"])
def test_password_worker_rechecks_otp_and_account_races(client, mutation):
    existing = phone_account(client, password=PASSWORD) if mutation == "flag-disabled" else None
    authorize(client)
    challenge, code = proof(client)
    db = client.app.state.db
    tools = accounts._AccountAuthTools()

    class RacingWorker:
        async def run(self, operation):
            assert not db.conn.in_transaction
            result = operation()
            with db.atomic():
                if mutation == "resend":
                    db.conn.execute("UPDATE phone_challenges SET status='invalid'")
                elif mutation == "expired":
                    db.conn.execute("UPDATE phone_challenges SET expiresAt=0")
                elif mutation == "taken-phone":
                    PhoneIdentityRepository(db, client.app.state.settings).create_phone_account(PHONE, None, None)
                else:
                    db.conn.execute("UPDATE accounts SET passwordEnabled=0 WHERE userId=?", (existing["userId"],))
            return result

    tools.workers = RacingWorker()
    client.app.state.account_auth_tools = tools
    response = client.post(PREFIX + "/bind-phone", headers=headers(client),
                           json=bind_body(challenge, code, password=PASSWORD))
    expected = (409, "identity_conflict") if mutation == "taken-phone" else (
        (401, "invalid_credentials") if mutation == "flag-disabled" else (401, "invalid_phone_code")
    )
    error(response, *expected)
    assert pending_row(client) is not None
    assert db.conn.execute("SELECT COUNT(*) FROM social_identities").fetchone()[0] == 0
    assert db.conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0


def test_complete_session_failure_rolls_back_pending_and_nested_outer_rollback(client, monkeypatch):
    account = phone_account(client)
    authorize(client)
    attach_pending(client, account["userId"])
    repo = repository(client)
    before = pending_row(client)["pendingHash"]
    with pytest.raises(RuntimeError, match="outer"), repo.db.atomic():
        repo.consume(pending_row(client), account["userId"])
        raise RuntimeError("outer")
    assert pending_row(client)["pendingHash"] == before
    monkeypatch.setattr(repo.db, "create_session", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("synthetic")))
    error(client.post(PREFIX + "/complete", json={}, headers=headers(client)), 500, "internal_error")
    assert pending_row(client)["pendingHash"] == before
    assert repo.db.conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0


def test_social_namespace_is_provider_and_client_id_not_union_nickname_or_phone(client):
    first, second = phone_account(client), phone_account(client, OTHER_PHONE)
    db = client.app.state.db
    with db.atomic():
        for name, client_id, subject, user in [
            ("qq", "app-a", "same-subject", first["userId"]),
            ("qq", "app-b", "same-subject", second["userId"]),
            ("wechat", "app-a", "same-subject", second["userId"]),
        ]:
            db.conn.execute("INSERT INTO social_identities(provider,clientId,subject,userId,linkedAt) VALUES(?,?,?,?,?)", (name, client_id, subject, user, "stamp"))
    repo = repository(client)
    assert repo.identity_user({"provider": "qq", "clientId": "app-a", "subject": "same-subject"}) == first["userId"]
    assert repo.identity_user({"provider": "qq", "clientId": "app-b", "subject": "same-subject"}) == second["userId"]
    assert repo.identity_user({"provider": "wechat", "clientId": "app-a", "subject": "same-subject"}) == second["userId"]


def test_missing_key_disabled_provider_and_expiry_fail_closed(client):
    settings = client.app.state.settings
    settings.phone_identity_hmac_key = ""
    error(client.get(PREFIX + "/session"), 503, "social_unavailable")
    settings.phone_identity_hmac_key = HMAC_KEY
    client.app.state.social_providers = {}
    error(client.get(PREFIX + "/session"), 503, "social_unavailable")
    error(client.post(PREFIX + "/qq/start", json={}), 503, "social_unavailable")


def test_pending_expiry_and_cookie_replacement_require_restart(client):
    authorize(client)
    db = client.app.state.db
    with db.atomic():
        db.conn.execute("UPDATE social_pending SET expiresAt=0")
    assert session(client)["pending"] is None
    error(client.post(PREFIX + "/complete", headers=headers(client), json={}), 401, "social_pending_required")
    old = client.cookies.get(COOKIE_NAME)
    with db.atomic():
        db.conn.execute("UPDATE social_browsers SET expiresAt=0")
    assert session(client)["pending"] is None and client.cookies.get(COOKIE_NAME) != old
    assert db.conn.execute("SELECT COUNT(*) FROM social_pending").fetchone()[0] == 0


def test_single_use_and_success_helpers_require_correct_transaction_boundaries(client):
    authorize(client)
    pending = pending_row(client)
    account = phone_account(client)
    repo = repository(client)
    with pytest.raises(RuntimeError, match="outer account transaction"):
        repo.attach(pending, account["userId"])
    with pytest.raises(RuntimeError, match="outer account transaction"):
        repo.consume(pending, account["userId"])
    provider = client.app.state.social_providers["qq"]
    with repo.db.atomic(), pytest.raises(RuntimeError, match="commit before provider I/O"):
        repo.exchange_state(repo.browser(client.cookies.get(COOKIE_NAME), ORIGIN), "qq", provider, "unused")
    assert pending_row(client)["pendingHash"] == pending["pendingHash"]
    assert not repo.db.conn.in_transaction


def test_social_browser_and_start_rate_limits_fail_closed(client):
    for _ in range(10):
        client.cookies.clear()
        assert client.get(PREFIX + "/session").status_code == 200
    client.cookies.clear()
    error(client.get(PREFIX + "/session"), 429, "rate_limited")
    assert client.app.state.db.conn.execute("SELECT COUNT(*) FROM social_browsers").fetchone()[0] == 10


def test_storage_capacity_does_not_evict_other_live_browser(client, monkeypatch):
    from allira import social_identity

    monkeypatch.setattr(social_identity, "MAX_BROWSERS", 1)
    view = session(client)
    token = client.cookies.get(COOKIE_NAME)
    client.cookies.clear()
    error(client.get(PREFIX + "/session"), 429, "rate_limited")
    client.cookies.set(COOKIE_NAME, token, domain="127.0.0.1", path=COOKIE_PATH)
    assert session(client) == view


@pytest.mark.parametrize("phase", ["issued", "exchanging"])
def test_credential_revocation_rejects_old_linked_oauth_and_fresh_start_succeeds(client, phase):
    account = phone_account(client)
    authorize(client)
    attach_pending(client, account["userId"])
    url = start(client)
    db = client.app.state.db
    provider = client.app.state.social_providers["qq"]
    flow_revision = db.conn.execute("SELECT flowRevision FROM social_oauth_states").fetchone()[0]

    if phase == "issued":
        with db.atomic():
            revoke_user_social_flows(db, account["userId"])
    else:
        class RevokingProvider:
            client_id, redirect_uri, simulated = provider.client_id, provider.redirect_uri, True

            async def exchange_code(self, code, redirect):
                assert not db.conn.in_transaction
                assert db.conn.execute("SELECT status FROM social_oauth_states").fetchone()[0] == "exchanging"
                with db.atomic():
                    revoke_user_social_flows(db, account["userId"])
                return VerifiedSocialIdentity("qq", self.client_id, "allira-test-subject-qq", "name")

        client.app.state.social_providers["qq"] = RevokingProvider()
    response = client.get(url, follow_redirects=False)
    assert response.status_code == 303 and pending_row(client) is None
    revoked_revision = db.conn.execute("SELECT revokedRevision FROM social_user_revocations").fetchone()[0]
    assert revoked_revision > flow_revision
    assert db.conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0
    client.app.state.social_providers["qq"] = provider
    authorize(client)
    assert pending_row(client)["flowRevision"] == revoked_revision
    response = client.post(PREFIX + "/complete", json={}, headers=headers(client))
    assert response.status_code == 200 and response.json()["user"]["id"] == account["userId"]


@pytest.mark.parametrize("phase", ["issued", "pending"])
def test_old_unlinked_oauth_cannot_later_select_a_revoked_phone_account(client, phase, monkeypatch):
    account = phone_account(client)
    url = start(client)
    if phase == "pending":
        client.get(url, follow_redirects=False)
    db = client.app.state.db
    with db.atomic():
        revoke_user_social_flows(db, account["userId"])
    if phase == "issued":
        client.get(url, follow_redirects=False)
    old = pending_row(client)
    assert old is not None and repository(client).identity_user(old) is None
    challenge, code = proof(client)
    error(client.post(PREFIX + "/bind-phone", headers=headers(client), json=bind_body(challenge, code)),
          401, "social_pending_required")
    assert db.conn.execute("SELECT status FROM phone_challenges WHERE challengeId=?", (challenge.challengeId,)).fetchone()[0] == "issued"
    assert db.conn.execute("SELECT COUNT(*) FROM social_identities").fetchone()[0] == 0
    assert db.conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0
    assert pending_row(client)["pendingHash"] == old["pendingHash"]
    with db.atomic(), pytest.raises(APIError) as failure:
        repository(client).consume(old, account["userId"])
    assert failure.value.code == "social_pending_required"
    authorize(client)
    current = phone_identity.time()
    monkeypatch.setattr(phone_identity, "time", lambda: current + 61)
    challenge, code = proof(client)
    response = client.post(PREFIX + "/bind-phone", headers=headers(client), json=bind_body(challenge, code))
    assert response.status_code == 200 and response.json()["user"]["id"] == account["userId"]


@pytest.mark.parametrize("linked", [False, True])
def test_revoking_other_user_does_not_cancel_this_flow(client, linked):
    account, other = phone_account(client), phone_account(client, OTHER_PHONE)
    if linked:
        authorize(client)
        attach_pending(client, account["userId"])
    url = start(client)
    db = client.app.state.db
    revision = db.conn.execute("SELECT flowRevision FROM social_oauth_states").fetchone()[0]
    with db.atomic():
        revoke_user_social_flows(db, other["userId"])
    client.get(url, follow_redirects=False)
    assert pending_row(client)["flowRevision"] == revision
    if linked:
        response = client.post(PREFIX + "/complete", headers=headers(client), json={})
    else:
        challenge, code = proof(client)
        response = client.post(PREFIX + "/bind-phone", headers=headers(client), json=bind_body(challenge, code))
    assert response.status_code == 200 and response.json()["user"]["id"] == account["userId"]


def test_revocation_helper_guard_developer_noop_atomic_cleanup_and_outer_rollback(client):
    db = client.app.state.db
    with pytest.raises(RuntimeError, match="outer account transaction"):
        revoke_user_social_flows(db, "dev-user")
    with db.atomic():
        revoke_user_social_flows(db, "dev-user")
    assert db.conn.execute("SELECT revision FROM social_revocation_clock").fetchone()[0] == 0
    account = phone_account(client)
    authorize(client)
    attach_pending(client, account["userId"])
    pending = pending_row(client)
    challenge, _ = proof(client)
    before = tuple(db.conn.iterdump())
    with pytest.raises(RuntimeError, match="rollback"), db.atomic():
        revoke_user_social_flows(db, account["userId"])
        assert pending_row(client) is None
        assert db.conn.execute("SELECT COUNT(*) FROM social_browsers").fetchone()[0] == 0
        assert db.conn.execute("SELECT 1 FROM phone_challenges WHERE challengeId=?", (challenge.challengeId,)).fetchone() is None
        raise RuntimeError("rollback")
    assert tuple(db.conn.iterdump()) == before and not db.conn.in_transaction
    assert pending_row(client)["pendingHash"] == pending["pendingHash"]
    with db.atomic():
        revoke_user_social_flows(db, account["userId"])
    assert db.conn.execute("SELECT revision FROM social_revocation_clock").fetchone()[0] == 1
    assert db.conn.execute("SELECT revokedRevision FROM social_user_revocations WHERE userId=?", (account["userId"],)).fetchone()[0] == 1
    assert pending_row(client) is None
    assert db.conn.execute("SELECT COUNT(*) FROM social_browsers").fetchone()[0] == 0
    assert db.conn.execute("SELECT COUNT(*) FROM phone_challenges").fetchone()[0] == 0
    initialize_social_identity(db)
    assert db.conn.execute("SELECT revision FROM social_revocation_clock").fetchone()[0] == 1


def test_revocation_clock_persists_across_restart_and_account_watermark_cascades(tmp_path):
    settings = settings_for()
    path = str(tmp_path / "revocations.sqlite3")
    db = Database(path, settings)
    with db.atomic():
        account = PhoneIdentityRepository(db, settings).create_phone_account(PHONE, None, None)
        revoke_user_social_flows(db, account["userId"])
    user = account["userId"]
    db.close()
    db = Database(path, settings)
    try:
        assert db.conn.execute("SELECT revision FROM social_revocation_clock").fetchone()[0] == 1
        assert db.conn.execute("SELECT revokedRevision FROM social_user_revocations WHERE userId=?", (user,)).fetchone()[0] == 1
        with db.atomic():
            revoke_user_social_flows(db, user)
        assert db.conn.execute("SELECT revision FROM social_revocation_clock").fetchone()[0] == 2
        with db.atomic():
            db.conn.execute("DELETE FROM accounts WHERE userId=?", (user,))
        assert db.conn.execute("SELECT COUNT(*) FROM social_user_revocations").fetchone()[0] == 0
        assert db.conn.execute("SELECT revision FROM social_revocation_clock").fetchone()[0] == 2
    finally:
        db.close()


def test_old_callback_after_account_erasure_is_unlinked_not_a_restored_session(client):
    account = phone_account(client)
    authorize(client)
    attach_pending(client, account["userId"])
    url = start(client)
    db = client.app.state.db
    with db.atomic():
        revoke_user_social_flows(db, account["userId"])
        db.conn.execute("DELETE FROM accounts WHERE userId=?", (account["userId"],))
    client.get(url, follow_redirects=False)
    pending = pending_row(client)
    assert pending is not None and repository(client).identity_user(pending) is None
    error(client.post(PREFIX + "/complete", headers=headers(client), json={}), 409, "phone_binding_required")
    assert db.conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0
    challenge, code = proof(client)
    response = client.post(PREFIX + "/bind-phone", headers=headers(client), json=bind_body(challenge, code))
    assert response.status_code == 200 and response.json()["user"]["id"] != account["userId"]


def test_pre_revision_transient_schema_is_invalidated_not_promoted(client):
    authorize(client)
    db = client.app.state.db
    state = db.conn.execute("SELECT * FROM social_oauth_states").fetchone()
    pending = pending_row(client)
    with db.atomic():
        db.conn.execute("DROP TABLE social_pending")
        db.conn.execute("DROP TABLE social_oauth_states")
        db.conn.execute("""CREATE TABLE social_oauth_states (
            stateHash TEXT PRIMARY KEY, browserHash TEXT NOT NULL,
            provider TEXT NOT NULL, clientId TEXT NOT NULL, redirectUri TEXT NOT NULL,
            status TEXT NOT NULL, expiresAt REAL NOT NULL
        )""")
        db.conn.execute("""CREATE TABLE social_pending (
            pendingHash TEXT PRIMARY KEY, browserHash TEXT NOT NULL,
            provider TEXT NOT NULL, clientId TEXT NOT NULL, subject TEXT NOT NULL,
            displayName TEXT NOT NULL, expiresAt REAL NOT NULL
        )""")
        db.conn.execute(
            "INSERT INTO social_oauth_states(stateHash,browserHash,provider,clientId,redirectUri,status,"
            "expiresAt) VALUES(?,?,?,?,?,?,?)", tuple(state)[:-1],
        )
        db.conn.execute(
            "INSERT INTO social_pending(pendingHash,browserHash,provider,clientId,subject,displayName,"
            "expiresAt) VALUES(?,?,?,?,?,?,?)", tuple(pending)[:-1],
        )
    initialize_social_identity(db)
    for table in ("social_oauth_states", "social_pending"):
        assert db.conn.execute("SELECT COUNT(*) FROM " + table).fetchone()[0] == 0
        assert "flowRevision" in {row["name"] for row in db.conn.execute("PRAGMA table_info(" + table + ")")}
    assert db.conn.execute("SELECT revision FROM social_revocation_clock").fetchone()[0] == 0


def test_revocation_clock_exhaustion_fails_closed_without_partial_changes(client):
    account = phone_account(client)
    db = client.app.state.db
    with db.atomic():
        db.conn.execute("UPDATE social_revocation_clock SET revision=9223372036854775807")
    before = tuple(db.conn.iterdump())
    with pytest.raises(RuntimeError, match="clock is unavailable"), db.atomic():
        revoke_user_social_flows(db, account["userId"])
    assert tuple(db.conn.iterdump()) == before and not db.conn.in_transaction


def test_concurrent_complete_only_one_session_commits():
    async def scenario():
        app = make_app()
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 99))
            async with httpx.AsyncClient(transport=transport, base_url=ORIGIN) as client:
                view = (await client.get(PREFIX + "/session")).json()
                auth = {"Origin": ORIGIN, "X-CSRF-Token": view["csrfToken"]}
                url = (await client.post(PREFIX + "/qq/start", json={}, headers=auth)).json()["authorizeUrl"]
                await client.get(url)
                db = app.state.db
                with db.atomic():
                    account = PhoneIdentityRepository(db, app.state.settings).create_phone_account(PHONE, None, None)
                    pending = db.conn.execute("SELECT * FROM social_pending").fetchone()
                    SocialIdentityRepository(db).attach(pending, account["userId"])
                responses = await asyncio.gather(*[
                    client.post(PREFIX + "/complete", json={}, headers=auth) for _ in range(8)
                ])
                assert [r.status_code for r in responses].count(200) == 1
                assert [r.status_code for r in responses].count(401) == 7
                assert db.conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 1
    asyncio.run(scenario())
