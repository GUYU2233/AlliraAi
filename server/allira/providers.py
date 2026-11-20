"""Server-configured providers for streaming assistant text."""

import codecs
import json
import re
from collections.abc import AsyncGenerator, Sequence
from typing import Literal, Protocol, TypedDict

import httpx


class PromptMessage(TypedDict):
    role: Literal["system", "user", "assistant"]
    content: str


class ModelProvider(Protocol):
    def stream(self, messages: Sequence[PromptMessage]) -> AsyncGenerator[str, None]: ...


class ProviderError(RuntimeError):
    """A model provider failed to deliver a complete response."""


class MockProvider:
    """Deterministic local provider for development and tests."""

    async def stream(self, messages: Sequence[PromptMessage]) -> AsyncGenerator[str, None]:
        yield "Mock "
        yield "response."


_FINISH_REASONS = {"stop", "length", "content_filter", "tool_calls", "function_call"}
_INVALID_EVENT = "Invalid response from model provider."
# Bound untrusted upstream framing before JSON parsing or public text output limits.
_MAX_LINE_CHARACTERS = 262144
_MAX_EVENT_CHARACTERS = 1048576
_MAX_STREAM_BYTES = 8388608


def _parse_event(data: str) -> tuple[str | None, bool]:
    if data.strip() == "[DONE]":
        return None, True

    try:
        payload = json.loads(data)
    except (ValueError, RecursionError):
        payload = None
    if not isinstance(payload, dict) or payload.get("error") is not None:
        raise ProviderError(_INVALID_EVENT)

    if "choices" not in payload and isinstance(payload.get("usage"), dict):
        return None, False
    choices = payload.get("choices")
    if not isinstance(choices, list) or (
        not choices and not isinstance(payload.get("usage"), dict)
    ):
        raise ProviderError(_INVALID_EVENT)

    for position, choice in enumerate(choices):
        if not isinstance(choice, dict):
            raise ProviderError(_INVALID_EVENT)
        index = choice.get("index", position)
        if type(index) is not int or index < 0:
            raise ProviderError(_INVALID_EVENT)
        if index != 0:
            continue

        finish_reason = choice.get("finish_reason")
        if finish_reason is not None and (
            not isinstance(finish_reason, str) or finish_reason not in _FINISH_REASONS
        ):
            raise ProviderError(_INVALID_EVENT)

        delta = choice.get("delta")
        if delta is None:
            if finish_reason is None:
                raise ProviderError(_INVALID_EVENT)
            return None, True
        if not isinstance(delta, dict):
            raise ProviderError(_INVALID_EVENT)
        text = delta.get("content")
        if text is not None and not isinstance(text, str):
            raise ProviderError(_INVALID_EVENT)
        return text, finish_reason is not None

    return None, False


async def _utf8_lines(response: httpx.Response) -> AsyncGenerator[str, None]:
    """Decode strictly, preserving SSE line endings across arbitrary byte splits."""
    decoder = codecs.getincrementaldecoder("utf-8-sig")("strict")
    pending = ""
    received = 0
    async for chunk in response.aiter_bytes():
        received += len(chunk)
        if received > _MAX_STREAM_BYTES:
            raise ProviderError(_INVALID_EVENT)
        pending += decoder.decode(chunk)
        while match := re.search(r"\r\n|\r|\n", pending):
            if match.start() > _MAX_LINE_CHARACTERS:
                raise ProviderError(_INVALID_EVENT)
            # A trailing CR might be the first half of CRLF in the next chunk.
            if match.group() == "\r" and match.end() == len(pending):
                break
            yield pending[: match.start()]
            pending = pending[match.end() :]
        if len(pending) > _MAX_LINE_CHARACTERS:
            raise ProviderError(_INVALID_EVENT)
    pending += decoder.decode(b"", final=True)
    for line in re.split(r"\r\n|\r|\n", pending)[:-1]:
        yield line
    # Do not dispatch an unterminated line/event at EOF.


class OpenAICompatibleProvider:
    """Stream chat completions using an application-owned HTTP client."""

    def __init__(
        self, base_url: str, model: str, api_key: str | None, *, client: httpx.AsyncClient,
        max_tokens: int | None = None,
    ) -> None:
        self._url = f"{base_url.rstrip('/')}/chat/completions"
        self._model = model
        self._api_key = api_key
        self._client = client
        if max_tokens is not None and (type(max_tokens) is not int or not 1 <= max_tokens <= 16384):
            raise ValueError("Provider output token limit is not valid.")
        self._max_tokens = max_tokens

    async def stream(self, messages: Sequence[PromptMessage]) -> AsyncGenerator[str, None]:
        headers = {"Accept": "text/event-stream", "User-Agent": "AlliraAi-Server/0.3"}
        if self._api_key is not None:
            headers["Authorization"] = f"Bearer {self._api_key}"

        try:
            async with self._client.stream(
                "POST",
                self._url,
                json={"model": self._model, "messages": list(messages), "stream": True,
                      **({"max_tokens": self._max_tokens} if self._max_tokens is not None else {})},
                headers=headers,
                follow_redirects=False,
            ) as response:
                if not response.is_success:
                    raise ProviderError("Model provider rejected the request.")
                data_lines: list[str] = []
                event_characters = 0
                async for line in _utf8_lines(response):
                    if line:
                        field, _, value = line.partition(":")
                        if field == "data":
                            event_characters += len(value) + 1
                            if event_characters > _MAX_EVENT_CHARACTERS:
                                raise ProviderError(_INVALID_EVENT)
                            data_lines.append(value.removeprefix(" "))
                    elif data_lines:
                        # An SSE frame is only complete after its blank separator.
                        text, finished = _parse_event("\n".join(data_lines))
                        data_lines.clear()
                        event_characters = 0
                        if text:
                            yield text
                        if finished:
                            return
        except UnicodeError:
            failure = _INVALID_EVENT
        except (httpx.HTTPError, httpx.InvalidURL):
            failure = "Model provider connection failed."
        else:
            failure = "Model provider stream ended before completion."
        raise ProviderError(failure)
