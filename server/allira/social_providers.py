"""Small, fixed-endpoint Tencent website OAuth adapters.

No provider tokens are retained or returned. There is no PKCE claim: support for
these website flows has not been established. All HTTP is injected and testable.
"""

import asyncio
import ipaddress
import json
import re
import secrets
import time
import unicodedata
from dataclasses import dataclass, field
from typing import Literal, Protocol
from urllib.parse import parse_qsl, urlencode, urlsplit

import httpx

from .config import Settings
from .db import APIError

SocialProviderName = Literal["wechat", "qq"]
MAX_RESPONSE_BYTES = 64 * 1024
MAX_TEST_CODES = 4096


class SocialProviderError(APIError):
    def __init__(self) -> None:
        # Never attach an upstream URL, body, code, token or exception message.
        super().__init__(502, "social_provider_error", "Social authorization could not be verified.")


def _opaque(value: object, maximum: int = 256) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise SocialProviderError()
    if not value.isascii() or re.fullmatch(r"[A-Za-z0-9_.~-]+", value) is None:
        raise SocialProviderError()
    return value


def _display(value: object, provider: SocialProviderName) -> str:
    fallback = "微信用户" if provider == "wechat" else "QQ用户"
    if not isinstance(value, str):
        return fallback
    result = "".join(c for c in value if not unicodedata.category(c).startswith("C")).strip()
    result = result[:80]
    while len(result.encode("utf-8")) > 240:
        result = result[:-1]
    return result or fallback


@dataclass(frozen=True)
class VerifiedSocialIdentity:
    provider: SocialProviderName
    client_id: str
    subject: str = field(repr=False)
    display_name: str

    def __post_init__(self) -> None:
        if self.provider not in {"wechat", "qq"}:
            raise SocialProviderError()
        _opaque(self.client_id, 128)
        _opaque(self.subject)
        object.__setattr__(self, "display_name", _display(self.display_name, self.provider))


class SocialProvider(Protocol):
    client_id: str
    redirect_uri: str
    simulated: bool

    def build_authorize_url(self, state: str, redirect_uri: str) -> str: ...

    async def exchange_code(self, code: str, redirect_uri: str) -> VerifiedSocialIdentity: ...


def _pairs(values: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in values:
        if key in result:
            raise SocialProviderError()
        result[key] = value
    return result


def _json(text: str) -> dict:
    try:
        result = json.loads(text, object_pairs_hook=_pairs,
                            parse_constant=lambda _: (_ for _ in ()).throw(SocialProviderError()))
    except (ValueError, RecursionError):
        raise SocialProviderError() from None
    if not isinstance(result, dict):
        raise SocialProviderError()
    return result


def _decode(body: bytes, *, form: bool = False, jsonp: bool = False) -> dict:
    try:
        text = body.decode("utf-8").strip()
    except UnicodeDecodeError:
        raise SocialProviderError() from None
    if jsonp:
        wrapper = re.fullmatch(r"callback\s*\(\s*(\{.*\})\s*\)\s*;?", text, re.DOTALL)
        if wrapper is not None:
            text = wrapper[1]
    if text.startswith("{"):
        return _json(text)
    if form:
        try:
            rows = parse_qsl(text, keep_blank_values=True, strict_parsing=True,
                             max_num_fields=20, encoding="utf-8", errors="strict")
        except (ValueError, UnicodeError):
            raise SocialProviderError() from None
        return _pairs(rows)
    raise SocialProviderError()


def _callback(value: str, provider: SocialProviderName, *, simulated: bool = False) -> str:
    try:
        parsed = urlsplit(value)
        host = parsed.hostname or ""
        port = parsed.port
        valid = (
            value.isascii() and bool(host) and parsed.username is None and parsed.password is None
            and "?" not in value and "#" not in value and "\\" not in value
            and parsed.path == f"/api/v1/auth/social/{provider}/callback"
            and not any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in value)
        )
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            address = None
        if simulated:
            valid = (valid and parsed.scheme in {"http", "https"}
                     and (host == "localhost" or (address is not None and address.is_loopback))
                     and not parsed.netloc.endswith(":") and (port is None or port > 0))
        else:
            is_ip = address is not None
            labels = host.split(".")
            valid = (valid and parsed.scheme == "https" and port is None and not is_ip
                     and host != "localhost"
                     and not host.endswith((".localhost", ".local", ".internal", ".invalid", ".test"))
                     and len(labels) >= 2 and len(host) <= 253
                     and all(re.fullmatch(r"[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?", label)
                             for label in labels)
                     and parsed.netloc == host)
    except (ValueError, AttributeError):
        valid = False
    if not valid:
        raise SocialProviderError()
    return value


class TencentSocialProvider:
    simulated = False

    def __init__(self, provider: SocialProviderName, client_id: str, client_secret: str,
                 redirect_uri: str, client: httpx.AsyncClient) -> None:
        if provider not in {"wechat", "qq"} or not isinstance(client_secret, str) or not client_secret:
            raise SocialProviderError()
        self.provider = provider
        self.client_id = _opaque(client_id, 128)
        self._secret = client_secret
        self.redirect_uri = _callback(redirect_uri, provider)
        self._client = client

    def _redirect(self, redirect_uri: str) -> None:
        if redirect_uri != self.redirect_uri:
            raise SocialProviderError()

    def build_authorize_url(self, state: str, redirect_uri: str) -> str:
        self._redirect(redirect_uri)
        _opaque(state, 128)
        if self.provider == "wechat":
            params = {"appid": self.client_id, "redirect_uri": redirect_uri,
                      "response_type": "code", "scope": "snsapi_login", "state": state}
            return "https://open.weixin.qq.com/connect/qrconnect?" + urlencode(params) + "#wechat_redirect"
        params = {"client_id": self.client_id, "redirect_uri": redirect_uri,
                  "response_type": "code", "scope": "get_user_info", "state": state}
        return "https://graph.qq.com/oauth2.0/authorize?" + urlencode(params)

    async def _get(self, url: str, params: dict, *, form: bool = False,
                   jsonp: bool = False) -> dict:
        try:
            async with asyncio.timeout(15):
                async with self._client.stream("GET", url, params=params, timeout=15,
                                               headers={"Accept-Encoding": "identity"},
                                               follow_redirects=False) as response:
                    if response.status_code != 200:
                        raise SocialProviderError()
                    if response.headers.get("content-encoding", "identity").lower() != "identity":
                        raise SocialProviderError()
                    advertised = response.headers.get("content-length")
                    if advertised is not None and (re.fullmatch(r"[0-9]{1,10}", advertised) is None
                                                   or int(advertised) > MAX_RESPONSE_BYTES):
                        raise SocialProviderError()
                    body = bytearray()
                    async for part in response.aiter_bytes():
                        if len(body) + len(part) > MAX_RESPONSE_BYTES:
                            raise SocialProviderError()
                        body.extend(part)
                return _decode(bytes(body), form=form, jsonp=jsonp)
        except (httpx.HTTPError, TimeoutError, ValueError, UnicodeError, RecursionError):
            raise SocialProviderError() from None

    async def exchange_code(self, code: str, redirect_uri: str) -> VerifiedSocialIdentity:
        try:
            async with asyncio.timeout(15):
                return await self._exchange_code(code, redirect_uri)
        except TimeoutError:
            raise SocialProviderError() from None

    async def _exchange_code(self, code: str, redirect_uri: str) -> VerifiedSocialIdentity:
        self._redirect(redirect_uri)
        _opaque(code, 2048)
        if self.provider == "wechat":
            token = await self._get("https://api.weixin.qq.com/sns/oauth2/access_token", {
                "appid": self.client_id, "secret": self._secret, "code": code,
                "grant_type": "authorization_code",
            })
            if "errcode" in token:
                raise SocialProviderError()
            access = _opaque(token.get("access_token"), 4096)
            subject = _opaque(token.get("openid"))
            profile = await self._get("https://api.weixin.qq.com/sns/userinfo", {
                "access_token": access, "openid": subject, "lang": "zh_CN",
            })
            if "errcode" in profile or profile.get("openid") != subject:
                raise SocialProviderError()
        else:
            token = await self._get("https://graph.qq.com/oauth2.0/token", {
                "grant_type": "authorization_code", "client_id": self.client_id,
                "client_secret": self._secret, "code": code, "redirect_uri": redirect_uri,
                "fmt": "json",
            }, form=True)
            if "error" in token or "errcode" in token:
                raise SocialProviderError()
            access = _opaque(token.get("access_token"), 4096)
            identity = await self._get("https://graph.qq.com/oauth2.0/me", {
                "access_token": access, "fmt": "json",
            }, jsonp=True)
            if ("error" in identity or "errcode" in identity
                    or identity.get("client_id") != self.client_id):
                raise SocialProviderError()
            subject = _opaque(identity.get("openid"))
            profile = await self._get("https://graph.qq.com/user/get_user_info", {
                "access_token": access, "oauth_consumer_key": self.client_id, "openid": subject,
            })
            if ("error" in profile or "errcode" in profile
                    or type(profile.get("ret")) is not int or profile["ret"] != 0):
                raise SocialProviderError()
        return VerifiedSocialIdentity(self.provider, self.client_id, subject,
                                      _display(profile.get("nickname"), self.provider))


class TestSocialProvider:
    """Server-only simulation, not an authorization provider or a universal test code."""

    __test__ = False
    simulated = True

    def __init__(self, provider: SocialProviderName, redirect_uri: str) -> None:
        if provider not in {"wechat", "qq"}:
            raise SocialProviderError()
        self.provider = provider
        self.client_id = "allira-test-" + provider
        self.redirect_uri = _callback(redirect_uri, provider, simulated=True)
        self._codes: dict[str, tuple[str, float]] = {}

    def build_authorize_url(self, state: str, redirect_uri: str) -> str:
        if redirect_uri != self.redirect_uri:
            raise SocialProviderError()
        _opaque(state, 128)
        current = time.time()
        self._codes = {key: value for key, value in self._codes.items() if value[1] > current}
        if len(self._codes) >= MAX_TEST_CODES:
            raise SocialProviderError()
        code = secrets.token_urlsafe(32)
        self._codes[code] = (state, current + 300)
        return redirect_uri + "?" + urlencode({"state": state, "code": code})

    def matches_state(self, code: str, state: str) -> bool:
        entry = self._codes.get(code)
        return entry is not None and entry[1] > time.time() and secrets.compare_digest(entry[0], state)

    async def exchange_code(self, code: str, redirect_uri: str) -> VerifiedSocialIdentity:
        entry = self._codes.pop(code, None)
        if redirect_uri != self.redirect_uri or entry is None or entry[1] <= time.time():
            raise SocialProviderError()
        return VerifiedSocialIdentity(self.provider, self.client_id, "allira-test-subject-" + self.provider,
                                      "模拟微信用户" if self.provider == "wechat" else "模拟QQ用户")


def default_social_providers(settings: Settings, client: httpx.AsyncClient) -> dict[str, SocialProvider]:
    mode = settings.social_mode
    if mode == "disabled":
        return {}
    result: dict[str, SocialProvider] = {}
    if mode == "test":
        if (settings.environment != "development" or settings.provider != "mock"
                or settings.sms_provider != "test-file"):
            raise ValueError("Simulated social authentication requires isolated development settings.")
        for name in ("wechat", "qq"):
            callback = getattr(settings, name + "_redirect_uri")
            if callback:
                result[name] = TestSocialProvider(name, callback)
        return result
    if mode != "tencent":
        raise ValueError("Unsupported social authorization mode.")
    for name in ("wechat", "qq"):
        client_id = getattr(settings, name + "_client_id")
        secret = getattr(settings, name + "_client_secret")
        callback = getattr(settings, name + "_redirect_uri")
        if client_id and secret and callback:
            result[name] = TencentSocialProvider(name, client_id, secret, callback, client)
    return result
