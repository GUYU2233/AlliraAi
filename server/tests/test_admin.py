"""Administrator authorization, CSRF, ownership, audit and loopback integration tests."""

import asyncio
import secrets
import time
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from allira.admin import COOKIE_NAME
from allira.admin_db import AdminRepository, token_hash
from allira.app import create_app
from allira.config import Settings
from allira.db import APIError, Database, now
from allira.schemas import ConversationInput, RunInput

ORIGIN = "http://127.0.0.1:8000"


@pytest.fixture
def client(tmp_path):
    settings = Settings(
        _env_file=None, database_path=str(tmp_path / "admin.sqlite3"),
        allow_dev_auth=True, admin_enabled=True, max_concurrent_runs=1,
    )
    with TestClient(
        create_app(settings), base_url=ORIGIN, client=("127.0.0.1", 34567),
    ) as client:
        yield client


def login(client):
    code = secrets.token_urlsafe(32)
    AdminRepository(client.app.state.db).issue_login(code, 300)
    response = client.post("/api/v1/admin/session", json={"code": code}, headers={"Origin": ORIGIN})
    assert response.status_code == 200, response.text
    return {"Origin": ORIGIN, "X-CSRF-Token": response.json()["csrfToken"]}


def user(client, name):
    response = client.post("/api/v1/dev/session", json={"userId": name})
    return {"Authorization": f"Bearer {response.json()['accessToken']}"}


def chat(client, headers, title="chat"):
    response = client.post("/api/v1/conversations", json={"title": title}, headers=headers)
    assert response.status_code == 201
    return response.json()


def test_admin_disabled_and_production_guard(tmp_path):
    settings = Settings(_env_file=None, database_path=str(tmp_path / "off.sqlite3"))
    with TestClient(create_app(settings), base_url=ORIGIN, client=("127.0.0.1", 1)) as client:
        assert client.get("/api/v1/admin/overview").status_code == 404
        assert client.get("/test/").status_code == 404
        assert client.get("/api/v1/info").json() == {
            "environment": "development", "provider": "mock", "allowDevAuth": False,
            "adminEnabled": False, "consoleEnabled": False,
        }
    for flag in ("admin_enabled", "console_enabled"):
        with pytest.raises(ValidationError):
            Settings(_env_file=None, environment="production", **{flag: True})
    with pytest.raises(ValidationError):
        Settings(_env_file=None, provider="openai", upstream_model="model", mock_chunk_delay_seconds=1)


def test_no_developer_id_can_grant_admin(client):
    developer = user(client, "admin")
    assert client.get("/api/v1/admin/session", headers=developer).status_code == 401
    assert client.get("/api/v1/admin/overview", headers=developer).status_code == 401
    assert client.post("/api/v1/admin/runs/missing/cancel", headers=developer).status_code == 401


def test_single_use_link_session_cookie_and_logout(client):
    repository = AdminRepository(client.app.state.db)
    old, code = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    repository.issue_login(old, 300)
    repository.issue_login(code, 300)
    stored = client.app.state.db.conn.execute("SELECT codeHash FROM admin_login_codes").fetchone()[0]
    assert stored == token_hash(code) and stored != code
    assert client.post("/api/v1/admin/session", json={"code": old}, headers={"Origin": ORIGIN}).status_code == 401
    # Origin is verified before consuming the link.
    for headers in ({}, {"Origin": "http://attacker.invalid"}):
        assert client.post("/api/v1/admin/session", json={"code": code}, headers=headers).status_code == 403
    response = client.post("/api/v1/admin/session", json={"code": code}, headers={"Origin": ORIGIN})
    assert response.status_code == 200
    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie and "path=/api/v1/admin" in cookie
    assert "cache-control" in response.headers and response.headers["cache-control"] == "no-store"
    session = response.json()
    assert session["authenticated"] is True and session["expiresAt"].endswith("Z")
    assert client.get("/api/v1/admin/session").json() == session
    assert client.post("/api/v1/admin/session", json={"code": code}, headers={"Origin": ORIGIN}).status_code == 401
    raw = client.cookies.get(COOKIE_NAME)
    assert client.app.state.db.conn.execute("SELECT tokenHash FROM admin_sessions").fetchone()[0] == token_hash(raw)
    assert client.post("/api/v1/admin/logout", headers={"Origin": ORIGIN}).status_code == 403
    headers = {"Origin": ORIGIN, "X-CSRF-Token": session["csrfToken"]}
    assert client.post("/api/v1/admin/logout", headers=headers).json() == {"ok": True}
    assert client.get("/api/v1/admin/overview").status_code == 401
    assert raw not in str(repository.overview())


def test_admin_expiry_and_cli_revocation(client):
    repository = AdminRepository(client.app.state.db)
    expired = secrets.token_urlsafe(32)
    repository.issue_login(expired, -1)
    assert client.post("/api/v1/admin/session", json={"code": expired}, headers={"Origin": ORIGIN}).status_code == 401
    login(client)
    assert repository.revoke_admin_sessions() == 1
    assert client.get("/api/v1/admin/session").status_code == 401
    login(client)
    with client.app.state.db.conn:
        client.app.state.db.conn.execute("UPDATE admin_sessions SET expiresAt = ?", (time.time() - 1,))
    assert client.get("/api/v1/admin/session").status_code == 401


@pytest.mark.parametrize("headers", [
    {"Host": "attacker.invalid"}, {"Host": "localhost.attacker.invalid:8000"},
    {"Host": "127.0.0.1:0"}, {"Host": "127.0.0.1:999999"},
    {"Host": "127.0.0.1:8000", "Sec-Fetch-Site": "cross-site"},
])
def test_loopback_host_and_fetch_metadata(client, headers):
    assert client.get("/api/v1/admin/session", headers=headers).status_code == 403


def test_nonloopback_peer_rejected_even_with_forwarded_headers(tmp_path):
    settings = Settings(_env_file=None, database_path=str(tmp_path / "peer.sqlite3"), admin_enabled=True)
    with TestClient(create_app(settings), base_url=ORIGIN, client=("10.0.0.2", 123)) as client:
        response = client.get("/api/v1/admin/session", headers={"X-Forwarded-For": "127.0.0.1"})
        assert response.status_code == 403


def test_management_filters_pagination_and_audit(client):
    alice, bob = user(client, "alice"), user(client, "bob")
    a, b = chat(client, alice, "a"), chat(client, bob, "b")
    headers = login(client)
    overview = client.get("/api/v1/admin/overview").json()
    assert overview["users"] == 2 and overview["conversations"] == 2
    assert overview["activeSessions"] == 2 and overview["maxConcurrentRuns"] == 1
    page = client.get("/api/v1/admin/users?limit=1").json()
    assert page["items"][0]["userId"] == "alice" and page["nextCursor"] == "alice"
    assert client.get("/api/v1/admin/users?cursor=alice&limit=1").json()["items"][0]["userId"] == "bob"
    assert client.get("/api/v1/admin/users?cursor=missing").status_code == 400
    filtered = client.get("/api/v1/admin/conversations?userId=alice").json()
    assert filtered["items"][0]["id"] == a["id"]
    assert filtered["items"][0]["ownerId"] == "alice"
    assert client.get(f"/api/v1/admin/conversations?userId=alice&cursor={b['id']}").status_code == 400
    assert client.get(f"/api/v1/admin/conversations/{a['id']}/messages").json()["items"] == []
    assert client.post(f"/api/v1/admin/conversations/{b['id']}/delete", headers=headers).json() == {"ok": True}
    assert client.get(f"/api/v1/conversations/{b['id']}", headers=bob).status_code == 404
    revoked = client.post("/api/v1/admin/users/alice/revoke-sessions", headers=headers)
    assert revoked.json() == {"ok": True, "revoked": 1}
    assert client.get("/api/v1/session", headers=alice).status_code == 401
    # Revocation deliberately does not turn development auth into account blocking.
    assert client.get("/api/v1/session", headers=user(client, "alice")).status_code == 200
    audits, cursor = [], None
    for _ in range(30):
        page = client.get("/api/v1/admin/audit", params={"limit": 1, **({"cursor": cursor} if cursor else {})}).json()
        audits.extend(page["items"])
        cursor = page["nextCursor"]
        if cursor is None:
            break
    actions = {item["action"] for item in audits}
    assert {"login_link.issued", "session.login", "conversation.messages_viewed", "conversation.deleted", "user.sessions_revoked"} <= actions
    assert all(set(item) == {"id", "action", "targetType", "targetId", "createdAt"} for item in audits)
    assert client.get("/api/v1/admin/audit?cursor=not-a-number").status_code == 400
    assert client.get("/api/v1/admin/runs?status=nonsense").status_code == 422
    assert client.post("/api/v1/admin/users/missing/revoke-sessions", headers=headers).status_code == 404


def test_csrf_and_origin_block_writes_without_side_effects(client):
    alice = user(client, "alice")
    item = chat(client, alice)
    headers = login(client)
    endpoint = f"/api/v1/admin/conversations/{item['id']}/delete"
    for bad in (
        {}, {"Origin": ORIGIN}, {"Origin": "http://attacker.invalid", "X-CSRF-Token": headers["X-CSRF-Token"]},
        {"Origin": ORIGIN, "X-CSRF-Token": "wrong"},
    ):
        assert client.post(endpoint, headers=bad).status_code == 403
    assert client.get(f"/api/v1/conversations/{item['id']}", headers=alice).status_code == 200


def test_admin_cancel_active_run_and_filter_state(tmp_path):
    class Waiting:
        async def stream(self, messages):
            yield "partial"
            await asyncio.Event().wait()

    settings = Settings(_env_file=None, database_path=str(tmp_path / "cancel.sqlite3"), allow_dev_auth=True, admin_enabled=True)
    with TestClient(create_app(settings, Waiting()), base_url=ORIGIN, client=("127.0.0.1", 1)) as client:
        alice = user(client, "alice")
        item = chat(client, alice)
        run = client.post(f"/api/v1/conversations/{item['id']}/runs", headers=alice,
                          json={"clientMessageId": str(uuid4()), "content": "hi"}).json()
        headers = login(client)
        assert client.post(f"/api/v1/admin/conversations/{item['id']}/delete", headers=headers).status_code == 409
        active = client.get("/api/v1/admin/runs?status=running&userId=alice").json()["items"]
        assert len(active) == 1 and active[0]["ownerId"] == "alice"
        endpoint = f"/api/v1/admin/runs/{run['id']}/cancel"
        cancelled = client.post(endpoint, headers=headers).json()
        assert cancelled["status"] == "cancelled"
        assert client.post(endpoint, headers=headers).json() == cancelled
        assert client.get("/api/v1/admin/runs?status=running").json()["items"] == []
        assert client.get(f"/api/v1/admin/runs/{run['id']}").json() == cancelled
        assert client.get(f"/api/v1/admin/runs?status=completed&cursor={run['id']}").status_code == 400
        assert client.post(f"/api/v1/admin/conversations/{item['id']}/delete", headers=headers).status_code == 200
        assert client.get(f"/api/v1/admin/runs/{run['id']}").status_code == 404


def test_existing_database_upgrade_preserves_messages(tmp_path):
    path = str(tmp_path / "upgrade.sqlite3")
    settings = Settings(_env_file=None, allow_dev_auth=True)
    db = Database(path, settings)
    db.create_session("old-session", "old-user", 300)
    # Simulate the previous schema; these are additive tables only.
    db.conn.executescript("DROP TABLE admin_access_sessions; DROP TABLE admin_login_codes; DROP TABLE admin_sessions; DROP TABLE admin_audit; DROP TABLE users; PRAGMA user_version=0;")
    db.close()
    db = Database(path, settings)
    assert db.authenticate("old-session") == "old-user"
    assert AdminRepository(db).list_users(None, 50)["items"][0]["userId"] == "old-user"
    assert db.conn.execute("PRAGMA user_version").fetchone()[0] == 8
    db.close()


def test_legacy_audit_owner_backfill_uses_only_recoverable_resource_links(tmp_path):
    path = str(tmp_path / "audit-upgrade.sqlite3")
    db = Database(path)
    active = db.create_conversation("first-owner", ConversationInput())
    paid = db.create_conversation("second-owner", ConversationInput())
    run, _ = db.create_run("second-owner", paid.id,
                           RunInput(clientMessageId=uuid4(), content="synthetic"), 120000)
    db.finish(run.id, "completed")
    db.delete_conversation("second-owner", paid.id)
    db.conn.executescript("""
        DROP TABLE admin_audit;
        CREATE TABLE admin_audit (
            id INTEGER PRIMARY KEY AUTOINCREMENT, action TEXT NOT NULL,
            targetType TEXT NOT NULL, targetId TEXT, createdAt TEXT NOT NULL
        );
    """)
    targets = [("conversation", active.id), ("conversation", paid.id),
               ("run", run.id), ("user", "first-owner"),
               ("conversation", "unattributable-legacy-id"), ("admin", None)]
    with db.atomic():
        db.conn.executemany(
            "INSERT INTO admin_audit (action, targetType, targetId, createdAt) VALUES (?, ?, ?, ?)",
            [("synthetic.audit", kind, target, now()) for kind, target in targets],
        )
    db.close()
    db = Database(path)
    expected = ["first-owner", "second-owner", "second-owner", "first-owner", None, None]
    rows = db.conn.execute("SELECT ownerId FROM admin_audit ORDER BY id").fetchall()
    assert [row["ownerId"] for row in rows] == expected
    assert all("ownerId" not in item for item in AdminRepository(db).list_audit(None, 50)["items"])
    assert db.conn.execute("PRAGMA foreign_key_check").fetchall() == []
    assert not db.conn.in_transaction
    db.close()
    db = Database(path)
    assert [row[0] for row in db.conn.execute("SELECT ownerId FROM admin_audit ORDER BY id")] == expected
    db.close()


def test_login_exchange_rollback_keeps_unused_link(client, monkeypatch):
    repository = AdminRepository(client.app.state.db)
    code, token = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    repository.issue_login(code, 300)

    def fail(*args):
        raise RuntimeError("simulated write failure")

    monkeypatch.setattr(repository, "audit", fail)
    with pytest.raises(RuntimeError):
        repository.exchange_login(code, token, 300)
    assert client.app.state.db.conn.execute("SELECT COUNT(*) FROM admin_sessions").fetchone()[0] == 0
    monkeypatch.undo()
    assert repository.exchange_login(code, token, 300) > time.time()
    with pytest.raises(APIError):
        repository.exchange_login(code, "other-token", 300)
