"""Session revocation, recent-history paging, editing/deletion and capacity controls."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from allira.app import create_app
from allira.config import Settings


def auth(client, user="alice"):
    session = client.post("/api/v1/dev/session", json={"userId": user}).json()
    return {"Authorization": f"Bearer {session['accessToken']}"}


def test_session_logout_only_revokes_that_token(tmp_path):
    settings = Settings(_env_file=None, database_path=str(tmp_path / "sessions.sqlite3"), allow_dev_auth=True)
    with TestClient(create_app(settings)) as client:
        first, second = auth(client), auth(client)
        assert client.get("/api/v1/session", headers=first).json() == {"userId": "alice"}
        assert client.post("/api/v1/session/logout", headers=first).json() == {"ok": True}
        assert client.get("/api/v1/session", headers=first).status_code == 401
        assert client.get("/api/v1/session", headers=second).status_code == 200


def test_recent_paging_edit_delete_and_isolation(tmp_path):
    settings = Settings(_env_file=None, database_path=str(tmp_path / "history.sqlite3"), allow_dev_auth=True)
    with TestClient(create_app(settings)) as client:
        alice, bob = auth(client), auth(client, "bob")
        item = client.post("/api/v1/conversations", headers=alice, json={"title": "old"}).json()
        path = f"/api/v1/conversations/{item['id']}"
        assert client.get(path + "/state", headers=alice).json() == {
            "conversation": item, "activeRun": None, "lastSequence": 0,
        }
        db = client.app.state.db
        # All repository methods stay on the same portal loop, not the caller thread.
        async def populate():
            from allira.schemas import RunInput
            for index in range(3):
                run, _ = db.create_run("alice", item["id"], RunInput(clientMessageId=uuid4(), content=f"Q{index}"), 120000)
                db.append_delta(run.id, f"A{index}")
                db.finish(run.id, "completed")
        client.portal.call(populate)
        newest = client.get(path + "/messages/recent?limit=3", headers=alice).json()
        assert [message["sequence"] for message in newest["items"]] == [4, 5, 6]
        older = client.get(path + "/messages/recent", headers=alice,
                           params={"before": newest["nextCursor"], "limit": 3}).json()
        assert [message["sequence"] for message in older["items"]] == [1, 2, 3]
        assert older["nextCursor"] is None
        assert client.get(path + "/state", headers=alice).json()["lastSequence"] == 6
        assert client.get(path + "/messages/recent?before=missing", headers=alice).status_code == 400
        for suffix in ("/state", "/messages/recent"):
            assert client.get(path + suffix, headers=bob).status_code == 404
        for suffix, body in (("/update", {"title": "theft"}), ("/delete", {})):
            assert client.post(path + suffix, headers=bob, json=body).status_code == 404
        changed = client.post(path + "/update", headers=alice,
                              json={"title": " renamed ", "instructions": "role"}).json()
        assert changed["title"] == "renamed" and changed["instructions"] == "role"
        assert client.post(path + "/update", headers=alice, json={"instructions": None}).json()["instructions"] is None
        for body in ({}, {"title": None}, {"title": "  "}, {"title": "x", "bad": 1}):
            assert client.post(path + "/update", headers=alice, json=body).status_code == 422
        assert client.post(path + "/delete", headers=alice).json() == {"ok": True}
        assert client.get(path, headers=alice).status_code == 404
        assert db.conn.execute("SELECT COUNT(*) FROM run_events").fetchone()[0] == 0
        assert db.conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 0
        assert db.conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 0


def test_running_edits_deletes_and_capacity_with_idempotency(tmp_path):
    class Waiting:
        async def stream(self, messages):
            yield "partial"
            await asyncio.Event().wait()

    settings = Settings(_env_file=None, database_path=str(tmp_path / "capacity.sqlite3"), allow_dev_auth=True, max_concurrent_runs=1)
    with TestClient(create_app(settings, Waiting())) as client:
        headers = auth(client)
        first, second = [client.post("/api/v1/conversations", headers=headers, json={}).json() for _ in range(2)]
        path = f"/api/v1/conversations/{first['id']}"
        body = {"clientMessageId": str(uuid4()), "content": "hello"}
        run = client.post(path + "/runs", headers=headers, json=body).json()
        assert client.get(path + "/state", headers=headers).json()["activeRun"]["id"] == run["id"]
        assert client.post(path + "/update", headers=headers, json={"title": "blocked"}).status_code == 409
        assert client.post(path + "/delete", headers=headers).status_code == 409
        replay = client.post(path + "/runs", headers=headers, json=body)
        assert replay.status_code == 202 and replay.json()["id"] == run["id"]
        endpoint = f"/api/v1/conversations/{second['id']}/runs"
        more = {"clientMessageId": str(uuid4()), "content": "other"}
        limited = client.post(endpoint, headers=headers, json=more)
        assert limited.status_code == 429 and limited.json()["error"]["code"] == "capacity_exceeded"
        assert client.get(f"/api/v1/conversations/{second['id']}/state", headers=headers).json()["lastSequence"] == 0
        assert client.post(f"/api/v1/runs/{run['id']}/cancel", headers=headers).json()["status"] == "cancelled"
        assert client.post(endpoint, headers=headers, json=more).status_code == 202


@pytest.mark.parametrize("delete_during_cleanup", [False, True])
def test_closing_task_keeps_capacity_until_upstream_is_released(tmp_path, delete_during_cleanup):
    class SlowClosing:
        def __init__(self):
            self.started = asyncio.Event()
            self.closing = asyncio.Event()
            self.release = asyncio.Event()

        async def stream(self, messages):
            try:
                self.started.set()
                await asyncio.Event().wait()
                yield "unreachable"
            finally:
                self.closing.set()
                await self.release.wait()

    provider = SlowClosing()
    settings = Settings(_env_file=None, database_path=str(tmp_path / "closing.sqlite3"),
                        allow_dev_auth=True, max_concurrent_runs=1)
    with TestClient(create_app(settings, provider)) as client:
        headers = auth(client)
        first, second = [client.post("/api/v1/conversations", headers=headers, json={}).json()
                         for _ in range(2)]
        endpoint = f"/api/v1/conversations/{first['id']}/runs"
        body = {"clientMessageId": str(uuid4()), "content": "hello"}
        created = client.post(endpoint, headers=headers, json=body).json()
        async def started():
            await asyncio.wait_for(provider.started.wait(), timeout=1)
        async def closing():
            await asyncio.wait_for(provider.closing.wait(), timeout=1)
        async def release():
            provider.release.set()
        client.portal.call(started)
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(client.post, f"/api/v1/runs/{created['id']}/cancel", headers=headers)
            try:
                client.portal.call(closing)
                # Replay remains free/available even while the physical slot is closing.
                replay = client.post(endpoint, headers=headers, json=body)
                assert replay.status_code == 202 and replay.json()["status"] == "cancelled"
                if delete_during_cleanup:
                    assert client.post(f"/api/v1/conversations/{first['id']}/delete", headers=headers).status_code == 200
                another = f"/api/v1/conversations/{second['id']}/runs"
                more = {"clientMessageId": str(uuid4()), "content": "other"}
                blocked = client.post(another, headers=headers, json=more)
                assert blocked.status_code == 429
                assert blocked.json()["error"]["code"] == "capacity_exceeded"
                assert client.get("/api/v1/energy", headers=headers).json()["balance"] == 100
                assert client.get(f"/api/v1/conversations/{second['id']}/state", headers=headers).json()["lastSequence"] == 0
            finally:
                client.portal.call(release)
            assert future.result(timeout=2).json()["status"] == "cancelled"
            assert client.post(another, headers=headers, json=more).status_code == 202


def test_console_static_headers_and_traversal(tmp_path):
    root = tmp_path / "dist"
    for name in ("test", "admin", "assets"):
        (root / name).mkdir(parents=True)
    (root / "test/index.html").write_text("<!doctype html><title>test</title>", encoding="utf-8")
    (root / "admin/index.html").write_text("<!doctype html><title>admin</title>", encoding="utf-8")
    (root / "assets/chat.js").write_text("console.log('test')", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("must-not-serve", encoding="utf-8")
    settings = Settings(_env_file=None, database_path=str(tmp_path / "static.sqlite3"), console_enabled=True, console_dist_path=str(root))
    with TestClient(create_app(settings), base_url="http://localhost:8000", client=("127.0.0.1", 1)) as client:
        for path in ("/test/", "/admin/", "/console-assets/chat.js"):
            response = client.get(path)
            assert response.status_code == 200
            assert response.headers["x-content-type-options"] == "nosniff"
            assert response.headers["x-frame-options"] == "DENY"
            assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
            assert response.headers["cache-control"] == "no-store"
        for path in ("/console-assets/../secret.txt", "/console-assets/%2e%2e/%2e%2e/secret.txt"):
            response = client.get(path)
            assert response.status_code == 404 and "must-not-serve" not in response.text
        assert client.get("/test/", headers={"Host": "rebind.invalid"}).status_code == 403
