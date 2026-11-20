"""Opt-in SMS adapters for a trusted gateway, never a vendor-specific SMS4J API.

Disabled is the default. Tests inject an in-memory sender or use an explicitly private
local outbox. Delivery errors contain no destination, code, gateway URL or response body.
"""

import asyncio
import json
import os
import re
import stat
from pathlib import Path
from typing import Protocol
from urllib.parse import urlsplit

import httpx

from .config import Settings

SMS_CODE_TTL_SECONDS = 300
SMS_RESPONSE_MAX_BYTES = 64 * 1024
SMS_TIMEOUT_SECONDS = 15
_PURPOSES = frozenset({"login", "register", "bind", "social_bind", "set_password", "erase"})


class SmsDeliveryError(Exception):
    def __init__(self) -> None:
        super().__init__("SMS delivery is unavailable.")


class SmsSender(Protocol):
    async def send(self, phone: str, code: str, purpose: str) -> None: ...


class DisabledSmsSender:
    async def send(self, phone: str, code: str, purpose: str) -> None:
        raise SmsDeliveryError()


def _valid_delivery(phone: str, code: str, purpose: str) -> bool:
    return (
        isinstance(phone, str) and re.fullmatch(r"\+861[3-9][0-9]{9}", phone) is not None
        and isinstance(code, str) and re.fullmatch(r"[0-9]{6}", code) is not None
        and purpose in _PURPOSES
    )


def _gateway_url(value: str) -> str:
    """Configuration-only URL: HTTPS DNS host, no credentials, query or local names."""
    try:
        parsed = urlsplit(value)
        host = parsed.hostname or ""
        labels = host.split(".")
        valid = (
            isinstance(value, str) and 1 <= len(value) <= 2048 and value.isascii()
            and not any(character.isspace() or ord(character) < 32 for character in value)
            and parsed.scheme == "https" and parsed.username is None and parsed.password is None
            and not parsed.query and not parsed.fragment and "?" not in value and "#" not in value
            and parsed.port in {None, 443} and not parsed.netloc.endswith(":")
            and len(host) <= 253 and len(labels) >= 2
            and all(re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", label)
                    for label in labels)
            and re.fullmatch(r"[A-Za-z]{2,63}", labels[-1]) is not None
            and not host.lower().endswith((".localhost", ".local", ".internal", ".lan", ".home", ".test", ".invalid"))
            and "\\" not in value
        )
    except (AttributeError, TypeError, ValueError):
        valid = False
    if not valid:
        raise ValueError("Invalid SMS gateway configuration.")
    return value


class GatewaySmsSender:
    """Fixed, trusted Allira gateway contract; this is not a generic URL fetcher.

    The configured DNS host must be controlled by the operator. No destinations or
    redirects from client requests or gateway responses are ever followed.
    """

    def __init__(self, url: str, key: str, *, client: httpx.AsyncClient | None = None) -> None:
        self._url = _gateway_url(url)
        if (
            not isinstance(key, str) or not 1 <= len(key) <= 4096 or not key.isascii()
            or any(not 33 <= ord(character) <= 126 for character in key)
        ):
            raise ValueError("Invalid SMS gateway configuration.")
        self._key, self._client = key, client

    async def _send(self, client: httpx.AsyncClient, phone: str, code: str, purpose: str) -> None:
        async with client.stream(
            "POST", self._url,
            headers={"Authorization": "Bearer " + self._key, "Accept-Encoding": "identity"},
            json={"phone": phone, "code": code, "purpose": purpose,
                  "expiresInSeconds": SMS_CODE_TTL_SECONDS},
            follow_redirects=False,
            timeout=httpx.Timeout(10, connect=5, pool=5),
        ) as response:
            if not 200 <= response.status_code < 300:
                raise SmsDeliveryError()
            if response.headers.get("content-encoding", "identity").lower() != "identity":
                raise SmsDeliveryError()
            length = response.headers.get("content-length")
            if length is not None and (not length.isascii() or not length.isdecimal()
                                       or len(length) > 10 or int(length) > SMS_RESPONSE_MAX_BYTES):
                raise SmsDeliveryError()
            data = bytearray()
            async for chunk in response.aiter_bytes(chunk_size=4096):
                if len(data) + len(chunk) > SMS_RESPONSE_MAX_BYTES:
                    raise SmsDeliveryError()
                data.extend(chunk)
            result = json.loads(data)
            if not isinstance(result, dict) or result.get("accepted") is not True:
                raise SmsDeliveryError()

    async def send(self, phone: str, code: str, purpose: str) -> None:
        try:
            if not _valid_delivery(phone, code, purpose):
                raise SmsDeliveryError()
            async with asyncio.timeout(SMS_TIMEOUT_SECONDS):
                if self._client is not None:
                    await self._send(self._client, phone, code, purpose)
                else:
                    async with httpx.AsyncClient(trust_env=False, follow_redirects=False) as client:
                        await self._send(client, phone, code, purpose)
        except (httpx.HTTPError, TimeoutError, ValueError, RuntimeError, SmsDeliveryError):
            # Never expose chained HTTP errors, raw response bodies, URLs or codes.
            raise SmsDeliveryError() from None


class TestFileSmsSender:
    """Development/Mock-only private JSONL outbox, not an SMS delivery mechanism."""

    __test__ = False

    def __init__(self, settings: Settings) -> None:
        if (
            settings.environment != "development" or settings.provider != "mock"
            or getattr(settings, "sms_provider", "disabled") != "test-file"
        ):
            raise ValueError("The test SMS outbox requires development Mock mode.")
        try:
            root = str(settings.sms_test_outbox_root)
            path = str(settings.sms_test_outbox_path)
            if (
                not root.startswith("/") or not path.startswith("/")
                or os.path.normpath(root) != root or os.path.normpath(path) != path
                or "\x00" in root or "\x00" in path or root == "/"
            ):
                raise ValueError
            relative = Path(path).relative_to(root)
            if not relative.parts or any(part in {"", ".", ".."} for part in relative.parts):
                raise ValueError
            self._root, self._path = root, path
            # Validate eagerly, but re-open/validate securely on every append as well.
            descriptor = self._open_outbox()
            os.close(descriptor)
        except (AttributeError, OSError, TypeError, ValueError):
            raise ValueError("Invalid private test SMS outbox configuration.") from None

    def _open_outbox(self) -> int:
        directory = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
        try:
            root_parts = Path(self._root).parts[1:]
            file_parts = Path(self._path).parts[1:]
            for index, part in enumerate(file_parts[:-1]):
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                                dir_fd=directory)
                os.close(directory)
                directory = child
                if index >= len(root_parts) - 1:
                    info = os.fstat(directory)
                    if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
                        raise OSError("Unsafe private directory.")
            descriptor = os.open(
                file_parts[-1], os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW
                | os.O_CLOEXEC | os.O_NONBLOCK, 0o600, dir_fd=directory,
            )
            try:
                info = os.fstat(descriptor)
                if (
                    not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                    or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o600
                ):
                    raise OSError("Unsafe private outbox.")
            except BaseException:
                os.close(descriptor)
                raise
            return descriptor
        finally:
            os.close(directory)

    async def send(self, phone: str, code: str, purpose: str) -> None:
        try:
            if not _valid_delivery(phone, code, purpose):
                raise SmsDeliveryError()
            data = (json.dumps({"phone": phone, "code": code, "purpose": purpose,
                                "expiresInSeconds": SMS_CODE_TTL_SECONDS},
                               ensure_ascii=True, separators=(",", ":")) + "\n").encode("ascii")
            descriptor = self._open_outbox()
            try:
                if os.write(descriptor, data) != len(data):
                    raise SmsDeliveryError()
            finally:
                os.close(descriptor)
        except (OSError, ValueError, SmsDeliveryError):
            raise SmsDeliveryError() from None


def create_sms_sender(settings: Settings) -> SmsSender:
    provider = getattr(settings, "sms_provider", "disabled")
    if provider == "gateway":
        return GatewaySmsSender(settings.sms_gateway_url, settings.sms_gateway_token)
    if provider == "test-file":
        return TestFileSmsSender(settings)
    if provider != "disabled":
        raise ValueError("Unsupported SMS provider.")
    return DisabledSmsSender()
