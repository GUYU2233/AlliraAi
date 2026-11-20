"""Request size/encoding guards run before JSON parsing and never echo payloads."""

import asyncio

from fastapi.testclient import TestClient

from allira.app import create_app
from allira.config import Settings
from allira.http_limits import RequestBodyLimit


def test_wire_limit_invalid_length_and_compressed_request(tmp_path):
    with TestClient(create_app(Settings(_env_file=None, database_path=str(tmp_path / "limits.sqlite3")))) as client:
        for headers, content, expected in [
            ({"Content-Length": "2097153"}, b"", 413),
            ({"Content-Length": "not-a-number"}, b"", 400),
            ({"Content-Encoding": "gzip"}, b"compressed-secret", 415),
        ]:
            response = client.post("/api/v1/auth/login", content=content, headers=headers)
            assert response.status_code == expected
            assert "compressed-secret" not in response.text
            assert response.headers["cache-control"] == "no-store"
        assert client.get("/healthz").status_code == 200


def test_chunked_body_is_bounded_without_content_length():
    async def check():
        reached = False
        async def app(scope, receive, send):
            nonlocal reached
            reached = True
        chunks = iter([
            {"type": "http.request", "body": b"abc", "more_body": True},
            {"type": "http.request", "body": b"def", "more_body": False},
        ])
        responses = []
        async def receive():
            return next(chunks)
        async def send(message):
            responses.append(message)
        await RequestBodyLimit(app, max_bytes=5)({"type": "http", "method": "POST", "headers": []}, receive, send)
        assert not reached
        assert responses[0]["status"] == 413
    asyncio.run(check())


def test_bounded_body_replayed_once_and_disconnect_passes_through():
    async def check():
        received = []
        async def app(scope, receive, send):
            received.append(await receive())
            received.append(await receive())
        chunks = iter([
            {"type": "http.request", "body": b"ab", "more_body": True},
            {"type": "http.request", "body": b"c", "more_body": False},
            {"type": "http.disconnect"},
        ])
        async def receive():
            return next(chunks)
        async def send(message):
            raise AssertionError("application did not send")
        await RequestBodyLimit(app, max_bytes=5)({"type": "http", "method": "POST", "headers": []}, receive, send)
        assert received == [{"type": "http.request", "body": b"abc", "more_body": False}, {"type": "http.disconnect"}]
    asyncio.run(check())
