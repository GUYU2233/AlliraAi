"""Registered browser cookies are opt-in, kind-bound, same-origin and CSRF protected."""

import hashlib
import hmac
import time
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from allira import accounts
from allira.admin_db import AdminRepository
from allira.app import create_app
from allira.browser_sessions import COOKIE_NAME, csrf_token
from allira.config import Settings
from allira.db import Database

ORIGIN = "http://127.0.0.1:8000"
PASSWORD = "test-browser-password-123"


@pytest.fixture(autouse=True)
def fast_password(monkeypatch):
    # Test the session boundary without performing dozens of expensive scrypt jobs.
    # Production scrypt remains covered by the dedicated account suite.
    monkeypatch.setattr(accounts, "_derive_password", lambda password, salt: hmac.digest(
        salt, password.get_secret_value().encode(), "sha256",
    ) * 2)


@pytest.fixture
def client(tmp_path):
    settings = Settings(_env_file=None, database_path=str(tmp_path / "browser.sqlite3"),
                        allow_dev_auth=True, admin_enabled=True, browser_session_ttl_seconds=600)
    with TestClient(create_app(settings), base_url=ORIGIN, client=("127.0.0.1", 10)) as client:
        yield client


def register(client, username="browser_user"):
    response = client.post("/api/v1/auth/register", json={
        "username": username, "password": PASSWORD, "acceptedTermsVersion": "2026-11-19", "isAdult": True,
    })
    assert response.status_code == 201
    return response.json()


def exchange(client, token):
    return client.post("/api/v1/auth/browser/session", headers={
        "Authorization": "Bearer " + token, "Origin": ORIGIN,
    })


def test_exchange_consumes_bearer_and_restores_without_javascript_credentials(client):
    account = register(client)
    response = exchange(client, account["accessToken"])
    assert response.status_code == 200
    view = response.json()
    assert set(view) == {"user", "csrfToken", "expiresAt"}
    assert view["user"] == account["user"]
    assert account["accessToken"] not in response.text
    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie and "SameSite=strict" in cookie and "Path=/api/v1" in cookie
    assert "Max-Age=600" in cookie and "Domain=" not in cookie and "Secure" not in cookie
    raw = client.cookies.get(COOKIE_NAME)
    assert view["csrfToken"] == csrf_token(raw)
    row = client.app.state.db.conn.execute("SELECT * FROM sessions").fetchone()
    assert row["kind"] == "browser" and row["label"] == "Browser"
    assert row["tokenHash"] == hashlib.sha256(raw.encode()).hexdigest() and raw not in dict(row).values()
    assert client.get("/api/v1/account").json() == account["user"]
    assert client.get("/api/v1/auth/browser/session").json() == view
    assert client.get("/api/v1/session", headers={"Authorization": "Bearer " + account["accessToken"]}).status_code == 401
    assert client.get("/api/v1/session", headers={"Authorization": "Bearer " + raw}).status_code == 401
    assert client.get("/api/v1/admin/overview").status_code == 401


@pytest.mark.parametrize("headers", [
    {}, {"Origin": ORIGIN}, {"Origin": ORIGIN, "X-CSRF-Token": "wrong"},
    {"Origin": "https://attacker.invalid"}, {"Origin": ORIGIN, "Sec-Fetch-Site": "cross-site"},
    {"Origin": ORIGIN, "Sec-Fetch-Site": "same-site"},
])
def test_cookie_writes_require_exact_origin_csrf_and_context(client, headers):
    exchange(client, register(client)["accessToken"])
    before = client.app.state.db.conn.execute("SELECT COUNT(*) FROM conversations").fetchone()[0]
    response = client.post("/api/v1/conversations", headers=headers, json={"title": "forbidden"})
    assert response.status_code == 403
    assert client.app.state.db.conn.execute("SELECT COUNT(*) FROM conversations").fetchone()[0] == before


def test_cookie_writes_sse_and_generic_logout(client):
    result = exchange(client, register(client)["accessToken"]).json()
    headers = {"Origin": ORIGIN, "X-CSRF-Token": result["csrfToken"]}
    chat = client.post("/api/v1/conversations", json={"title": "cookie chat"}, headers=headers).json()
    response = client.post(f"/api/v1/conversations/{chat['id']}/runs", headers=headers,
                           json={"clientMessageId": str(uuid4()), "content": "cookie message"})
    assert response.status_code == 202
    run_id = response.json()["id"]
    events = client.get(f"/api/v1/runs/{run_id}/events")
    assert events.status_code == 200 and "event: run.completed" in events.text
    response = client.post("/api/v1/session/logout", headers=headers)
    assert response.json() == {"ok": True} and "Max-Age=0" in response.headers["set-cookie"]
    assert client.cookies.get(COOKIE_NAME) is None
    assert client.get("/api/v1/account").status_code == 401


def test_exchange_rotation_preserves_other_devices_and_no_fallback(client):
    first = register(client)
    exchange(client, first["accessToken"])
    old_cookie = client.cookies.get(COOKIE_NAME)
    login = client.post("/api/v1/auth/login", json={"username": first["user"]["username"], "password": PASSWORD}).json()
    untouched = client.post("/api/v1/auth/login", json={"username": first["user"]["username"], "password": PASSWORD}).json()
    response = exchange(client, login["accessToken"])
    assert response.status_code == 200
    new_cookie = client.cookies.get(COOKIE_NAME)
    assert old_cookie != new_cookie
    assert client.get("/api/v1/account", headers={"Authorization": "Bearer " + untouched["accessToken"]}).status_code == 200
    for malformed in ("Basic missing", "Bearer", "Bearer expired"):
        assert client.get("/api/v1/account", headers={"Authorization": malformed}).status_code == 401
    # An ambient cookie cannot be silently exchanged again without a fresh Bearer.
    assert client.post("/api/v1/auth/browser/session", headers={
        "Origin": ORIGIN, "X-CSRF-Token": response.json()["csrfToken"],
    }).status_code == 401
    assert client.app.state.db.conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 2


def test_failed_exchange_has_no_side_effects_and_dev_cannot_exchange(client, monkeypatch):
    account = register(client)
    assert client.post("/api/v1/auth/browser/session", headers={
        "Authorization": "Bearer " + account["accessToken"], "Origin": "https://attacker.invalid",
    }).status_code == 403
    assert client.get("/api/v1/account", headers={"Authorization": "Bearer " + account["accessToken"]}).status_code == 200
    original = client.app.state.db.revoke_session

    def failure(_token):
        raise RuntimeError("synthetic session rotation failure")

    monkeypatch.setattr(client.app.state.db, "revoke_session", failure)
    with pytest.raises(RuntimeError, match="synthetic"):
        exchange(client, account["accessToken"])
    assert client.app.state.db.conn.execute("SELECT COUNT(*) FROM sessions WHERE kind = 'browser'").fetchone()[0] == 0
    monkeypatch.setattr(client.app.state.db, "revoke_session", original)
    developer = client.post("/api/v1/dev/session", json={"userId": "not_admin"}).json()
    assert exchange(client, developer["accessToken"]).status_code == 403


def test_account_and_admin_cookies_are_independent(client):
    browser = exchange(client, register(client)["accessToken"]).json()
    repository = AdminRepository(client.app.state.db)
    repository.issue_login("a" * 43, 300)
    admin = client.post("/api/v1/admin/session", headers={"Origin": ORIGIN}, json={"code": "a" * 43}).json()
    assert client.get("/api/v1/admin/overview").status_code == 200
    assert client.post("/api/v1/conversations", headers={
        "Origin": ORIGIN, "X-CSRF-Token": admin["csrfToken"],
    }, json={"title": "wrong identity csrf"}).status_code == 403
    assert client.post("/api/v1/auth/browser/logout", headers={
        "Origin": ORIGIN, "X-CSRF-Token": browser["csrfToken"],
    }).status_code == 200
    assert client.get("/api/v1/admin/overview").status_code == 200
    assert client.get("/api/v1/account").status_code == 401


@pytest.mark.parametrize(("origin", "peer", "expected"), [
    ("http://127.0.0.1:8000", "203.0.113.1", 403),
    ("http://evil.invalid", "127.0.0.1", 403),
    ("https://private.example", "203.0.113.1", 200),
    ("http://localhost:8000", "127.0.0.1", 200),
])
def test_http_only_loopback_https_secure_cookie(tmp_path, origin, peer, expected):
    with TestClient(create_app(Settings(_env_file=None, database_path=str(tmp_path / "origin.sqlite3"))),
                    base_url=origin, client=(peer, 10)) as other:
        account = register(other)
        response = other.post("/api/v1/auth/browser/session", headers={
            "Authorization": "Bearer " + account["accessToken"], "Origin": origin,
        })
        assert response.status_code == expected
        if expected == 200:
            assert ("Secure" in response.headers["set-cookie"]) == origin.startswith("https:")


def test_expiry_revocation_and_kind_boundaries(client):
    exchange(client, register(client)["accessToken"])
    with client.app.state.db.atomic():
        client.app.state.db.conn.execute("UPDATE sessions SET expiresAt = ?", (time.time() - 1,))
    assert client.get("/api/v1/auth/browser/session").status_code == 401
    assert client.get("/api/v1/account").status_code == 401


def test_original_three_column_sessions_migrate_without_rotating_token(tmp_path):
    import sqlite3

    path = tmp_path / "legacy.sqlite3"
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE sessions(tokenHash TEXT PRIMARY KEY, userId TEXT NOT NULL, expiresAt REAL NOT NULL)")
    connection.execute("INSERT INTO sessions VALUES (?, 'legacy', ?)",
                       (hashlib.sha256(b"legacy-value").hexdigest(), time.time() + 600))
    connection.commit()
    connection.close()
    db = Database(str(path), Settings(_env_file=None, allow_dev_auth=True))
    assert db.authenticate("legacy-value") == "legacy"
    row = dict(db.conn.execute("SELECT * FROM sessions").fetchone())
    assert row["kind"] == "bearer" and row["label"] == "SDK" and row["sessionId"]
    assert row["createdAt"] and row["lastSeenAt"]
    db.close()
    db = Database(str(path), Settings(_env_file=None, allow_dev_auth=True))
    assert dict(db.conn.execute("SELECT * FROM sessions").fetchone())["sessionId"] == row["sessionId"]
    assert db.authenticate("legacy-value") == "legacy"
    db.close()
