"""Isolated account HTTP/SQLite tests; no server process or external upstream is used."""

import asyncio
import hashlib
import hmac
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Annotated
from uuid import UUID

import pytest
from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.testclient import TestClient
from pydantic import ValidationError

from allira import accounts
from allira.account_security import initialize_account_security
from allira.accounts import (
    AccountLogin,
    AccountPassword,
    AccountRegister,
    AccountSession,
    AccountUpdate,
    AccountUser,
    accounts_router,
    initialize_accounts,
)
from allira.admin import COOKIE_NAME, admin_router
from allira.admin_db import AdminRepository
from allira.config import Settings
from allira.db import APIError, Database
from allira.schemas import ConversationInput

PASSWORD = "correct-horse-123"
NEW_PASSWORD = "a-different-password-456"
ORIGIN = "http://127.0.0.1:8000"


def make_app(path=":memory:", *, enabled=True):
    # This fixture intentionally does not import/create the shared application: root
    # integrates these routes separately. Real Settings validation stays root-owned.
    settings = SimpleNamespace(
        database_path=path, registration_enabled=enabled, session_ttl_seconds=300,
        environment="development", legacy_registration_enabled=True,
        admin_session_ttl_seconds=300, admin_auth_backend="link", admin_public_origin="",
    )

    @asynccontextmanager
    async def lifespan(app):
        db = Database(settings.database_path, Settings(_env_file=None, allow_dev_auth=True))
        initialize_accounts(db)
        initialize_account_security(db)
        app.state.db = db
        try:
            yield
        finally:
            db.close()

    app = FastAPI(lifespan=lifespan)

    def error(status, code, message):
        return JSONResponse({"error": {"code": code, "message": message}}, status_code=status)

    @app.exception_handler(APIError)
    async def api_error(request, exc):
        return error(exc.status, exc.code, exc.message)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        return error(422, "validation_error", "Request parameters are invalid.")

    @app.exception_handler(Exception)
    async def unexpected_error(request, exc):
        return error(500, "internal_error", "An internal error occurred.")

    bearer = HTTPBearer(auto_error=False)

    async def current_user(
        request: Request,
        credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    ) -> str:
        if credentials is None or credentials.scheme.lower() != "bearer":
            raise APIError(401, "unauthorized", "A valid session is required.")
        user = request.app.state.db.authenticate(credentials.credentials)
        request.state.session_token = credentials.credentials
        return user

    app.include_router(accounts_router(settings, current_user))
    app.include_router(admin_router(settings))
    return app


@pytest.fixture
def client():
    with TestClient(
        make_app(), base_url=ORIGIN, client=("127.0.0.1", 1234),
        raise_server_exceptions=False,
    ) as client:
        yield client


def register_body(username="Alice_123", **changes):
    return {
        "username": username, "password": PASSWORD,
        "acceptedTermsVersion": "2026-11-19", "isAdult": True, **changes,
    }


def register(client, username="Alice_123", **changes):
    response = client.post("/api/v1/auth/register", json=register_body(username, **changes))
    assert response.status_code == 201, response.text
    return response.json()


def authorization(session):
    return {"Authorization": f"Bearer {session['accessToken']}"}


def login(client, username="Alice_123", password=PASSWORD):
    return client.post("/api/v1/auth/login", json={"username": username, "password": password})


def assert_error(response, status, code):
    assert response.status_code == status, response.text
    assert set(response.json()) == {"error"}
    assert set(response.json()["error"]) == {"code", "message"}
    assert response.json()["error"]["code"] == code


def test_register_login_user_and_session_contract(client):
    session = register(client, displayName="  爱丽丝  ")
    assert set(session) == {"accessToken", "tokenType", "user"}
    assert session["tokenType"] == "Bearer"
    user = session["user"]
    assert set(user) == {"id", "username", "displayName", "createdAt", "termsVersion", "isAdult"}
    assert user["id"].startswith("acct_") and len(user["id"]) == 37
    assert UUID(hex=user["id"][5:]).version == 4
    assert user["username"] == "alice_123" and user["displayName"] == "爱丽丝"
    assert user["termsVersion"] == "2026-11-19" and user["isAdult"] is True
    assert user["createdAt"].endswith("Z")
    assert client.get("/api/v1/account", headers=authorization(session)).json() == user
    response = login(client, "ALICE_123")
    assert response.status_code == 200 and response.json()["user"] == user
    assert response.json()["accessToken"] != session["accessToken"]
    db = client.app.state.db
    assert db.authenticate(response.json()["accessToken"]) == user["id"]
    assert dict(db.conn.execute("SELECT * FROM users").fetchone()) == {
        "id": user["id"], "createdAt": user["createdAt"],
    }
    expiries = [row[0] for row in db.conn.execute("SELECT expiresAt FROM sessions")]
    assert len(expiries) == 2 and max(expiries) - min(expiries) < 30


def test_lowercase_duplicate_is_409_and_rolls_back_new_user(client):
    original = register(client)
    duplicate = client.post("/api/v1/auth/register", json=register_body("aLiCe_123"))
    assert_error(duplicate, 409, "username_taken")
    db = client.app.state.db
    for table in ("users", "accounts", "sessions"):
        assert db.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 1
    assert client.get("/api/v1/account", headers=authorization(original)).status_code == 200


def test_credentials_use_scrypt_individual_salts_and_session_token_hash(client, caplog):
    first, second = register(client), register(client, "second_user")
    db = client.app.state.db
    rows = [dict(row) for row in db.conn.execute("SELECT * FROM accounts ORDER BY username")]
    assert rows[0]["passwordSalt"] != rows[1]["passwordSalt"]
    assert rows[0]["passwordHash"] != rows[1]["passwordHash"]
    for row in rows:
        assert len(row["passwordSalt"]) == 16 and len(row["passwordHash"]) == 64
        expected = hashlib.scrypt(
            PASSWORD.encode(), salt=row["passwordSalt"], n=32768, r=8, p=3,
            dklen=64, maxmem=64 * 1024 * 1024,
        )
        assert hmac.compare_digest(row["passwordHash"], expected)
    stored = "\n".join(db.conn.iterdump())
    for value in (PASSWORD, first["accessToken"], second["accessToken"]):
        assert value not in stored and value not in caplog.text
    for session in (first, second):
        hashed = hashlib.sha256(session["accessToken"].encode()).hexdigest()
        assert db.conn.execute("SELECT 1 FROM sessions WHERE tokenHash = ?", (hashed,)).fetchone()
    dto = AccountRegister(**register_body())
    assert PASSWORD not in repr(dto) and PASSWORD not in dto.model_dump_json()
    assert PASSWORD not in repr(AccountLogin(username="alice", password=PASSWORD))
    assert NEW_PASSWORD not in repr(AccountPassword(currentPassword=PASSWORD, newPassword=NEW_PASSWORD))


def test_unknown_user_and_wrong_password_both_scrypt_and_constant_time_compare(client, monkeypatch):
    register(client)
    real_derive, real_compare = accounts._derive_password, hmac.compare_digest
    derived, compared = [], []

    def tracked_derive(password, salt):
        derived.append(salt)
        return real_derive(password, salt)

    def tracked_compare(first, second):
        compared.append((len(first), len(second)))
        return real_compare(first, second)

    monkeypatch.setattr(accounts, "_derive_password", tracked_derive)
    monkeypatch.setattr(accounts.hmac, "compare_digest", tracked_compare)
    wrong = login(client, password="incorrect-password-999")
    missing = login(client, username="unknown_user", password="incorrect-password-999")
    assert_error(wrong, 401, "invalid_credentials")
    assert_error(missing, 401, "invalid_credentials")
    assert wrong.json() == missing.json()
    assert len(derived) == 2 and all(len(salt) == 16 for salt in derived)
    assert compared == [(64, 64), (64, 64)]


@pytest.mark.parametrize("username", ["ab", "a" * 33, "a-b", "a b", " alice", "alice ", "Ａlice", "Kelvin", "ali\nce"])
def test_username_invalid_characters_and_lengths(username):
    with pytest.raises(ValidationError):
        AccountRegister(**register_body(username))


@pytest.mark.parametrize("username", ["ABC", "a" * 32, "0123", "a_b"])
def test_username_valid_boundaries_normalize_ascii(username):
    assert AccountRegister(**register_body(username)).username == username.lower()


@pytest.mark.parametrize("password", ["x" * 9, "x" * 129, "😀" * 65, "a" * 9 + "\ud800", 123456789012, None])
def test_password_invalid_length_encoding_and_utf8_byte_limit(password):
    with pytest.raises(ValidationError):
        AccountRegister(**register_body(password=password))
    with pytest.raises(ValidationError):
        AccountLogin(username="alice", password=password)
    with pytest.raises(ValidationError):
        AccountPassword(currentPassword=PASSWORD, newPassword=password)


@pytest.mark.parametrize("password", ["x" * 10, "x" * 128, "😀" * 64, "  keep-spaces-123  "])
def test_password_valid_boundaries_preserve_exact_value(password):
    body = AccountRegister(**register_body(password=password))
    assert body.password.get_secret_value() == password


@pytest.mark.parametrize("display_name", ["", " " * 20, "x" * 81, "😀" * 61, "a\nb", "a\x00b", "a\u202eb", "a\ud800b"])
def test_display_name_invalid_lengths_bytes_and_control_characters(display_name):
    with pytest.raises(ValidationError):
        AccountUpdate(displayName=display_name)


@pytest.mark.parametrize("changes", [
    {"acceptedTermsVersion": "2026-11-18"}, {"acceptedTermsVersion": None},
    {"isAdult": False}, {"isAdult": "true"}, {"isAdult": 1}, {"isAdult": None},
])
def test_terms_version_and_explicit_strict_adult_confirmation(changes):
    with pytest.raises(ValidationError):
        AccountRegister(**register_body(**changes))


@pytest.mark.parametrize("missing", ["acceptedTermsVersion", "isAdult"])
def test_terms_and_adult_are_required(missing):
    body = register_body()
    del body[missing]
    with pytest.raises(ValidationError):
        AccountRegister(**body)


def test_validation_envelope_never_reflects_credentials_or_extra_fields(client, caplog):
    secret = "never-reflect-this-credential"
    response = client.post(
        "/api/v1/auth/register", json=register_body(password=secret, admin=True),
    )
    assert_error(response, 422, "validation_error")
    assert secret not in response.text and secret not in caplog.text
    assert client.app.state.db.conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0] == 0
    response = client.post("/api/v1/auth/login", json={"username": "alice", "password": "short"})
    assert_error(response, 422, "validation_error")
    assert "short" not in response.text


def test_registration_disabled_does_not_disable_existing_login(tmp_path):
    path = str(tmp_path / "disabled.sqlite3")
    with TestClient(make_app(path), base_url=ORIGIN, client=("127.0.0.1", 1)) as first:
        user = register(first)["user"]
    with TestClient(make_app(path, enabled=False), base_url=ORIGIN, client=("127.0.0.1", 2)) as second:
        response = second.post("/api/v1/auth/register", json=register_body("new_user"))
        assert_error(response, 403, "registration_disabled")
        assert login(second).json()["user"] == user
        assert second.app.state.db.conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0] == 1


def test_profile_update_is_self_only_and_does_not_change_login_name(client):
    first, second = register(client), register(client, "bob_user")
    response = client.post(
        "/api/v1/account/update", headers=authorization(first), json={"displayName": "  新名字  "},
    )
    assert response.status_code == 200
    assert response.json() == {**first["user"], "displayName": "新名字"}
    assert client.get("/api/v1/account", headers=authorization(second)).json() == second["user"]
    forbidden = client.post(
        "/api/v1/account/update", headers=authorization(first),
        json={"displayName": "new", "id": second["user"]["id"]},
    )
    assert_error(forbidden, 422, "validation_error")
    assert login(client).json()["user"]["username"] == "alice_123"


def test_account_identity_does_not_collide_with_existing_developer_username(client):
    db = client.app.state.db
    db.create_session("developer-token", "alice_123", 300)
    developer_chat = db.create_conversation("alice_123", ConversationInput(title="private"))
    session = register(client)
    assert db.authenticate(session["accessToken"]) != "alice_123"
    with pytest.raises(APIError) as error:
        db.conversation(session["user"]["id"], developer_chat.id)
    assert error.value.status == 404
    assert_error(
        client.get("/api/v1/account", headers={"Authorization": "Bearer developer-token"}),
        403, "account_required",
    )
    for method, path, body in (
        ("post", "/api/v1/account/update", {"displayName": "steal"}),
        ("post", "/api/v1/account/password", {"currentPassword": PASSWORD, "newPassword": NEW_PASSWORD}),
        ("post", "/api/v1/account/logout-all", None),
    ):
        assert_error(getattr(client, method)(
            path, headers={"Authorization": "Bearer developer-token"}, json=body,
        ), 403, "account_required")


def test_account_and_administrator_authentication_are_independent(client):
    ordinary = register(client)
    assert_error(
        client.get("/api/v1/admin/session", headers=authorization(ordinary)), 401, "unauthorized",
    )
    code = "one-time-administrator-link-code-12345"
    AdminRepository(client.app.state.db).issue_login(code, 300)
    admin = client.post("/api/v1/admin/session", headers={"Origin": ORIGIN}, json={"code": code})
    assert admin.status_code == 200
    cookie = client.cookies.get(COOKIE_NAME)
    assert cookie
    assert_error(client.get("/api/v1/account"), 401, "unauthorized")
    assert_error(client.get("/api/v1/account", headers={"Authorization": f"Bearer {cookie}"}), 401, "unauthorized")
    response = client.post("/api/v1/account/logout-all", headers=authorization(ordinary))
    assert response.json() == {"ok": True, "revoked": 1}
    assert client.get("/api/v1/admin/session").status_code == 200
    assert client.post("/api/v1/account/update", json={"displayName": "x"}).status_code == 401


def test_password_change_revokes_all_and_only_target_sessions(client):
    first, other = register(client), register(client, "bob_user")
    second = login(client).json()
    db = client.app.state.db
    before = dict(db.conn.execute("SELECT * FROM accounts WHERE userId = ?", (first["user"]["id"],)).fetchone())
    incorrect = client.post(
        "/api/v1/account/password", headers=authorization(first),
        json={"currentPassword": "not-the-right-password", "newPassword": NEW_PASSWORD},
    )
    assert_error(incorrect, 401, "invalid_credentials")
    assert client.get("/api/v1/account", headers=authorization(first)).status_code == 200
    response = client.post(
        "/api/v1/account/password", headers=authorization(first),
        json={"currentPassword": PASSWORD, "newPassword": NEW_PASSWORD},
    )
    assert response.status_code == 200 and response.json() == {"ok": True}
    for session in (first, second):
        assert_error(client.get("/api/v1/account", headers=authorization(session)), 401, "unauthorized")
    assert client.get("/api/v1/account", headers=authorization(other)).status_code == 200
    after = dict(db.conn.execute("SELECT * FROM accounts WHERE userId = ?", (first["user"]["id"],)).fetchone())
    assert before["passwordSalt"] != after["passwordSalt"]
    assert before["passwordHash"] != after["passwordHash"]
    assert_error(login(client), 401, "invalid_credentials")
    assert login(client, password=NEW_PASSWORD).status_code == 200


def test_logout_all_reports_revocations_and_preserves_other_users(client):
    first, other = register(client), register(client, "bob_user")
    second = login(client).json()
    response = client.post("/api/v1/account/logout-all", headers=authorization(first))
    assert response.status_code == 200 and response.json() == {"ok": True, "revoked": 2}
    for session in (first, second):
        assert client.get("/api/v1/account", headers=authorization(session)).status_code == 401
    assert client.get("/api/v1/account", headers=authorization(other)).json() == other["user"]
    assert login(client).status_code == 200


def test_additive_schema_is_idempotent_and_preserves_existing_database(tmp_path):
    path = str(tmp_path / "migration.sqlite3")
    db = Database(path, Settings(_env_file=None, allow_dev_auth=True))
    db.create_session("legacy-token", "legacy-user", 300)
    conversation = db.create_conversation("legacy-user", ConversationInput(title="legacy"))
    with db.atomic():
        # Simulate a pre-accounts database even when root initializes new DBs eagerly.
        db.conn.execute("DROP TABLE IF EXISTS accounts")
        db.conn.execute("CREATE TABLE unrelated_feature (id TEXT PRIMARY KEY)")
        db.conn.execute("INSERT INTO unrelated_feature VALUES ('untouched')")
        db.conn.execute("PRAGMA user_version = 41")
    before = list(db.conn.execute("SELECT name, sql FROM sqlite_master WHERE type = 'table'"))
    initialize_accounts(db)
    initialize_accounts(db)
    assert db.conn.execute("PRAGMA user_version").fetchone()[0] == 41
    for row in before:
        current = db.conn.execute("SELECT sql FROM sqlite_master WHERE name = ?", (row[0],)).fetchone()
        assert current[0] == row[1]
    assert db.authenticate("legacy-token") == "legacy-user"
    assert db.conversation("legacy-user", conversation.id).title == "legacy"
    assert db.conn.execute("SELECT id FROM unrelated_feature").fetchone()[0] == "untouched"
    db.close()
    with TestClient(make_app(path), base_url=ORIGIN, client=("127.0.0.1", 3)) as reopened:
        registered = register(reopened)
        assert registered["user"]["id"] != "legacy-user"
        assert reopened.app.state.db.authenticate("legacy-token") == "legacy-user"


def test_account_and_bearer_sessions_persist_across_restarts(tmp_path):
    path = str(tmp_path / "persistent.sqlite3")
    with TestClient(make_app(path), base_url=ORIGIN, client=("127.0.0.1", 1)) as first:
        session = register(first)
    with TestClient(make_app(path), base_url=ORIGIN, client=("127.0.0.1", 2)) as second:
        assert second.get("/api/v1/account", headers=authorization(session)).json() == session["user"]
        assert login(second).json()["user"] == session["user"]


def test_session_insert_failure_rolls_back_registration_user_and_account(client):
    db = client.app.state.db
    with db.atomic():
        db.conn.execute("""
            CREATE TEMP TRIGGER fail_session_insert BEFORE INSERT ON sessions
            BEGIN SELECT RAISE(ABORT, 'simulated session write failure'); END
        """)
    response = client.post("/api/v1/auth/register", json=register_body())
    assert_error(response, 500, "internal_error")
    for table in ("users", "accounts", "sessions"):
        assert db.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
    with db.atomic():
        db.conn.execute("DROP TRIGGER fail_session_insert")
    assert register(client)["user"]["username"] == "alice_123"


def test_failure_after_nested_create_session_still_rolls_back_entire_registration(client, monkeypatch):
    db = client.app.state.db
    original = db.create_session

    def fail_after_session(*args):
        original(*args)
        raise RuntimeError("simulated failure after session insertion")

    monkeypatch.setattr(db, "create_session", fail_after_session)
    response = client.post("/api/v1/auth/register", json=register_body())
    assert_error(response, 500, "internal_error")
    for table in ("users", "accounts", "sessions"):
        assert db.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0


def test_login_session_insert_failure_does_not_mutate_account_or_existing_sessions(client):
    session = register(client)
    db = client.app.state.db
    account_before = dict(db.conn.execute("SELECT * FROM accounts").fetchone())
    sessions_before = [dict(row) for row in db.conn.execute("SELECT * FROM sessions")]
    with db.atomic():
        db.conn.execute("""
            CREATE TEMP TRIGGER fail_login_session BEFORE INSERT ON sessions
            BEGIN SELECT RAISE(ABORT, 'simulated login session write failure'); END
        """)
    assert_error(login(client), 500, "internal_error")
    assert dict(db.conn.execute("SELECT * FROM accounts").fetchone()) == account_before
    assert [dict(row) for row in db.conn.execute("SELECT * FROM sessions")] == sessions_before
    assert client.get("/api/v1/account", headers=authorization(session)).status_code == 200


def test_failed_password_revocation_rolls_back_new_password(client):
    session = register(client)
    db = client.app.state.db
    before = dict(db.conn.execute("SELECT * FROM accounts").fetchone())
    with db.atomic():
        db.conn.execute("""
            CREATE TEMP TRIGGER fail_revoke BEFORE DELETE ON sessions
            BEGIN SELECT RAISE(ABORT, 'simulated revocation failure'); END
        """)
    response = client.post(
        "/api/v1/account/password", headers=authorization(session),
        json={"currentPassword": PASSWORD, "newPassword": NEW_PASSWORD},
    )
    assert_error(response, 500, "internal_error")
    assert dict(db.conn.execute("SELECT * FROM accounts").fetchone()) == before
    assert client.get("/api/v1/account", headers=authorization(session)).status_code == 200
    assert login(client).status_code == 200
    assert_error(login(client, password=NEW_PASSWORD), 401, "invalid_credentials")


def test_failed_logout_all_rolls_back_every_revocation(client):
    session = register(client)
    second = login(client).json()
    db = client.app.state.db
    with db.atomic():
        db.conn.execute("""
            CREATE TEMP TRIGGER fail_second_revoke BEFORE DELETE ON sessions
            WHEN (SELECT COUNT(*) FROM sessions) = 1
            BEGIN SELECT RAISE(ABORT, 'simulated partial revocation failure'); END
        """)
    assert_error(client.post("/api/v1/account/logout-all", headers=authorization(session)), 500, "internal_error")
    assert db.conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 2
    assert client.get("/api/v1/account", headers=authorization(session)).status_code == 200
    assert client.get("/api/v1/account", headers=authorization(second)).status_code == 200


def test_login_limit_uses_peer_not_forwarding_headers_and_resets_after_window(client, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(accounts, "monotonic", lambda: clock[0])
    monkeypatch.setattr(accounts, "_derive_password", lambda *args: b"x" * 64)
    for attempt in range(10):
        response = client.post(
            "/api/v1/auth/login", json={"username": "unknown", "password": PASSWORD},
            headers={"X-Forwarded-For": f"198.51.100.{attempt + 1}"},
        )
        assert_error(response, 401, "invalid_credentials")
    assert_error(login(client, "unknown"), 429, "rate_limited")
    clock[0] += 59.999
    assert_error(login(client, "unknown"), 429, "rate_limited")
    clock[0] += 0.001
    assert_error(login(client, "unknown"), 401, "invalid_credentials")


def test_registration_limit_counts_validation_failures_separately_from_login(client, monkeypatch):
    monkeypatch.setattr(accounts, "_derive_password", lambda *args: b"x" * 64)
    for attempt in range(5):
        response = client.post(
            "/api/v1/auth/register", json=register_body(f"name_{attempt}", isAdult=False),
        )
        assert_error(response, 422, "validation_error")
    assert_error(client.post("/api/v1/auth/register", json=register_body()), 429, "rate_limited")
    assert_error(login(client, "unknown"), 401, "invalid_credentials")


def test_limit_capacity_is_bounded_fail_closed_and_expired_peers_are_cleaned(monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(accounts, "monotonic", lambda: clock[0])
    monkeypatch.setattr(accounts, "RATE_MAX_ENTRIES", 3)
    limiter = accounts._AttemptLimiter()
    for host in ("one", "two", "three"):
        limiter.check("login", host, 1)
    with pytest.raises(APIError) as full:
        limiter.check("login", "four", 1)
    assert full.value.status == 429 and len(limiter.entries) == 3
    with pytest.raises(APIError):
        limiter.check("login", "one", 1)
    # Entry limits do not evict a live, already blocked host.
    assert ("login", "one") in limiter.entries
    clock[0] += 60
    limiter.check("register", "new", 5)
    assert list(limiter.entries) == [("register", "new")]


def test_sliding_window_does_not_reset_at_fixed_minute_boundary(monkeypatch):
    clock = [59.0]
    monkeypatch.setattr(accounts, "monotonic", lambda: clock[0])
    limiter = accounts._AttemptLimiter()
    for _ in range(10):
        limiter.check("login", "peer", 10)
    clock[0] = 60.0
    with pytest.raises(APIError):
        limiter.check("login", "peer", 10)
    clock[0] = 119.0
    limiter.check("login", "peer", 10)
    assert len(limiter.entries[("login", "peer")]) == 1


def test_rate_state_is_per_router_not_shared_across_apps(monkeypatch):
    monkeypatch.setattr(accounts, "_derive_password", lambda *args: b"x" * 64)
    with TestClient(make_app()) as first, TestClient(make_app()) as second:
        for _ in range(10):
            assert_error(login(first, "unknown"), 401, "invalid_credentials")
        assert_error(login(first, "unknown"), 429, "rate_limited")
        assert_error(login(second, "unknown"), 401, "invalid_credentials")


def test_stale_login_cannot_issue_session_after_password_changes(client, monkeypatch):
    session = register(client)
    entered, release = threading.Event(), threading.Event()
    original = accounts._derive_password

    def blocked(password, salt):
        entered.set()
        if not release.wait(10):
            raise RuntimeError("test did not release password worker")
        return original(password, salt)

    monkeypatch.setattr(accounts, "_derive_password", blocked)
    db = client.app.state.db
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(login, client)
        try:
            assert entered.wait(5)
            with db.atomic():
                db.conn.execute("UPDATE accounts SET passwordHash = ?", (b"z" * 64,))
                db.conn.execute("DELETE FROM sessions WHERE userId = ?", (session["user"]["id"],))
        finally:
            release.set()
        assert_error(pending.result(timeout=10), 401, "invalid_credentials")
    assert db.conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0


def test_logout_while_password_worker_awaits_cannot_update_password(client, monkeypatch):
    session = register(client)
    entered, release = threading.Event(), threading.Event()
    original = accounts._derive_password

    def blocked(password, salt):
        entered.set()
        if not release.wait(10):
            raise RuntimeError("test did not release password worker")
        return original(password, salt)

    monkeypatch.setattr(accounts, "_derive_password", blocked)
    db = client.app.state.db
    before = dict(db.conn.execute("SELECT * FROM accounts").fetchone())
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(
            client.post, "/api/v1/account/password", headers=authorization(session),
            json={"currentPassword": PASSWORD, "newPassword": NEW_PASSWORD},
        )
        try:
            assert entered.wait(5)
            response = client.post("/api/v1/account/logout-all", headers=authorization(session))
            assert response.json() == {"ok": True, "revoked": 1}
        finally:
            release.set()
        assert_error(pending.result(timeout=10), 401, "unauthorized")
    assert dict(db.conn.execute("SELECT * FROM accounts").fetchone()) == before


def test_password_workers_bound_concurrency_and_keep_slot_until_cancelled_job_finishes():
    async def scenario():
        workers = accounts._PasswordWorkers()
        entered, release = threading.Event(), threading.Event()

        def blocked():
            entered.set()
            if not release.wait(10):
                raise RuntimeError("test did not release worker")
            return b"done"

        first = asyncio.create_task(workers.run(blocked))
        second = None
        try:
            assert await asyncio.to_thread(entered.wait, 5)
            first.cancel()
            with pytest.raises(asyncio.CancelledError):
                await first
            assert workers.active == 1
            second = asyncio.create_task(workers.run(blocked))
            # Wait for task scheduling without waiting for password computation.
            await asyncio.sleep(0)
            assert workers.active == 2
            with pytest.raises(APIError) as saturated:
                await workers.run(lambda: b"third")
            assert saturated.value.status == 429
        finally:
            release.set()
            if second is not None:
                assert await asyncio.wait_for(second, 5) == b"done"
        for _ in range(100):
            if workers.active == 0:
                break
            await asyncio.sleep(0.001)
        assert workers.active == 0
        assert await workers.run(lambda: b"next") == b"next"

    asyncio.run(scenario())


def test_accounts_openapi_uses_exact_wire_shapes_and_password_only_fields(client):
    spec = client.get("/openapi.json").json()
    paths, schemas = spec["paths"], spec["components"]["schemas"]
    assert set(paths) >= {
        "/api/v1/auth/register", "/api/v1/auth/login", "/api/v1/account",
        "/api/v1/account/update", "/api/v1/account/password", "/api/v1/account/logout-all",
    }
    assert paths["/api/v1/auth/register"]["post"]["responses"]["201"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/AccountSession",
    }
    assert set(schemas["AccountUser"]["properties"]) == {
        "id", "username", "displayName", "createdAt", "termsVersion", "isAdult",
    }
    assert schemas["AccountRegister"]["properties"]["password"]["format"] == "password"
    assert schemas["AccountRegister"]["properties"]["password"]["writeOnly"] is True
    assert schemas["AccountRegister"]["properties"]["password"]["maxLength"] == 128
    assert schemas["AccountRegister"]["additionalProperties"] is False
    assert AccountUser.model_fields["id"].annotation is str
    assert AccountSession.model_fields["user"].annotation is AccountUser
    assert Settings(_env_file=None).session_ttl_seconds >= 60


def test_sqlite_constraints_reject_unconfirmed_accounts_without_partial_rows():
    db = Database(":memory:")
    try:
        initialize_accounts(db)
        with pytest.raises(sqlite3.IntegrityError), db.atomic():
            user = "acct_" + "a" * 32
            db.conn.execute("INSERT INTO users VALUES (?, 'stamp')", (user,))
            db.conn.execute(
                "INSERT INTO accounts (userId,username,displayName,passwordSalt,passwordHash,"
                "createdAt,termsVersion,isAdult) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (user, "valid_name", "name", b"s" * 16, b"h" * 64, "stamp", "2026-11-19", 0),
            )
        assert db.conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0
        assert db.conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0] == 0
    finally:
        db.close()
