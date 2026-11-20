"""HTTP integration tests; use an isolated SQLite database and no external model traffic."""

import json
import time
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from allira.app import create_app
from allira.config import Settings


@pytest.fixture
def client(tmp_path):
    settings = Settings(database_path=str(tmp_path / "test.sqlite3"), allow_dev_auth=True)
    with TestClient(create_app(settings)) as client:
        yield client


def auth(client, user="alice"):
    response = client.post("/api/v1/dev/session", json={"userId": user})
    assert response.status_code == 200
    session = response.json()
    assert session["tokenType"] == "Bearer"
    return {"Authorization": f"Bearer {session['accessToken']}"}


def conversation(client, headers, **body):
    response = client.post("/api/v1/conversations", headers=headers, json=body)
    assert response.status_code == 201
    return response.json()


def run(client, headers, conversation_id, content="你好🌍", client_id=None):
    response = client.post(
        f"/api/v1/conversations/{conversation_id}/runs", headers=headers,
        json={"clientMessageId": client_id or str(uuid4()), "content": content},
    )
    assert response.status_code == 202, response.text
    return response.json()


def terminal(client, headers, run_id):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        result = client.get(f"/api/v1/runs/{run_id}", headers=headers).json()
        if result["status"] != "running":
            return result
    pytest.fail("Mock run did not finish within three seconds.")


def events(response):
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["x-accel-buffering"] == "no"
    result = []
    for block in response.text.split("\n\n"):
        fields = dict(line.split(": ", 1) for line in block.splitlines() if ": " in line)
        if "data" in fields:
            result.append({"id": int(fields["id"]), "type": fields["event"],
                           "data": json.loads(fields["data"])})
    return result


def test_health_and_auth_defaults(tmp_path):
    with TestClient(create_app(Settings(database_path=str(tmp_path / "no-dev.sqlite3")))) as client:
        assert client.get("/healthz").json() == {"status": "ok"}
        assert client.post("/api/v1/dev/session", json={"userId": "alice"}).status_code == 404
        assert "/api/v1/dev/session" not in client.get("/openapi.json").json()["paths"]
        response = client.get("/api/v1/conversations")
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "unauthorized"
        assert client.get("/api/v1/conversations", headers={"Authorization": "Bearer bad"}).status_code == 401
    with pytest.raises(ValidationError):
        Settings(environment="production", allow_dev_auth=True)
    with pytest.raises(ValidationError):
        Settings(cors_origins=["*"])
    with pytest.raises(ValidationError):
        Settings(provider="openai", upstream_model="")


def test_full_chat_idempotency_pagination_and_replay(client):
    headers = auth(client)
    chat = conversation(client, headers, title=" 我的角色 ", instructions="Be kind.")
    assert chat["title"] == "我的角色"
    assert chat["instructions"] == "Be kind."
    assert chat["createdAt"].endswith("Z")
    client_id = str(uuid4())
    created = run(client, headers, chat["id"], client_id=client_id)
    finished = terminal(client, headers, created["id"])
    assert finished["status"] == "completed"
    assert finished["errorCode"] is None
    replay = run(client, headers, chat["id"], client_id=client_id)
    assert replay["id"] == created["id"]
    response = client.post(
        f"/api/v1/conversations/{chat['id']}/runs", headers=headers,
        json={"clientMessageId": client_id, "content": "different"},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "idempotency_conflict"
    page = client.get(f"/api/v1/conversations/{chat['id']}/messages?limit=1", headers=headers).json()
    assert len(page["items"]) == 1
    assert page["items"][0]["content"] == "你好🌍"
    assert page["items"][0]["sequence"] == 1
    next_page = client.get(
        f"/api/v1/conversations/{chat['id']}/messages", headers=headers,
        params={"limit": 1, "cursor": page["nextCursor"]},
    ).json()
    assert next_page["nextCursor"] is None
    assert next_page["items"][0]["content"] == "Mock response."
    assert next_page["items"][0]["sequence"] == 2
    streamed = events(client.get(f"/api/v1/runs/{created['id']}/events", headers=headers))
    assert [event["id"] for event in streamed] == [1, 2, 3, 4]
    assert [event["type"] for event in streamed] == [
        "run.started", "message.delta", "message.delta", "run.completed",
    ]
    assert streamed[-1]["data"]["run"] == finished
    assert streamed[-1]["data"]["message"] == next_page["items"][0]
    resumed = events(client.get(f"/api/v1/runs/{created['id']}/events?after=2", headers=headers))
    assert resumed == streamed[2:]
    assert events(client.get(f"/api/v1/runs/{created['id']}/events?after=4", headers=headers)) == []
    assert client.get(f"/api/v1/runs/{created['id']}/events?after=99", headers=headers).status_code == 400
    assert client.post(f"/api/v1/runs/{created['id']}/cancel", headers=headers).json() == finished


def test_user_isolation_and_cursor_validation(client):
    alice, bob = auth(client, "alice"), auth(client, "bob")
    chat = conversation(client, alice)
    created = run(client, alice, chat["id"])
    terminal(client, alice, created["id"])
    assert client.get("/api/v1/conversations", headers=bob).json()["items"] == []
    for path in (
        f"/api/v1/conversations/{chat['id']}",
        f"/api/v1/conversations/{chat['id']}/messages",
        f"/api/v1/runs/{created['id']}", f"/api/v1/runs/{created['id']}/events",
    ):
        assert client.get(path, headers=bob).status_code == 404
    assert client.post(f"/api/v1/runs/{created['id']}/cancel", headers=bob).status_code == 404
    assert client.post(
        f"/api/v1/conversations/{chat['id']}/runs", headers=bob,
        json={"clientMessageId": str(uuid4()), "content": "stolen"},
    ).status_code == 404
    assert client.get(
        f"/api/v1/conversations/{chat['id']}/messages?cursor=missing", headers=alice,
    ).status_code == 400
    assert client.get("/api/v1/conversations?cursor=missing", headers=alice).status_code == 400


def test_stable_conversation_pagination(client):
    headers = auth(client)
    expected = {conversation(client, headers)["id"] for _ in range(4)}
    found, cursor = [], None
    for _ in range(4):
        page = client.get(
            "/api/v1/conversations", headers=headers,
            params={"limit": 1, **({"cursor": cursor} if cursor else {})},
        ).json()
        found.extend(item["id"] for item in page["items"])
        cursor = page["nextCursor"]
    assert set(found) == expected
    assert found == sorted(found)
    assert cursor is None


def test_validation_and_errors_never_reflect_input(client):
    headers = auth(client)
    chat = conversation(client, headers)
    secret = "do-not-reflect-a-secret"
    response = client.post(
        f"/api/v1/conversations/{chat['id']}/runs", headers=headers,
        json={"clientMessageId": secret, "content": "hi", "upstreamApiKey": secret},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
    assert secret not in response.text
    for body in ({"title": "  "}, {"title": "x" * 201}):
        assert client.post("/api/v1/conversations", headers=headers, json=body).status_code == 422
    for value in ("", " ", "x" * 20001):
        assert client.post(
            f"/api/v1/conversations/{chat['id']}/runs", headers=headers,
            json={"clientMessageId": str(uuid4()), "content": value},
        ).status_code == 422
    for query in ("limit=0", "limit=101"):
        assert client.get(f"/api/v1/conversations?{query}", headers=headers).status_code == 422
    assert client.get("/api/v1/runs/missing/events?after=-1", headers=headers).status_code == 422


def test_large_replay_drains_past_database_batch_limit(tmp_path):
    class ManyChunks:
        async def stream(self, messages):
            for _ in range(125):
                yield "你"

    settings = Settings(database_path=str(tmp_path / "many.sqlite3"), allow_dev_auth=True)
    with TestClient(create_app(settings, ManyChunks())) as client:
        headers = auth(client)
        chat = conversation(client, headers)
        created = run(client, headers, chat["id"])
        streamed = events(client.get(f"/api/v1/runs/{created['id']}/events", headers=headers))
        assert len(streamed) == 127
        assert streamed[-1]["type"] == "run.completed"
        assert streamed[-1]["data"]["message"]["content"] == "你" * 125


def test_terminal_conversation_deleted_during_replay_closes_without_stream_error(tmp_path, monkeypatch):
    settings = Settings(database_path=str(tmp_path / "deleted-replay.sqlite3"), allow_dev_auth=True)
    app = create_app(settings)
    with TestClient(app) as client:
        headers = auth(client)
        chat = conversation(client, headers)
        created = run(client, headers, chat["id"])
        terminal(client, headers, created["id"])
        original_events = app.state.db.events

        def delete_after_read(run_id, after):
            batch = original_events(run_id, after)
            app.state.db.delete_conversation("alice", chat["id"])
            return batch

        monkeypatch.setattr(app.state.db, "events", delete_after_read)
        streamed = events(client.get(f"/api/v1/runs/{created['id']}/events", headers=headers))
        assert streamed[-1]["type"] == "run.completed"
        assert client.get(f"/api/v1/runs/{created['id']}", headers=headers).status_code == 404


def test_cors_is_explicit(tmp_path):
    settings = Settings(
        database_path=str(tmp_path / "cors.sqlite3"), cors_origins=["http://localhost:5173"],
    )
    with TestClient(create_app(settings)) as client:
        headers = {"Origin": "http://localhost:5173", "Access-Control-Request-Method": "POST",
                   "Access-Control-Request-Headers": "authorization,content-type"}
        response = client.options("/api/v1/conversations", headers=headers)
        assert response.status_code == 200
        assert response.headers["access-control-allow-origin"] == "http://localhost:5173"
        headers["Origin"] = "https://untrusted.example"
        assert client.options("/api/v1/conversations", headers=headers).status_code == 400


def test_restart_keeps_session_and_messages(tmp_path):
    settings = Settings(database_path=str(tmp_path / "persist.sqlite3"), allow_dev_auth=True)
    with TestClient(create_app(settings)) as first:
        headers = auth(first)
        chat = conversation(first, headers)
        created = run(first, headers, chat["id"])
        terminal(first, headers, created["id"])
    with TestClient(create_app(settings)) as second:
        assert second.get(f"/api/v1/runs/{created['id']}", headers=headers).json()["status"] == "completed"
        assert len(second.get(f"/api/v1/conversations/{chat['id']}/messages", headers=headers).json()["items"]) == 2
