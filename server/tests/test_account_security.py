"""Isolated recovery/session tests: no listener, live database, environment or upstream.

Most behavioral tests use a deterministic test-only password derivation to keep fault
and race coverage cheap. The real-scrypt test and the existing account suite exercise
the production password primitive. Recovery-code randomness/hashing remain real.
"""

import hashlib
import hmac
import json
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Annotated
from uuid import UUID, uuid4

import pytest
from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.testclient import TestClient

from allira import account_security, accounts
from allira.account_security import (
    AccountRecover,
    AccountRecoveryCodes,
    AccountRecoveryRotate,
    initialize_account_security,
)
from allira.accounts import accounts_router, initialize_accounts
from allira.config import Settings
from allira.db import APIError, Database

PASSWORD = "test-current-password-123"
NEW_PASSWORD = "test-replacement-password-456"
ORIGIN = "http://127.0.0.1:8000"


def make_app(path=":memory:", *, enabled=True):
    settings = SimpleNamespace(
        registration_enabled=enabled, session_ttl_seconds=300,
        environment="development", legacy_registration_enabled=True,
    )

    @asynccontextmanager
    async def lifespan(app):
        db = Database(path, Settings(_env_file=None, allow_dev_auth=True))
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
        # Test-only dependency simulates root's trusted browser-cookie authenticator.
        # Production does not accept this header; the module only trusts state values.
        kind = request.headers.get("X-Test-Session-Kind", "bearer")
        user = request.app.state.db.authenticate(credentials.credentials, kind=kind)
        request.state.session_token = credentials.credentials
        request.state.auth_kind = kind
        return user

    app.include_router(accounts_router(settings, current_user))
    return app


@pytest.fixture
def fast_hash(monkeypatch):
    def derive(password, salt):
        return hashlib.sha512(salt + password.get_secret_value().encode()).digest()

    monkeypatch.setattr(accounts, "_derive_password", derive)
    return derive


@pytest.fixture
def client(fast_hash):
    with TestClient(
        make_app(), base_url=ORIGIN, client=("127.0.0.1", 1234), raise_server_exceptions=False,
    ) as client:
        yield client


def register(client, username="Alice_123"):
    response = client.post("/api/v1/auth/register", json={
        "username": username, "password": PASSWORD,
        "acceptedTermsVersion": "2026-11-19", "isAdult": True,
    })
    assert response.status_code == 201
    return response.json()


def headers(session, *, kind="bearer"):
    result = {"Authorization": "Bearer " + session["accessToken"]}
    if kind != "bearer":
        result["X-Test-Session-Kind"] = kind
    return result


def rotate(client, session, password=PASSWORD):
    return client.post("/api/v1/account/recovery", headers=headers(session), json={
        "currentPassword": password,
    })


def issue(client, session):
    response = rotate(client, session)
    assert response.status_code == 200
    return response.json()


def recover(client, code, *, username="Alice_123", password=NEW_PASSWORD):
    return client.post("/api/v1/auth/recover", content=json.dumps({
        "username": username, "recoveryCode": code, "newPassword": password,
    }, ensure_ascii=True), headers={"Content-Type": "application/json"})


def login(client, username="Alice_123", password=PASSWORD):
    return client.post("/api/v1/auth/login", json={"username": username, "password": password})


def error(response, status, code):
    assert response.status_code == status
    assert response.json()["error"]["code"] == code
    return response.json()


def snapshot(db):
    return {
        table: [tuple(row) for row in db.conn.execute(f"SELECT * FROM {table} ORDER BY 1")]
        for table in ("accounts", "sessions", "account_recovery_state", "account_recovery_codes")
    }


def test_initial_status_and_rotation_are_hash_only_once(client, caplog):
    session = register(client)
    auth = headers(session)
    assert client.get("/api/v1/account/recovery", headers=auth).json() == {
        "enabled": False, "remainingCodes": 0, "updatedAt": None,
    }
    codes = issue(client, session)
    assert set(codes) == {"codes", "createdAt"}
    assert len(codes["codes"]) == len(set(codes["codes"])) == 8
    assert all(re.fullmatch(r"[A-Za-z0-9_-]{32}", code) for code in codes["codes"])
    rows = [dict(row) for row in client.app.state.db.conn.execute(
        "SELECT * FROM account_recovery_codes",
    )]
    assert {row["codeHash"] for row in rows} == {
        hashlib.sha256(code.encode()).hexdigest() for code in codes["codes"]
    }
    status = client.get("/api/v1/account/recovery", headers=auth).json()
    assert status == {"enabled": True, "remainingCodes": 8, "updatedAt": codes["createdAt"]}
    assert all(code not in str(rows) + str(status) + caplog.text for code in codes["codes"])
    assert codes["codes"][0] not in repr(AccountRecoveryCodes(**codes))
    assert PASSWORD not in repr(AccountRecoveryRotate(currentPassword=PASSWORD))
    assert codes["codes"][0] not in repr(AccountRecover(
        username="alice_123", recoveryCode=codes["codes"][0], newPassword=NEW_PASSWORD,
    ))


def test_recovery_uses_real_scrypt_and_rotates_salt_without_logging(caplog):
    with TestClient(make_app(), base_url=ORIGIN, client=("127.0.0.1", 1)) as client:
        session = register(client)
        db = client.app.state.db
        before = dict(db.conn.execute("SELECT * FROM accounts").fetchone())
        codes = issue(client, session)
        assert recover(client, codes["codes"][0]).json() == {"ok": True}
        after = dict(db.conn.execute("SELECT * FROM accounts").fetchone())
        assert before["passwordSalt"] != after["passwordSalt"]
        assert before["passwordHash"] != after["passwordHash"]
        assert after["passwordHash"] == accounts._derive_password(
            accounts.AccountLogin(username="alice_123", password=NEW_PASSWORD).password,
            after["passwordSalt"],
        )
        assert not any(secret in caplog.text for secret in (
            PASSWORD, NEW_PASSWORD, session["accessToken"], *codes["codes"],
        ))


def test_rotation_invalidates_previous_codes_but_not_sessions(client):
    session = register(client)
    first, second = issue(client, session), issue(client, session)
    assert set(first["codes"]).isdisjoint(second["codes"])
    error(recover(client, first["codes"][0]), 401, "invalid_credentials")
    assert client.get("/api/v1/account", headers=headers(session)).status_code == 200
    assert recover(client, second["codes"][0]).json() == {"ok": True}


def test_recover_normalizes_username_revokes_all_types_and_only_target(client):
    alice, bob = register(client), register(client, "bob_123")
    second = login(client).json()
    db = client.app.state.db
    db.create_session("test-only-browser-session", alice["user"]["id"], 300, kind="browser")
    codes = issue(client, alice)
    assert recover(client, codes["codes"][3], username="ALICE_123").json() == {"ok": True}
    assert db.conn.execute("SELECT COUNT(*) FROM sessions WHERE userId = ?", (
        alice["user"]["id"],
    )).fetchone()[0] == 0
    for session in (alice, second):
        error(client.get("/api/v1/account", headers=headers(session)), 401, "unauthorized")
    assert client.get("/api/v1/account", headers=headers(bob)).status_code == 200
    error(login(client), 401, "invalid_credentials")
    fresh = login(client, password=NEW_PASSWORD)
    assert fresh.status_code == 200
    status = client.get("/api/v1/account/recovery", headers=headers(fresh.json())).json()
    assert status == {"enabled": False, "remainingCodes": 0, "updatedAt": codes["createdAt"]}
    assert db.conn.execute("SELECT COUNT(*) FROM account_recovery_codes").fetchone()[0] == 0
    for code in codes["codes"]:
        error(recover(client, code), 401, "invalid_credentials")


def test_bad_code_and_missing_account_do_same_bounded_hash_work(client, monkeypatch, fast_hash):
    alice, bob = register(client), register(client, "bob_123")
    codes = issue(client, alice)
    calls, compares = [], []
    real_compare = hmac.compare_digest

    def tracked(password, salt):
        calls.append(1)
        return fast_hash(password, salt)

    def compare(left, right):
        compares.append((len(left), len(right)))
        return real_compare(left, right)

    monkeypatch.setattr(accounts, "_derive_password", tracked)
    monkeypatch.setattr(account_security.hmac, "compare_digest", compare)
    responses = [
        recover(client, "not-a-real-code", username="missing_account"),
        recover(client, "not-a-real-code"),
        recover(client, codes["codes"][0], username=bob["user"]["username"]),
    ]
    assert all(error(response, 401, "invalid_credentials") == responses[0].json()
               for response in responses)
    assert len(calls) == 3
    assert compares == [(64, 64)] * 24
    assert client.app.state.db.conn.execute("SELECT COUNT(*) FROM account_recovery_codes").fetchone()[0] == 8


def test_unconfirmed_rotation_has_no_side_effects_and_password_change_invalidates_codes(client):
    session = register(client)
    codes = issue(client, session)
    before = snapshot(client.app.state.db)
    error(rotate(client, session, "incorrect-password-123"), 401, "invalid_credentials")
    assert snapshot(client.app.state.db) == before
    response = client.post("/api/v1/account/password", headers=headers(session), json={
        "currentPassword": PASSWORD, "newPassword": NEW_PASSWORD,
    })
    assert response.json() == {"ok": True}
    error(recover(client, codes["codes"][0]), 401, "invalid_credentials")
    fresh = login(client, password=NEW_PASSWORD).json()
    assert client.get("/api/v1/account/recovery", headers=headers(fresh)).json() == {
        "enabled": False, "remainingCodes": 0, "updatedAt": codes["createdAt"],
    }


@pytest.mark.parametrize("value", [None, 123, [], "", "x" * 129, "非ASCII恢复码", "\ud800"])
def test_recovery_code_input_is_bounded_generic_and_secret(client, value):
    response = recover(client, value)
    assert error(response, 422, "validation_error") == {
        "error": {"code": "validation_error", "message": "Request parameters are invalid."},
    }
    assert "recoveryCode" not in response.text


@pytest.mark.parametrize("value", ["short", "x" * 129, "😀" * 65, "\ud800" * 10])
def test_recovery_new_password_reuses_character_and_utf8_limits(client, value):
    error(recover(client, "not-a-code", password=value), 422, "validation_error")


def test_recovery_is_available_when_registration_is_disabled(tmp_path, fast_hash):
    path = str(tmp_path / "accounts.sqlite3")
    with TestClient(make_app(path), base_url=ORIGIN) as first:
        session = register(first)
        code = issue(first, session)["codes"][0]
    with TestClient(make_app(path, enabled=False), base_url=ORIGIN) as second:
        assert recover(second, code).json() == {"ok": True}
        assert login(second, password=NEW_PASSWORD).status_code == 200


def test_rotation_insert_failure_restores_previous_codes_and_timestamp(client):
    session = register(client)
    codes = issue(client, session)
    db = client.app.state.db
    before = snapshot(db)
    with db.atomic():
        db.conn.execute("""
            CREATE TEMP TRIGGER fail_rotation BEFORE INSERT ON account_recovery_codes
            WHEN (SELECT COUNT(*) FROM account_recovery_codes) = 2
            BEGIN SELECT RAISE(ABORT, 'synthetic rotation failure'); END
        """)
    error(rotate(client, session), 500, "internal_error")
    assert snapshot(db) == before
    with db.atomic():
        db.conn.execute("DROP TRIGGER fail_rotation")
    assert recover(client, codes["codes"][0]).json() == {"ok": True}


@pytest.mark.parametrize("table,event", [
    ("accounts", "UPDATE"), ("sessions", "DELETE"), ("account_recovery_codes", "DELETE"),
])
def test_failed_recovery_does_not_consume_code_or_partially_revoke(client, table, event):
    session = register(client)
    codes = issue(client, session)
    db = client.app.state.db
    before = snapshot(db)
    with db.atomic():
        db.conn.execute(f"""
            CREATE TEMP TRIGGER fail_recover BEFORE {event} ON {table}
            BEGIN SELECT RAISE(ABORT, 'synthetic recovery failure'); END
        """)
    error(recover(client, codes["codes"][0]), 500, "internal_error")
    assert snapshot(db) == before
    assert not db.conn.in_transaction
    with db.atomic():
        db.conn.execute("DROP TRIGGER fail_recover")
    assert recover(client, codes["codes"][0]).json() == {"ok": True}


def test_rotation_rechecks_revocation_after_password_worker_awaits(client, monkeypatch, fast_hash):
    session = register(client)
    entered, release = threading.Event(), threading.Event()

    def blocked(password, salt):
        entered.set()
        if not release.wait(10):
            raise RuntimeError("test worker was not released")
        return fast_hash(password, salt)

    monkeypatch.setattr(accounts, "_derive_password", blocked)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(rotate, client, session)
        try:
            assert entered.wait(5)
            assert client.post("/api/v1/account/logout-all", headers=headers(session)).status_code == 200
        finally:
            release.set()
        error(pending.result(timeout=10), 401, "unauthorized")
    assert client.app.state.db.conn.execute("SELECT COUNT(*) FROM account_recovery_codes").fetchone()[0] == 0


@pytest.mark.parametrize("mutation", ["rotation", "password"])
def test_recovery_rechecks_code_and_credentials_after_worker_awaits(client, monkeypatch, fast_hash, mutation):
    session = register(client)
    codes = issue(client, session)
    entered, release = threading.Event(), threading.Event()

    def blocked(password, salt):
        if password.get_secret_value() == NEW_PASSWORD:
            entered.set()
            if not release.wait(10):
                raise RuntimeError("test worker was not released")
        return fast_hash(password, salt)

    monkeypatch.setattr(accounts, "_derive_password", blocked)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(recover, client, codes["codes"][0])
        try:
            assert entered.wait(5)
            if mutation == "rotation":
                issue(client, session)
            else:
                changed = client.post("/api/v1/account/password", headers=headers(session), json={
                    "currentPassword": PASSWORD, "newPassword": "different-valid-password-789",
                })
                assert changed.status_code == 200
        finally:
            release.set()
        error(pending.result(timeout=10), 401, "invalid_credentials")
    assert login(client, password=PASSWORD if mutation == "rotation" else "different-valid-password-789").status_code == 200


@pytest.mark.parametrize("mutation", ["password", "delete"])
def test_rotation_rechecks_credential_snapshot_or_deleted_account(client, monkeypatch, fast_hash, mutation):
    session = register(client)
    entered, release = threading.Event(), threading.Event()

    def blocked(password, salt):
        entered.set()
        if not release.wait(10):
            raise RuntimeError("test worker was not released")
        return fast_hash(password, salt)

    monkeypatch.setattr(accounts, "_derive_password", blocked)
    db = client.app.state.db
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(rotate, client, session)
        try:
            assert entered.wait(5)
            with db.atomic():
                if mutation == "password":
                    db.conn.execute("UPDATE accounts SET passwordHash = ?", (b"z" * 64,))
                else:
                    db.conn.execute("DELETE FROM accounts")
        finally:
            release.set()
        error(pending.result(timeout=10), 401, "invalid_credentials")
    assert db.conn.execute("SELECT COUNT(*) FROM account_recovery_codes").fetchone()[0] == 0


def test_password_change_code_invalidation_failure_rolls_back_everything(client):
    session = register(client)
    issue(client, session)
    db = client.app.state.db
    before = snapshot(db)
    with db.atomic():
        db.conn.execute("""
            CREATE TEMP TRIGGER fail_invalidate BEFORE DELETE ON account_recovery_codes
            BEGIN SELECT RAISE(ABORT, 'synthetic code invalidation failure'); END
        """)
    error(client.post("/api/v1/account/password", headers=headers(session), json={
        "currentPassword": PASSWORD, "newPassword": NEW_PASSWORD,
    }), 500, "internal_error")
    assert snapshot(db) == before
    assert not db.conn.in_transaction


def test_concurrent_recovery_is_one_use_even_with_two_workers(client, monkeypatch, fast_hash):
    session = register(client)
    codes = issue(client, session)
    entered, release = threading.Event(), threading.Event()
    lock, count = threading.Lock(), 0

    def blocked(password, salt):
        nonlocal count
        with lock:
            count += 1
            if count == 2:
                entered.set()
        if not release.wait(10):
            raise RuntimeError("test worker was not released")
        return fast_hash(password, salt)

    monkeypatch.setattr(accounts, "_derive_password", blocked)
    with ThreadPoolExecutor(max_workers=2) as pool:
        pending = [pool.submit(recover, client, codes["codes"][0]) for _ in range(2)]
        try:
            assert entered.wait(5)
        finally:
            release.set()
        responses = [task.result(timeout=10) for task in pending]
    assert sorted(response.status_code for response in responses) == [200, 401]
    assert next(response for response in responses if response.status_code == 200).json() == {"ok": True}
    error(next(response for response in responses if response.status_code == 401), 401, "invalid_credentials")
    assert client.app.state.db.conn.execute("SELECT COUNT(*) FROM account_recovery_codes").fetchone()[0] == 0


def test_sensitive_rotation_and_password_attempts_share_peer_and_account_window(client, monkeypatch):
    monkeypatch.setattr(accounts, "monotonic", lambda: 1000)
    session = register(client)
    for _ in range(5):
        error(rotate(client, session, "incorrect-password-123"), 401, "invalid_credentials")
        error(client.post("/api/v1/account/password", headers=headers(session), json={
            "currentPassword": "incorrect-password-123", "newPassword": NEW_PASSWORD,
        }), 401, "invalid_credentials")
    error(rotate(client, session), 429, "rate_limited")
    tools = client.app.state.account_auth_tools
    assert len(tools.attempts.entries[("sensitive_user", session["user"]["id"])]) == 10
    assert len(tools.attempts.entries[("sensitive_peer", "127.0.0.1")]) == 10
    monkeypatch.setattr(accounts, "monotonic", lambda: 1060)
    assert rotate(client, session).status_code == 200


def test_sensitive_account_limit_cannot_be_reset_by_new_peer_and_forwarded_headers(client, monkeypatch):
    monkeypatch.setattr(accounts, "monotonic", lambda: 1000)
    session = register(client)
    for index in range(10):
        peer = TestClient(client.app, base_url=ORIGIN, client=(f"127.0.0.{index + 2}", 1))
        response = peer.post("/api/v1/account/recovery", headers={
            **headers(session), "X-Forwarded-For": f"192.0.2.{index}",
        }, json={"currentPassword": "incorrect-password-123"})
        error(response, 401, "invalid_credentials")
        peer.close()
    error(rotate(client, session), 429, "rate_limited")
    assert not any("192.0.2" in key[1] for key in client.app.state.account_auth_tools.attempts.entries)


def test_sensitive_peer_limit_cannot_be_reset_by_changing_authenticated_account(client, monkeypatch):
    monkeypatch.setattr(accounts, "monotonic", lambda: 1000)
    alice, bob = register(client), register(client, "bob_123")
    for index in range(10):
        error(rotate(client, alice if index % 2 else bob, "incorrect-password-123"), 401, "invalid_credentials")
    error(rotate(client, alice), 429, "rate_limited")
    error(rotate(client, bob), 429, "rate_limited")


def test_recover_rate_limits_malformed_attempts_and_ignores_forwarded_peer(client, monkeypatch):
    monkeypatch.setattr(accounts, "monotonic", lambda: 1000)
    for index in range(10):
        error(client.post("/api/v1/auth/recover", json={}, headers={
            "X-Forwarded-For": f"192.0.2.{index}",
        }), 422, "validation_error")
    error(recover(client, "bad-code"), 429, "rate_limited")
    assert ("recover", "127.0.0.1") in client.app.state.account_auth_tools.attempts.entries
    monkeypatch.setattr(accounts, "monotonic", lambda: 1060)
    error(recover(client, "bad-code"), 401, "invalid_credentials")


def test_all_account_routes_share_one_password_capacity_pool(client, monkeypatch, fast_hash):
    session = register(client)
    entered, release = threading.Event(), threading.Event()
    lock, count = threading.Lock(), 0

    def blocked(password, salt):
        nonlocal count
        with lock:
            count += 1
            if count == 2:
                entered.set()
        if not release.wait(10):
            raise RuntimeError("test worker was not released")
        return fast_hash(password, salt)

    monkeypatch.setattr(accounts, "_derive_password", blocked)
    with ThreadPoolExecutor(max_workers=2) as pool:
        pending = [pool.submit(login, client), pool.submit(recover, client, "wrong-code")]
        try:
            assert entered.wait(5)
            error(rotate(client, session), 429, "rate_limited")
            assert client.app.state.account_auth_tools.workers.active == 2
        finally:
            release.set()
        assert pending[0].result(timeout=10).status_code == 200
        error(pending[1].result(timeout=10), 401, "invalid_credentials")


def test_session_list_has_nonsecret_ids_metadata_and_user_scope(client):
    alice, bob = register(client), register(client, "bob_123")
    db = client.app.state.db
    browser_token = "test-browser-only-session"
    db.create_session(browser_token, alice["user"]["id"], 300, kind="browser", label="Laptop")
    db.create_session("expired-test-session", alice["user"]["id"], -1)
    response = client.get("/api/v1/account/sessions", headers=headers(alice))
    assert response.status_code == 200
    items = response.json()["items"]
    assert len(items) == 2 and sum(item["current"] for item in items) == 1
    assert {item["type"] for item in items} == {"bearer", "browser"}
    for item in items:
        assert str(UUID(item["id"])) == item["id"]
        assert set(item) == {"id", "createdAt", "lastSeenAt", "expiresAt", "label", "current", "type"}
        assert item["createdAt"].endswith("Z") and item["lastSeenAt"].endswith("Z")
        assert item["expiresAt"].endswith("Z")
    assert next(item for item in items if item["type"] == "browser")["label"] == "Laptop"
    assert all(secret not in response.text for secret in (
        alice["accessToken"], bob["accessToken"], browser_token,
        hashlib.sha256(alice["accessToken"].encode()).hexdigest(),
    ))
    assert len(client.get("/api/v1/account/sessions", headers=headers(bob)).json()["items"]) == 1


def test_revoke_is_owned_idempotent_and_self_revocation_is_safe(client):
    alice, bob = register(client), register(client, "bob_123")
    second = login(client).json()
    items = client.get("/api/v1/account/sessions", headers=headers(alice)).json()["items"]
    current = next(item["id"] for item in items if item["current"])
    other = next(item["id"] for item in items if not item["current"])
    bob_id = client.get("/api/v1/account/sessions", headers=headers(bob)).json()["items"][0]["id"]
    for target in (str(uuid4()), bob_id, other, other):
        assert client.post(f"/api/v1/account/sessions/{target}/revoke", headers=headers(alice)).json() == {"ok": True}
    error(client.get("/api/v1/account", headers=headers(second)), 401, "unauthorized")
    assert client.get("/api/v1/account", headers=headers(bob)).status_code == 200
    assert client.post(f"/api/v1/account/sessions/{current}/revoke", headers=headers(alice)).json() == {"ok": True}
    error(client.get("/api/v1/account/sessions", headers=headers(alice)), 401, "unauthorized")


def test_browser_kind_is_preserved_when_confirming_password_and_revoking_self(client):
    alice = register(client)
    token = "test-only-browser-cookie-value"
    client.app.state.db.create_session(token, alice["user"]["id"], 300, kind="browser")
    browser = {"accessToken": token}
    error(client.get("/api/v1/account/sessions", headers=headers(browser)), 401, "unauthorized")
    error(client.get("/api/v1/account/sessions", headers=headers(alice, kind="browser")), 401, "unauthorized")
    auth = headers(browser, kind="browser")
    items = client.get("/api/v1/account/sessions", headers=auth).json()["items"]
    current = next(item for item in items if item["current"])
    assert current["type"] == "browser"
    rotated = client.post("/api/v1/account/recovery", headers=auth, json={"currentPassword": PASSWORD})
    assert rotated.status_code == 200
    assert client.post(f"/api/v1/account/sessions/{current['id']}/revoke", headers=auth).json() == {"ok": True}
    error(client.get("/api/v1/account/sessions", headers=auth), 401, "unauthorized")


@pytest.mark.parametrize("path,method,body", [
    ("/api/v1/account/recovery", "get", None),
    ("/api/v1/account/recovery", "post", {"currentPassword": PASSWORD}),
    ("/api/v1/account/sessions", "get", None),
    (f"/api/v1/account/sessions/{uuid4()}/revoke", "post", None),
])
def test_account_security_requires_registered_identity_not_admin_or_dev(client, path, method, body):
    request = getattr(client, method)
    kwargs = {"json": body} if body is not None else {}
    error(request(path, **kwargs), 401, "unauthorized")
    db = client.app.state.db
    db.create_session("test-dev-token", "alice_developer", 300)
    auth = {"Authorization": "Bearer test-dev-token"}
    error(request(path, headers=auth, **kwargs), 403, "account_required")
    client.cookies.set("allira_admin", "not-an-ordinary-session")
    error(request(path, **kwargs), 401, "unauthorized")
    assert getattr(client.app.state, "account_auth_tools", None) is None


def test_account_security_limiter_keys_remain_bounded_and_reclaim_idle_state(client, monkeypatch):
    monkeypatch.setattr(accounts, "monotonic", lambda: 1000)
    monkeypatch.setattr(accounts, "RATE_MAX_ENTRIES", 2)
    error(recover(client, "bad-code"), 401, "invalid_credentials")
    second = TestClient(client.app, base_url=ORIGIN, client=("127.0.0.2", 1))
    third = TestClient(client.app, base_url=ORIGIN, client=("127.0.0.3", 1))
    try:
        error(recover(second, "bad-code"), 401, "invalid_credentials")
        error(recover(third, "bad-code"), 429, "rate_limited")
        assert len(client.app.state.account_auth_tools.attempts.entries) == 2
        monkeypatch.setattr(accounts, "monotonic", lambda: 1060)
        error(recover(third, "bad-code"), 401, "invalid_credentials")
        assert list(client.app.state.account_auth_tools.attempts.entries) == [("recover", "127.0.0.3")]
    finally:
        second.close()
        third.close()


def test_security_migration_preserves_legacy_three_column_session(tmp_path, fast_hash):
    import sqlite3
    import time

    path = str(tmp_path / "old-schema.sqlite3")
    token = "test-only-existing-legacy-token"
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    conn = sqlite3.connect(path)
    with conn:
        conn.execute("CREATE TABLE sessions (tokenHash TEXT PRIMARY KEY, userId TEXT NOT NULL, expiresAt REAL NOT NULL)")
        conn.execute("INSERT INTO sessions VALUES (?, ?, ?)", (token_hash, "legacy-user", time.time() + 300))
        conn.execute("CREATE TABLE untouched (id TEXT PRIMARY KEY)")
        conn.execute("INSERT INTO untouched VALUES ('keep')")
    conn.close()
    with TestClient(make_app(path), base_url=ORIGIN) as client:
        db = client.app.state.db
        assert db.authenticate(token) == "legacy-user"
        row = dict(db.conn.execute("SELECT * FROM sessions WHERE tokenHash = ?", (token_hash,)).fetchone())
        session_id = row["sessionId"]
        assert str(UUID(session_id)) == session_id
        assert row["createdAt"] and row["lastSeenAt"]
        assert row["kind"] == "bearer" and row["label"] == "SDK"
        assert db.conn.execute("SELECT id FROM untouched").fetchone()[0] == "keep"
        initialize_account_security(db)
        assert db.conn.execute("SELECT sessionId FROM sessions WHERE tokenHash = ?", (token_hash,)).fetchone()[0] == session_id
        session = register(client)
        assert client.get("/api/v1/account/recovery", headers=headers(session)).json() == {
            "enabled": False, "remainingCodes": 0, "updatedAt": None,
        }
    with TestClient(make_app(path), base_url=ORIGIN) as reopened:
        assert reopened.app.state.db.authenticate(token) == "legacy-user"
        assert reopened.app.state.db.conn.execute("SELECT sessionId FROM sessions WHERE tokenHash = ?", (token_hash,)).fetchone()[0] == session_id


def test_recovery_schema_idempotence_restart_and_account_delete_cascade(tmp_path, fast_hash):
    path = str(tmp_path / "migration.sqlite3")
    with TestClient(make_app(path), base_url=ORIGIN) as first:
        session = register(first)
        codes = issue(first, session)
        db = first.app.state.db
        db.conn.execute("PRAGMA user_version = 77")
        before = snapshot(db)
        initialize_account_security(db)
        initialize_account_security(db)
        assert snapshot(db) == before
        assert db.conn.execute("PRAGMA user_version").fetchone()[0] == 77
    with TestClient(make_app(path), base_url=ORIGIN) as second:
        assert second.get("/api/v1/account/recovery", headers=headers(session)).json()["remainingCodes"] == 8
        assert second.app.state.db.conn.execute("PRAGMA user_version").fetchone()[0] == 77
        with second.app.state.db.atomic():
            second.app.state.db.conn.execute("DELETE FROM accounts WHERE userId = ?", (session["user"]["id"],))
        for table in ("account_recovery_state", "account_recovery_codes"):
            assert second.app.state.db.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
        error(recover(second, codes["codes"][0]), 401, "invalid_credentials")


def test_openapi_freezes_recovery_session_dtos_and_secret_inputs(client):
    spec = client.get("/openapi.json").json()
    paths, schemas = spec["paths"], spec["components"]["schemas"]
    assert set(paths) >= {
        "/api/v1/account/recovery", "/api/v1/auth/recover",
        "/api/v1/account/sessions", "/api/v1/account/sessions/{id}/revoke",
    }
    assert set(schemas["AccountDeviceSession"]["properties"]) == {
        "id", "createdAt", "lastSeenAt", "expiresAt", "label", "current", "type",
    }
    assert set(schemas["AccountRecoveryStatus"]["properties"]) == {"enabled", "remainingCodes", "updatedAt"}
    for schema, field in (("AccountRecoveryRotate", "currentPassword"), ("AccountRecover", "recoveryCode"), ("AccountRecover", "newPassword")):
        assert schemas[schema]["properties"][field]["writeOnly"] is True
    assert schemas["AccountRecover"]["additionalProperties"] is False
    assert schemas["AccountDeviceSession"]["properties"]["type"]["enum"] == ["bearer", "browser"]
    assert accounts.SENSITIVE_ATTEMPTS == account_security.RECOVERY_ATTEMPTS == 10
    assert account_security.RECOVERY_CODE_BYTES * 8 >= 128
