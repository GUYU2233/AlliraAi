"""Real application boundary tests with synthetic signed OIDC responses.

This suite tests RP wiring, not Logto, SMS or a real social provider. Authlib and
joserfc still perform code/PKCE and ID-token verification through MockTransport.
"""

import hashlib
import secrets
import sqlite3
from contextlib import closing
from pathlib import Path
from time import time
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from joserfc import jwk, jwt
from pydantic import ValidationError

from allira import accounts
from allira.accounts import AccountRegister, _Accounts
from allira.admin_db import AdminRepository
from allira.app import create_app
from allira.backup_cli import create_backup, restore_backup
from allira.config import Settings
from allira.db import APIError, Database
from allira.economy import EnergyRepository
from allira.identity_lifecycle import clear_transient_identity_state
from allira.imports import ImportCommitInput, ImportRepository
from allira.phone_identity import PhoneIdentityRepository

ORIGIN = "http://127.0.0.1:8000"
ISSUER = "http://127.0.0.1:3001/oidc"
PREFIX = "/api/v1/auth/oidc"
PHONE = "+8613800138000"
PASSWORD = "synthetic-legacy-password-123"
CONFIRM = {"acceptedTermsVersion": "2026-11-19", "isAdult": True, "confirmIdentity": True}
TRANSIENT = ("sessions", "admin_sessions", "admin_login_codes", "account_recovery_codes",
             "phone_challenges", "social_pending", "social_oauth_states", "social_browsers",
             "oidc_pending", "oidc_states", "oidc_browsers")


def cheap_password(password, salt):
    return hashlib.sha512(salt + password.get_secret_value().encode()).digest()


class SyntheticIssuer:
    def __init__(self, db, key):
        self.db, self.key = db, key
        self.subject, self.phone = "synthetic-subject", PHONE

    async def __call__(self, request):
        assert not self.db.conn.in_transaction
        if request.url.path.endswith("/.well-known/openid-configuration"):
            return httpx.Response(200, json={
                "issuer": ISSUER, "authorization_endpoint": ISSUER + "/auth",
                "token_endpoint": ISSUER + "/token", "userinfo_endpoint": ISSUER + "/me",
                "jwks_uri": ISSUER + "/jwks", "id_token_signing_alg_values_supported": ["ES384"],
                "code_challenge_methods_supported": ["S256"],
            })
        if request.url.path == "/oidc/jwks":
            return httpx.Response(200, json={"keys": [self.key.as_dict(private=False)]})
        if request.url.path == "/oidc/me":
            return httpx.Response(200, json={"sub": self.subject, "phone_number": self.phone,
                                           "phone_number_verified": True, "name": "测试朋友"})
        assert request.url.path == "/oidc/token"
        flow = self.db.conn.execute("SELECT * FROM oidc_states WHERE status='exchanging'").fetchone()
        assert parse_qs(request.content.decode())["code_verifier"] == [flow["codeVerifier"]]
        token = jwt.encode({"alg": "ES384", "kid": "isolated-p384"}, {
            "iss": ISSUER, "aud": "synthetic-client", "sub": self.subject,
            "iat": int(time()), "exp": int(time()) + 300,
            "nonce": flow["nonce"], "auth_time": int(time()),
        }, self.key, algorithms=["ES384"])
        return httpx.Response(200, json={"id_token": token,
                                       "access_token": "synthetic-access-in-memory", "token_type": "Bearer"})


@pytest.fixture(scope="session")
def signing_key():
    return jwk.ECKey.generate_key("P-384", parameters={"kid": "isolated-p384", "alg": "ES384", "use": "sig"})


@pytest.fixture
def client(tmp_path, monkeypatch, signing_key):
    monkeypatch.setattr(accounts, "_derive_password", cheap_password)
    settings = Settings(
        _env_file=None, database_path=str(tmp_path / "oidc.sqlite3"), provider="mock",
        auth_backend="oidc", oidc_issuer_url=ISSUER, oidc_client_id="synthetic-client",
        oidc_client_secret="synthetic-confidential-secret", oidc_simulated=True,
        oidc_redirect_uri=ORIGIN + PREFIX + "/callback", allow_dev_auth=True, admin_enabled=True,
    )
    with TestClient(create_app(settings), base_url=ORIGIN, client=("127.0.0.1", 1234),
                    raise_server_exceptions=False) as active:
        active.issuer = SyntheticIssuer(active.app.state.db, signing_key)
        active.app.state.oidc_transport = httpx.MockTransport(active.issuer)
        yield active


def error(response, status, code):
    assert response.status_code == status, response.text
    assert response.json()["error"]["code"] == code
    assert set(response.json()) == {"error"}


def seed_legacy(client, *, phone=None):
    db = client.app.state.db
    body = AccountRegister(username="legacy_" + uuid4().hex[:12], password=PASSWORD,
                           acceptedTermsVersion="2026-11-19", isAdult=True)
    salt, token = secrets.token_bytes(16), secrets.token_urlsafe(32)
    result = _Accounts(db).register(body, salt, cheap_password(body.password, salt), token, 3600)
    if phone:
        PhoneIdentityRepository(db).bind_phone(result.user.id, phone)
    return result


def pending(client, *, purpose="login", ordinary_csrf=None):
    view = client.get(PREFIX + "/session")
    assert view.status_code == 200
    headers = {"Origin": ORIGIN, "X-Allira-OIDC-CSRF": view.json()["csrfToken"]}
    if ordinary_csrf:
        headers["X-CSRF-Token"] = ordinary_csrf
    started = client.post(PREFIX + "/start", json={"purpose": purpose}, headers=headers)
    assert started.status_code == 200, started.text
    query = parse_qs(urlsplit(started.json()["authorizationUrl"]).query)
    if purpose in {"erase", "phone_migration"}:
        assert query["prompt"] == ["login"] and query["max_age"] == ["0"]
    callback = client.get(PREFIX + "/callback", params={"state": query["state"][0], "code": "test-code"},
                          follow_redirects=False)
    assert callback.status_code == 303 and callback.headers["location"] == "/test/"
    assert client.get(PREFIX + "/session").json()["pending"]["purpose"] == purpose
    return headers


def complete(client):
    headers = pending(client)
    response = client.post(PREFIX + "/complete", json=CONFIRM, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def remembered(client, session):
    response = client.post("/api/v1/auth/browser/session", headers={
        "Origin": ORIGIN, "Authorization": "Bearer " + session["accessToken"],
    })
    assert response.status_code == 200, response.text
    return response.json()["csrfToken"]


def test_options_describes_actual_backend_and_inactive_local_channels(client):
    body = client.get("/api/v1/auth/options").json()
    assert body["backend"] == "oidc" and not body["legacyPasswordEnabled"]
    assert body["oidc"] == {"enabled": True, "provider": "logto", "simulated": True,
                             "reason": None, "authorizationOrigin": "http://127.0.0.1:3001"}
    assert not body["phone"]["enabled"] and not any(item["enabled"] for item in body["social"])
    assert client.app.state.db.conn.execute("PRAGMA user_version").fetchone()[0] == 8


def test_operationally_disabled_oidc_does_not_restore_previous_core(client):
    client.app.state.db.settings.oidc_enabled = False
    response = client.get("/api/v1/auth/options")
    oidc = response.json()["oidc"]
    assert oidc == {"enabled": False, "provider": "logto", "simulated": False,
                    "reason": "oidc_disabled", "authorizationOrigin": "http://127.0.0.1:3001"}
    error(client.get(PREFIX + "/session"), 503, "oidc_unavailable")
    error(client.post(PREFIX + "/start", json={}), 503, "oidc_unavailable")
    error(client.post("/api/v1/auth/login", json={}), 403, "oidc_auth_required")
    assert client.app.state.db.conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0] == 0


@pytest.mark.parametrize("backend", ["legacy", "oidc"])
@pytest.mark.parametrize("environment,allowed", [("development", True), ("development", False), ("production", False)])
@pytest.mark.parametrize("kind", ["bearer", "browser"])
def test_issued_developer_sessions_obey_current_runtime_policy(tmp_path, backend, environment, allowed, kind):
    settings = Settings(_env_file=None, database_path=str(tmp_path / "dev-policy.sqlite3"),
                        auth_backend=backend, environment=environment, allow_dev_auth=allowed)
    db = Database(settings.database_path, settings)
    try:
        db.create_session("historical-developer-token", "historical-developer", 3600, kind=kind)
        if allowed:
            assert db.authenticate("historical-developer-token", kind=kind) == "historical-developer"
        else:
            with pytest.raises(APIError) as rejected:
                db.authenticate("historical-developer-token", kind=kind)
            assert rejected.value.status == 401
        # Rejected credentials do not delete the owner's data or reinterpret it as a
        # registered account. A test operator can explicitly re-enable only in dev.
        assert db.conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 1
        assert db.conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0] == 0
    finally:
        db.close()


def test_disabled_developer_token_cannot_create_conversations_or_wallet(client):
    db = client.app.state.db
    db.create_session("pre-cutover-dev", "developer-before-cutover", 3600)
    db.settings.allow_dev_auth = False
    headers = {"Authorization": "Bearer pre-cutover-dev"}
    for method, path in [("GET", "/api/v1/session"), ("POST", "/api/v1/conversations"),
                         ("GET", "/api/v1/energy")]:
        error(client.request(method, path, headers=headers, **({"json": {}} if method == "POST" else {})),
              401, "unauthorized")
    assert db.conn.execute("SELECT COUNT(*) FROM conversations").fetchone()[0] == 0
    assert db.conn.execute("SELECT COUNT(*) FROM energy_wallets").fetchone()[0] == 0


@pytest.mark.parametrize("path", [
    "/auth/register", "/auth/login", "/auth/recover", "/auth/phone/challenges",
    "/auth/phone/register", "/auth/phone/login", "/auth/phone/password",
    "/auth/social/qq/start", "/auth/social/wechat/start", "/auth/social/complete",
    "/account/phone/challenges", "/account/phone/bind", "/account/phone/password",
    "/account/phone/security/challenges", "/account/phone/erase",
    "/account/password", "/account/recovery", "/account/erase",
])
def test_previous_core_closed_on_server_even_for_direct_calls(client, path):
    response = client.post("/api/v1" + path, json={})
    error(response, 403, "oidc_auth_required")
    assert response.headers["cache-control"] == "no-store"
    assert client.app.state.db.conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0] == 0


def test_pre_cutover_bearer_and_cookie_do_not_become_oidc_proofs(client):
    legacy = seed_legacy(client)
    db, user = client.app.state.db, legacy.user.id
    db.create_session("synthetic-old-browser", user, 3600, kind="browser")
    client.cookies.set("allira_user", "synthetic-old-browser", path="/api/v1")
    error(client.get("/api/v1/account", headers={"Authorization": "Bearer " + legacy.accessToken}),
          401, "oidc_login_required")
    error(client.get("/api/v1/auth/browser/session"), 401, "oidc_login_required")
    error(client.get("/api/v1/account", headers={"Authorization": "invalid"}), 401, "unauthorized")
    assert db.conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0] == 1


def test_pending_is_not_account_or_admin_and_tokens_never_exported(client):
    headers = pending(client)
    db = client.app.state.db
    assert db.conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0] == 0
    error(client.get("/api/v1/account"), 401, "unauthorized")
    error(client.get("/api/v1/admin/overview"), 401, "unauthorized")
    session = client.post(PREFIX + "/complete", json=CONFIRM, headers=headers).json()
    bearer = {"Authorization": "Bearer " + session["accessToken"]}
    assert client.get("/api/v1/account", headers=bearer).json()["id"] == session["user"]["id"]
    exported = client.get("/api/v1/account/export", headers=bearer)
    assert exported.status_code == 200 and exported.json()["identity"]["phoneVerified"]
    for secret in ("synthetic-access-in-memory", "synthetic-confidential-secret", client.issuer.subject):
        assert secret not in exported.text
    error(client.get("/api/v1/admin/overview", headers=bearer), 401, "unauthorized")
    error(client.get("/api/v1/account", headers={"Authorization": "Bearer synthetic-access-in-memory"}),
          401, "unauthorized")


@pytest.mark.parametrize("phone_bound", [False, True])
def test_explicit_old_owner_migration_preserves_history_and_revokes_credentials(client, phone_bound):
    original = seed_legacy(client, phone=PHONE if phone_bound else None)
    db, user = client.app.state.db, original.user.id
    chat = ImportRepository(db).commit(user, ImportCommitInput(
        clientImportId=uuid4(), format="json", content='[{"role":"user","content":"旧原文"},'
        '{"role":"assistant","content":"旧回复"}]',
    ))
    db.create_session("another-old-device", user, 3600)
    headers = pending(client)
    if phone_bound:
        error(client.post(PREFIX + "/complete", json=CONFIRM, headers=headers), 409, "identity_link_required")
    migrated = client.post(PREFIX + "/migrate", json={**CONFIRM, "username": original.user.username,
                                                       "currentPassword": PASSWORD}, headers=headers)
    assert migrated.status_code == 200, migrated.text
    session = migrated.json()
    assert session["user"]["id"] == user
    assert db.conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0] == 1
    assert db.conn.execute("SELECT COUNT(*) FROM sessions WHERE userId=?", (user,)).fetchone()[0] == 1
    assert db.conn.execute("SELECT revision FROM oidc_revocation_clock").fetchone()[0] == 1
    auth = {"Authorization": "Bearer " + session["accessToken"]}
    messages = client.get("/api/v1/conversations/" + str(chat.conversation.id) + "/messages", headers=auth)
    assert [item["content"] for item in messages.json()["items"]] == ["旧原文", "旧回复"]
    error(client.get("/api/v1/account", headers={"Authorization": "Bearer " + original.accessToken}),
          401, "unauthorized")
    error(client.get("/api/v1/account", headers={"Authorization": "Bearer another-old-device"}),
          401, "unauthorized")
    assert db.conn.execute("PRAGMA foreign_key_check").fetchall() == []


@pytest.mark.parametrize("registration_enabled", [True, False])
def test_explicit_fresh_phone_only_migration_keeps_business_owner(client, registration_enabled):
    db = client.app.state.db
    with db.atomic():
        original = PhoneIdentityRepository(db).create_phone_account(PHONE, None, None)
        user = original["userId"]
        db.create_session("old-phone-only-bearer", user, 3600)
        db.create_session("old-phone-only-browser", user, 3600, kind="browser")
        chat = ImportRepository(db).commit(user, ImportCommitInput(
            clientImportId=uuid4(), format="json", content='[{"role":"user","content":"手机旧历史"},'
            '{"role":"assistant","content":"保留这段回复"}]',
        ))
        wallet = EnergyRepository(db, db.settings).wallet(user)
    db.settings.registration_enabled = registration_enabled
    normal = pending(client)
    error(client.post(PREFIX + "/complete", json={**CONFIRM, "confirmPhoneMigration": True}, headers=normal),
          401, "oidc_pending_required")
    proof = pending(client, purpose="phone_migration")
    error(client.post(PREFIX + "/complete", json=CONFIRM, headers=proof), 401, "oidc_pending_required")
    migrated = client.post(PREFIX + "/complete", json={**CONFIRM, "confirmPhoneMigration": True}, headers=proof)
    assert migrated.status_code == 200, migrated.text
    assert migrated.json()["user"]["id"] == user
    assert db.conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0] == 1
    assert db.conn.execute("SELECT passwordEnabled FROM accounts WHERE userId=?", (user,)).fetchone()[0] == 0
    assert db.conn.execute("SELECT COUNT(*) FROM sessions WHERE userId=?", (user,)).fetchone()[0] == 1
    headers = {"Authorization": "Bearer " + migrated.json()["accessToken"]}
    messages = client.get("/api/v1/conversations/" + str(chat.conversation.id) + "/messages", headers=headers)
    assert [item["content"] for item in messages.json()["items"]] == ["手机旧历史", "保留这段回复"]
    assert client.get("/api/v1/energy", headers=headers).json()["balance"] == wallet.balance
    for token, kind in [("old-phone-only-bearer", "bearer"), ("old-phone-only-browser", "browser")]:
        with pytest.raises(APIError):
            db.authenticate(token, kind=kind)
    assert db.conn.execute("PRAGMA foreign_key_check").fetchall() == []


def test_admin_revocation_invalidates_ordinary_session_and_pending_proof(client):
    session = complete(client)
    user = session["user"]["id"]
    proof = pending(client)
    db = client.app.state.db
    AdminRepository(db).revoke_user_sessions(user)
    error(client.get("/api/v1/account", headers={"Authorization": "Bearer " + session["accessToken"]}),
          401, "unauthorized")
    error(client.post(PREFIX + "/complete", json=CONFIRM, headers=proof), 401, "oidc_session_required")
    assert db.conn.execute("SELECT revokedRevision FROM oidc_user_revocations WHERE userId=?", (user,)).fetchone()[0] == 1
    assert db.conn.execute("SELECT COUNT(*) FROM oidc_identities").fetchone()[0] == 1


def test_exact_browser_and_both_csrf_required_for_fresh_erasure(client):
    session = complete(client)
    ordinary = remembered(client, session)
    oidc = client.get(PREFIX + "/session").json()["csrfToken"]
    headers = {"Origin": ORIGIN, "X-Allira-OIDC-CSRF": oidc}
    error(client.post(PREFIX + "/start", json={"purpose": "erase"}, headers=headers), 403, "csrf_forbidden")
    error(client.post(PREFIX + "/start", json={"purpose": "erase"}, headers={
        **headers, "X-CSRF-Token": ordinary, "Authorization": "invalid",
    }), 401, "browser_required")
    proof = pending(client, purpose="erase", ordinary_csrf=ordinary)
    error(client.post(PREFIX + "/complete", json=CONFIRM, headers=proof), 401, "oidc_pending_required")
    error(client.post(PREFIX + "/erase", json={"confirmation": "DELETE MY ACCOUNT"},
                      headers={key: value for key, value in proof.items() if key != "X-CSRF-Token"}),
          403, "csrf_forbidden")
    response = client.post(PREFIX + "/erase", json={"confirmation": "DELETE MY ACCOUNT"}, headers=proof)
    assert response.status_code == 200, response.text
    db = client.app.state.db
    assert db.conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0] == 0
    assert db.conn.execute("SELECT COUNT(*) FROM oidc_identities").fetchone()[0] == 0
    assert db.conn.execute("PRAGMA foreign_key_check").fetchall() == []
    error(client.get("/api/v1/account"), 401, "unauthorized")


def test_restore_and_restart_remove_proofs_but_keep_external_mappings(client, tmp_path):
    session = complete(client)
    pending(client)
    db, user = client.app.state.db, session["user"]["id"]
    output = tmp_path / "backups"
    output.mkdir(mode=0o700)
    made = create_backup(Path(db.settings.database_path), output)
    backup = output / made["backupName"]
    target = tmp_path / "offline.sqlite3"
    restore_backup(backup, target)
    with closing(sqlite3.connect(target)) as restored:
        for table in TRANSIENT:
            assert restored.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0] == 0
        assert restored.execute("SELECT userId FROM oidc_identities").fetchone()[0] == user
        assert restored.execute("SELECT phone FROM phone_phones").fetchone()[0] == PHONE
    clear_transient_identity_state(db)
    assert db.conn.execute("SELECT COUNT(*) FROM oidc_browsers").fetchone()[0] == 0
    assert db.conn.execute("SELECT COUNT(*) FROM oidc_states").fetchone()[0] == 0
    assert db.conn.execute("SELECT COUNT(*) FROM oidc_pending").fetchone()[0] == 0
    assert db.conn.execute("SELECT COUNT(*) FROM oidc_identities").fetchone()[0] == 1


@pytest.mark.parametrize("change", [
    {"oidc_client_id": ""}, {"oidc_client_secret": ""}, {"oidc_redirect_uri": ""},
    {"oidc_issuer_url": "http://example.invalid/oidc"},
    {"oidc_issuer_url": "https://user:password@example.invalid/oidc"},
    {"oidc_redirect_uri": "http://127.0.0.1:8000/wrong"},
    {"social_mode": "test"}, {"sms_provider": "test-file"},
    {"provider": "openai", "upstream_model": "synthetic-model", "oidc_simulated": True},
])
def test_fixed_oidc_configuration_fails_closed(change):
    values = {"auth_backend": "oidc", "oidc_issuer_url": ISSUER, "oidc_client_id": "synthetic-client",
                  "oidc_client_secret": "synthetic-confidential-secret", "oidc_redirect_uri": ORIGIN + PREFIX + "/callback"}
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{**values, **change})


def test_no_configuration_exposes_disabled_not_a_local_fallback(tmp_path, monkeypatch):
    monkeypatch.delenv("ALLIRA_AUTH_BACKEND")
    settings = Settings(_env_file=None, database_path=str(tmp_path / "disabled.sqlite3"))
    assert settings.auth_backend == "oidc"
    with TestClient(create_app(settings), base_url=ORIGIN, client=("127.0.0.1", 1)) as active:
        body = active.get("/api/v1/auth/options").json()
        assert body["oidc"]["enabled"] is False and body["oidc"]["authorizationOrigin"] is None
        error(active.get(PREFIX + "/session"), 503, "oidc_unavailable")
        error(active.post("/api/v1/auth/login", json={}), 403, "oidc_auth_required")


def test_schema6_addition_preserves_legacy_data_without_authenticating_it(tmp_path, monkeypatch):
    monkeypatch.setattr(accounts, "_derive_password", cheap_password)
    path = tmp_path / "old-schema6.sqlite3"
    legacy = Settings(_env_file=None, database_path=str(path), auth_backend="legacy")
    original = Database(str(path), legacy)
    salt = secrets.token_bytes(16)
    body = AccountRegister(username="historical_owner", password=PASSWORD,
                           acceptedTermsVersion="2026-11-19", isAdult=True)
    account = _Accounts(original).register(body, salt, cheap_password(body.password, salt), "old-token", 3600)
    snapshot = _Accounts(original).require(account.user.id)
    original.close()
    with closing(sqlite3.connect(path)) as connection:
        connection.executescript("""
            DROP TABLE oidc_pending; DROP TABLE oidc_states; DROP TABLE oidc_browsers;
            DROP TABLE oidc_user_revocations; DROP TABLE oidc_revocation_clock; DROP TABLE oidc_identities;
            PRAGMA user_version=6;
        """)
        connection.commit()
    current = Database(str(path), Settings(_env_file=None, database_path=str(path), auth_backend="oidc"))
    try:
        row = _Accounts(current).require(account.user.id)
        assert row["passwordHash"] == snapshot["passwordHash"] and row["passwordSalt"] == snapshot["passwordSalt"]
        assert current.conn.execute("PRAGMA user_version").fetchone()[0] == 8
        with pytest.raises(APIError) as raised:
            current.authenticate("old-token")
        assert raised.value.code == "oidc_login_required"
        assert current.conn.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        current.close()
