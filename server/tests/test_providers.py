"""Provider contract tests: all upstream traffic stays inside MockTransport."""

import asyncio
import json

import httpx
import pytest

from allira.providers import MockProvider, OpenAICompatibleProvider, ProviderError

MESSAGES = [{"role": "user", "content": "hello"}]
SECRET = "upstream-secret-do-not-expose"


class ByteStream(httpx.AsyncByteStream):
    def __init__(self, chunks, error=None):
        self.chunks = chunks
        self.error = error
        self.closed = False

    async def __aiter__(self):
        for chunk in self.chunks:
            yield chunk
        if self.error:
            raise self.error

    async def aclose(self):
        self.closed = True


def event(payload):
    return ("data: " + json.dumps(payload, ensure_ascii=False) + "\n\n").encode()


def delta(content=None, finish=None):
    return {
        "choices": [
            {"index": 0, "delta": {"content": content}, "finish_reason": finish}
        ]
    }


def collect(chunks, *, status=200, error=None, headers=None, requests=None):
    stream = ByteStream(chunks, error)

    def handler(request):
        if requests is not None:
            requests.append(request)
        return httpx.Response(status, stream=stream, headers=headers)

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler), follow_redirects=True
        ) as client:
            provider = OpenAICompatibleProvider(
                "https://models.example/v1/", "test-model", SECRET, client=client
            )
            return [text async for text in provider.stream(MESSAGES)]

    return run, stream


def assert_safe_error(run, message=None):
    with pytest.raises(ProviderError) as caught:
        asyncio.run(run())
    assert SECRET not in str(caught.value)
    # No chained transport exception may leak its URL, headers, or body either.
    assert caught.value.__context__ is None
    if message:
        assert str(caught.value) == message


def test_mock_contract():
    async def run():
        return [text async for text in MockProvider().stream(MESSAGES)]

    assert asyncio.run(run()) == ["Mock ", "response."]


def test_normal_tokens_done_and_request_contract():
    requests = []
    run, stream = collect(
        [event(delta("Hello ")), event(delta("world")), b"data: [DONE]\n\n"],
        requests=requests,
    )
    assert asyncio.run(run()) == ["Hello ", "world"]
    assert stream.closed
    request = requests[0]
    assert request.method == "POST"
    assert str(request.url) == "https://models.example/v1/chat/completions"
    assert request.headers["Authorization"] == f"Bearer {SECRET}"
    assert request.headers["Accept"] == "text/event-stream"
    assert json.loads(request.content) == {
        "model": "test-model", "messages": MESSAGES, "stream": True
    }


@pytest.mark.parametrize("reason", ["stop", "length", "content_filter", "tool_calls", "function_call"])
def test_finish_reason_completes_without_done(reason):
    run, stream = collect([event(delta("last", reason))])
    assert asyncio.run(run()) == ["last"]
    assert stream.closed


def test_empty_finish_delta_is_allowed():
    run, _ = collect([event({"choices": [{"finish_reason": "stop"}]})])
    assert asyncio.run(run()) == []


def test_ignore_role_reasoning_usage_and_other_choices():
    run, _ = collect([
        event({"choices": [{"delta": {"role": "assistant"}}]}),
        event({"choices": [{"delta": {"reasoning_content": SECRET}}]}),
        event({"choices": [{"index": 1, "delta": {"content": SECRET}}]}),
        event({"choices": [], "usage": {"total_tokens": 3}}),
        event({"usage": {"total_tokens": 3}}),
        event(delta("visible")),
        b"data: [DONE]\n\n",
    ])
    assert asyncio.run(run()) == ["visible"]


@pytest.mark.parametrize("ending", [b"\n", b"\r\n", b"\r"])
def test_utf8_bom_multiline_framing_and_arbitrary_byte_splits(ending):
    data = (
        b"\xef\xbb\xbf: keepalive" + ending + ending
        + b"event: message" + ending
        + b"id: 1" + ending
        + b'data: {"choices":' + ending
        + 'data: [{"delta":{"content":"你好 🌍"}}]}'.encode() + ending + ending
        + b"data:[DONE]" + ending + ending
    )
    run, stream = collect([bytes([byte]) for byte in data])
    assert asyncio.run(run()) == ["你好 🌍"]
    assert stream.closed


@pytest.mark.parametrize("payload", [
    None, [], "bad", {"error": {"message": SECRET}}, {},
    {"choices": None}, {"choices": {}}, {"choices": []},
    {"choices": [None]}, {"choices": [{}]},
    {"choices": [{"index": True, "delta": {}}]},
    {"choices": [{"index": -1, "delta": {}}]},
    {"choices": [{"delta": "bad"}]},
    {"choices": [{"delta": {"content": [SECRET]}}]},
    {"choices": [{"delta": {}, "finish_reason": 123}]},
    {"choices": [{"delta": {}, "finish_reason": SECRET}]},
])
def test_malformed_chunks_rejected(payload):
    run, stream = collect([event(payload), b"data: [DONE]\n\n"])
    assert_safe_error(run, "Invalid response from model provider.")
    assert stream.closed


@pytest.mark.parametrize("data", [
    b"data: not-json\n\n",
    b'data: {"choices":[{"delta":{"content":"\xff"}}]}\n\n',
    b"data: \xf0\x9f",
])
def test_invalid_json_and_utf8_rejected(data):
    run, stream = collect([data])
    assert_safe_error(run, "Invalid response from model provider.")
    assert stream.closed


@pytest.mark.parametrize("chunks", [
    [], [b": heartbeat\n\n"], [event(delta("partial"))],
    [b"data: [DONE]"], [b"data: [DONE]\n"],
    [event(delta("partial")), b"data: [DONE]\r"],
])
def test_premature_eof_and_unterminated_events_rejected(chunks):
    run, stream = collect(chunks)
    assert_safe_error(run, "Model provider stream ended before completion.")
    assert stream.closed


@pytest.mark.parametrize("status", [301, 302, 307, 400, 401, 429, 500])
def test_http_errors_are_safe_and_redirects_not_followed(status):
    requests = []
    run, stream = collect(
        [SECRET.encode()], status=status,
        headers={"Location": f"https://other.example/{SECRET}"}, requests=requests,
    )
    assert_safe_error(run, "Model provider rejected the request.")
    assert len(requests) == 1
    assert stream.closed


@pytest.mark.parametrize("error_type", [httpx.ReadError, httpx.ReadTimeout, httpx.RemoteProtocolError])
def test_midstream_transport_errors_are_safe_and_close_stream(error_type):
    run, stream = collect([event(delta("partial"))], error=error_type(SECRET))
    assert_safe_error(run, "Model provider connection failed.")
    assert stream.closed


def test_connect_error_is_safe():
    def handler(request):
        raise httpx.ConnectError(SECRET, request=request)

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            provider = OpenAICompatibleProvider(
                f"https://models.example/{SECRET}", "model", SECRET, client=client
            )
            return [text async for text in provider.stream(MESSAGES)]

    assert_safe_error(run, "Model provider connection failed.")


def test_aclose_closes_upstream_but_not_owned_client():
    async def run():
        stream = ByteStream([event(delta("first")), event(delta("second"))])
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, stream=stream))
        ) as client:
            provider = OpenAICompatibleProvider("https://models.example", "model", None, client=client)
            generator = provider.stream(MESSAGES)
            assert await anext(generator) == "first"
            assert not stream.closed
            await generator.aclose()
            assert stream.closed
            assert not client.is_closed

    asyncio.run(run())


def test_configured_output_token_limit_is_sent_to_upstream():
    requests = []
    async def run():
        def handler(request):
            requests.append(request)
            return httpx.Response(200, content=b"data: [DONE]\n\n")
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            provider = OpenAICompatibleProvider(
                "https://models.example/v1", "model", None, client=client, max_tokens=32,
            )
            assert [text async for text in provider.stream(MESSAGES)] == []
    asyncio.run(run())
    assert json.loads(requests[0].content)["max_tokens"] == 32


@pytest.mark.parametrize("chunks", [
    [b"data: " + b"x" * 262145],
    [b"data: " + b"x" * 200000 + b"\n"] * 6 + [b"\n"],
    [b":" + b"x" * 65530 + b"\n\n"] * 129,
])
def test_upstream_line_event_and_total_limits_close_untrusted_stream(chunks):
    run, stream = collect(chunks)
    assert_safe_error(run, "Invalid response from model provider.")
    assert stream.closed


def test_event_size_budget_resets_between_frames():
    run, stream = collect([event(delta("hello"))] * 3 + [b"data: [DONE]\n\n"])
    assert asyncio.run(run()) == ["hello", "hello", "hello"]
    assert stream.closed


def test_cancellation_closes_upstream():
    class BlockingStream(ByteStream):
        async def __aiter__(self):
            yield event(delta("first"))
            await asyncio.Event().wait()

    async def run():
        stream = BlockingStream([])
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, stream=stream))
        ) as client:
            provider = OpenAICompatibleProvider("https://models.example", "model", None, client=client)
            generator = provider.stream(MESSAGES)
            assert await anext(generator) == "first"
            task = asyncio.create_task(anext(generator))
            await asyncio.sleep(0)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert stream.closed
            assert not client.is_closed

    asyncio.run(run())
