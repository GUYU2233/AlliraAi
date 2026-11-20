"""Cloudflare Access human identity verification using joserfc, never a header trust shortcut.

Only the operator-owned team certificate URL is fetched. This module has no account,
Cookie, management-token or database authority; routing and CSRF belong to the caller.
"""

import asyncio
import hashlib
import json
import math
import re
import time
from dataclasses import dataclass, field

import httpx
from fastapi import Request
from joserfc import jwk, jws, jwt
from joserfc.errors import JoseError
from joserfc.jwt import JWTClaimsRegistry

from .config import Settings
from .db import APIError

_MAX_ASSERTION = 16384
_MAX_RESPONSE = 65536
_MAX_KEYS = 16
_CACHE_SECONDS = 300.0
_UNKNOWN_REFRESH_SECONDS = 30.0
_FAILURE_RETRY_SECONDS = 5.0
_FETCH_SECONDS = 15.0
_TEAM = re.compile(r"https://[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.cloudflareaccess\.com\Z")
_KID = re.compile(r"[A-Za-z0-9_.:-]{1,256}\Z")
_BASE64URL = re.compile(r"[A-Za-z0-9_-]+\Z")
_PRIVATE_RSA = frozenset({"d", "p", "q", "dp", "dq", "qi", "oth"})
_HEADER_FIELDS = frozenset({"alg", "kid", "typ"})
_SERVICE_CLAIMS = frozenset({"service_token_id", "service_token_status", "common_name"})
_JWS_REGISTRY = jws.JWSRegistry(algorithms=["RS256"])


def _unauthorized() -> APIError:
    return APIError(401, "access_unauthorized", "A valid administrator identity is required.")


def _unavailable() -> APIError:
    return APIError(503, "access_unavailable", "Administrator identity verification is unavailable.")


@dataclass(frozen=True)
class AccessIdentity:
    subject: str = field(repr=False)
    email: str = field(repr=False)
    expires_at: float
    assertion_digest: str = field(repr=False)


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON member.")
        result[key] = value
    return result


def _constant(_value):
    raise ValueError("Invalid JSON constant.")


class _StrictJSON(json.JSONDecoder):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, object_pairs_hook=_pairs, parse_constant=_constant, **kwargs)


def _assertion(value: str) -> None:
    if (not isinstance(value, str) or not 1 <= len(value) <= _MAX_ASSERTION
            or not value.isascii() or any(ord(c) <= 32 or ord(c) == 127 for c in value)):
        raise _unauthorized()


def _email(value: object) -> str:
    if (not isinstance(value, str) or not 3 <= len(value) <= 254 or not value.isascii()
            or value.count("@") != 1 or value.startswith("@") or value.endswith("@")
            or any(ord(c) <= 32 or ord(c) == 127 for c in value)):
        raise _unauthorized()
    return value.lower()


def _header(assertion: str) -> str:
    # Parsing and all JWS structural validation are delegated to the JOSE library.
    header = jws.extract_compact(assertion.encode("ascii"), registry=_JWS_REGISTRY).protected
    if (header.get("alg") != "RS256" or set(header) - _HEADER_FIELDS
            or not isinstance(header.get("kid"), str) or not _KID.fullmatch(header["kid"])
            or ("typ" in header and header["typ"] != "JWT")):
        raise _unauthorized()
    return header["kid"]


def _keyset(value: object) -> dict[str, jwk.RSAKey]:
    if not isinstance(value, dict) or not isinstance(value.get("keys"), list):
        raise TypeError("Invalid public key set.")
    entries = value["keys"]
    if not 1 <= len(entries) <= _MAX_KEYS:
        raise ValueError("Invalid public key count.")
    result = {}
    for item in entries:
        if (not isinstance(item, dict) or item.get("kty") != "RSA"
                or _PRIVATE_RSA.intersection(item) or item.get("alg", "RS256") != "RS256"
                or item.get("use", "sig") != "sig"
                or ("key_ops" in item and item["key_ops"] != ["verify"])
                or not isinstance(item.get("kid"), str) or not _KID.fullmatch(item["kid"])
                or item["kid"] in result
                or not isinstance(item.get("n"), str) or not 342 <= len(item["n"]) <= 1366
                or not _BASE64URL.fullmatch(item["n"])
                or not isinstance(item.get("e"), str) or not 1 <= len(item["e"]) <= 12
                or not _BASE64URL.fullmatch(item["e"])):
            raise ValueError("Invalid public RSA key.")
        key = jwk.RSAKey.import_key(item)
        if key.is_private or not 2048 <= key.public_key.key_size <= 8192:
            raise ValueError("Invalid public RSA key size.")
        result[item["kid"]] = key
    return result


def _cache_seconds(headers: httpx.Headers) -> float:
    # Never use a server-provided age to extend our own five-minute limit.
    directives = [part.strip().lower() for part in headers.get("cache-control", "").split(",")]
    if any(part in {"no-store", "no-cache"} for part in directives):
        return 0.0
    age = 0.0
    if headers.get("age", "").isdigit():
        age = min(float(headers["age"]), _CACHE_SECONDS)
    maximum = _CACHE_SECONDS
    for directive in directives:
        if directive.startswith("max-age="):
            value = directive.removeprefix("max-age=").strip('"')
            if not value.isdigit() or len(value) > 10:
                return 0.0
            maximum = min(maximum, float(value))
    return max(0.0, maximum - age)


class AccessVerifier:
    def __init__(self, settings: Settings, *, transport: httpx.AsyncBaseTransport | None = None):
        issuer = settings.access_team_domain
        audience = settings.access_audience
        try:
            allowed = frozenset(_email(email) for email in settings.access_allowed_emails)
        except (APIError, TypeError):
            raise _unavailable() from None
        if (not isinstance(issuer, str) or not _TEAM.fullmatch(issuer)
                or not isinstance(audience, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", audience)
                or not allowed or len(allowed) > 100):
            raise _unavailable()
        self._issuer, self._audience, self._allowed = issuer, audience, allowed
        self._url = issuer + "/cdn-cgi/access/certs"
        self._client = httpx.AsyncClient(
            transport=transport, trust_env=False, follow_redirects=False,
            timeout=httpx.Timeout(_FETCH_SECONDS), headers={"Accept-Encoding": "identity"},
        )
        self._keys: dict[str, jwk.RSAKey] = {}
        self._expires = 0.0
        self._unknown_after = 0.0
        self._retry_after = 0.0
        self._generation = 0
        self._lock = asyncio.Lock()
        self._closed = False

    async def aclose(self) -> None:
        self._closed = True
        self._keys.clear()
        self._expires = 0.0
        await self._client.aclose()

    async def _fetch(self) -> None:
        try:
            async with asyncio.timeout(_FETCH_SECONDS):
                async with self._client.stream("GET", self._url) as response:
                    if response.status_code != 200 or str(response.url) != self._url:
                        raise ValueError("Invalid key response.")
                    if response.headers.get("content-encoding", "identity").lower() != "identity":
                        raise ValueError("Encoded key response.")
                    length = response.headers.get("content-length")
                    if length is not None and (not length.isdigit() or len(length) > 10
                                               or int(length) > _MAX_RESPONSE):
                        raise ValueError("Invalid key response size.")
                    content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
                    if content_type not in {"application/json", "application/jwk-set+json"}:
                        raise ValueError("Invalid key response type.")
                    raw = bytearray()
                    if response.is_stream_consumed:
                        raw.extend(response.content)
                    else:
                        async for part in response.aiter_raw():
                            raw.extend(part)
                            if len(raw) > _MAX_RESPONSE:
                                raise ValueError("Key response size limit exceeded.")
                    if len(raw) > _MAX_RESPONSE:
                        raise ValueError("Key response size limit exceeded.")
                    keys = _keyset(json.loads(bytes(raw).decode("utf-8"), cls=_StrictJSON))
                    ttl = _cache_seconds(response.headers)
            if self._closed:
                raise _unavailable()
            self._keys = keys
            self._expires = time.monotonic() + ttl
            self._unknown_after = time.monotonic() + _UNKNOWN_REFRESH_SECONDS
            self._retry_after = 0.0
            self._generation += 1
        except asyncio.CancelledError:
            self._keys.clear()
            self._expires = 0.0
            self._retry_after = time.monotonic() + _FAILURE_RETRY_SECONDS
            raise
        except (httpx.HTTPError, JoseError, ValueError, TypeError, OverflowError,
                RecursionError, RuntimeError, TimeoutError, APIError):
            self._keys.clear()
            self._expires = 0.0
            self._retry_after = time.monotonic() + _FAILURE_RETRY_SECONDS
            raise _unavailable() from None

    async def _key(self, kid: str) -> jwk.RSAKey:
        stamp = time.monotonic()
        if self._closed:
            raise _unavailable()
        if self._keys and stamp < self._expires and kid in self._keys:
            return self._keys[kid]
        generation = self._generation
        async with self._lock:
            stamp = time.monotonic()
            if self._closed or stamp < self._retry_after:
                raise _unavailable()
            if self._keys:
                if stamp < self._expires and kid in self._keys:
                    return self._keys[kid]
                if kid not in self._keys and stamp < self._unknown_after:
                    raise _unauthorized()
            # A caller already waiting for the same refresh never triggers a
            # second fetch, including a freshly fetched set with a zero cache TTL.
            if generation == self._generation:
                await self._fetch()
            key = self._keys.get(kid)
            if key is None:
                raise _unauthorized()
            return key

    async def verify(self, assertion: str) -> AccessIdentity:
        _assertion(assertion)
        try:
            kid = _header(assertion)
            key = await self._key(kid)
            token = jwt.decode(assertion, key, algorithms=["RS256"],
                               registry=_JWS_REGISTRY, decoder_cls=_StrictJSON)
            claims = token.claims
            audience = claims.get("aud")
            if (claims.get("iss") != self._issuer
                    or not (isinstance(audience, str) or (isinstance(audience, list)
                            and audience and all(isinstance(value, str) for value in audience)))):
                raise _unauthorized()
            for name in ("exp", "iat", "nbf"):
                if name not in claims and name == "nbf":
                    continue
                value = claims.get(name)
                if type(value) not in {int, float} or not math.isfinite(value):
                    raise _unauthorized()
            registry = JWTClaimsRegistry(
                now=time.time, leeway=0,
                iss={"essential": True, "value": self._issuer},
                aud={"essential": True, "value": self._audience},
                exp={"essential": True}, iat={"essential": True},
                sub={"essential": True}, email={"essential": True},
                type={"essential": True, "value": "app"},
            )
            registry.validate(claims)
            subject = claims["sub"]
            if (not isinstance(subject, str) or not 1 <= len(subject) <= 256
                    or not subject.isascii() or any(ord(c) <= 32 or ord(c) == 127 for c in subject)
                    or claims["type"] != "app" or _SERVICE_CLAIMS.intersection(claims)
                    or claims["exp"] <= claims["iat"]
                    or ("nbf" in claims and claims["exp"] <= claims["nbf"])):
                raise _unauthorized()
            email = _email(claims["email"])
            if email not in self._allowed:
                raise _unauthorized()
            return AccessIdentity(subject=subject, email=email, expires_at=float(claims["exp"]),
                                  assertion_digest=hashlib.sha256(assertion.encode("ascii")).hexdigest())
        except APIError:
            raise
        except (JoseError, ValueError, TypeError, KeyError, AttributeError, OverflowError, RecursionError):
            raise _unauthorized() from None


async def verified_access(request: Request) -> AccessIdentity:
    assertions = [value for name, value in request.scope.get("headers", [])
                  if name.lower() == b"cf-access-jwt-assertion"]
    if len(assertions) != 1:
        raise _unauthorized()
    try:
        assertion = assertions[0].decode("ascii")
    except (UnicodeDecodeError, AttributeError):
        raise _unauthorized() from None
    _assertion(assertion)
    verifier = getattr(request.app.state, "access_verifier", None)
    if verifier is None:
        raise _unavailable()
    return await verifier.verify(assertion)
