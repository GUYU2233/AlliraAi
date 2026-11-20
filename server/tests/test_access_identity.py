"""Synthetic Access assertions and JWKS only, never Cloudflare or live administrator access."""

import asyncio
import base64
import hashlib
import json
import time
from dataclasses import FrozenInstanceError
from types import SimpleNamespace

import httpx
import pytest
from fastapi import Request
from joserfc import jwk, jwt

from allira import access_identity
from allira.access_identity import AccessIdentity, AccessVerifier, verified_access
from allira.db import APIError

ISSUER = "https://synthetic-team.cloudflareaccess.com"
AUDIENCE = "a" * 64
EMAIL = "operator@example.invalid"
SUBJECT = "synthetic-human-subject"
CERTS = ISSUER + "/cdn-cgi/access/certs"


def settings(**changes):
    return SimpleNamespace(**{
        "access_team_domain": ISSUER, "access_audience": AUDIENCE,
        "access_allowed_emails": [EMAIL], **changes,
    })


@pytest.fixture(scope="session")
def rsa_key():
    return jwk.RSAKey.generate_key(2048, parameters={"kid": "access-key", "alg": "RS256", "use": "sig"})


@pytest.fixture(scope="session")
def other_key():
    return jwk.RSAKey.generate_key(2048, parameters={"kid": "rotated-key", "alg": "RS256", "use": "sig"})


def claims(**changes):
    stamp = time.time()
    return {
        "iss": ISSUER, "aud": [AUDIENCE], "sub": SUBJECT, "email": EMAIL,
        "exp": stamp + 600, "iat": stamp - 10, "nbf": stamp - 10, "type": "app", **changes,
    }


def signed(key, *, body=None, header=None):
    return jwt.encode({"alg": "RS256", "kid": key.kid, **(header or {})},
                      body if body is not None else claims(), key)


class CertIssuer:
    def __init__(self, key):
        self.body = {"keys": [key.as_dict(private=False)]}
        self.headers = {"content-type": "application/json", "cache-control": "max-age=300"}
        self.status = 200
        self.calls = []
        self.failure = None
        self.raw = None

    def respond(self, request):
        self.calls.append(request)
        assert str(request.url) == CERTS and request.method == "GET"
        assert request.headers["accept-encoding"] == "identity"
        assert "authorization" not in request.headers and "cookie" not in request.headers
        if self.failure is not None:
            raise self.failure
        if self.raw is not None:
            return httpx.Response(self.status, headers=self.headers, content=self.raw)
        return httpx.Response(self.status, headers=self.headers, json=self.body)

    def verifier(self, **changes):
        return AccessVerifier(settings(**changes), transport=httpx.MockTransport(self.respond))


async def refused(verifier, assertion, status=401):
    with pytest.raises(APIError) as failure:
        await verifier.verify(assertion)
    error = failure.value
    assert error.status == status
    assert error.code == ("access_unauthorized" if status == 401 else "access_unavailable")
    assert "synthetic" not in error.message and assertion not in error.message
    return error


def test_real_rs256_identity_frozen_digest_allowlist_and_no_repr_leak(rsa_key, monkeypatch, caplog):
    async def scenario():
        issuer = CertIssuer(rsa_key)
        verifier = issuer.verifier(access_allowed_emails=[EMAIL.upper()])
        body = claims(email=EMAIL.upper())
        assertion = signed(rsa_key, body=body)
        decoded, validated = [], []
        original_decode = access_identity.jwt.decode
        original_validate = access_identity.JWTClaimsRegistry.validate

        def decode(*args, **kwargs):
            decoded.append(kwargs["algorithms"])
            return original_decode(*args, **kwargs)

        def validate(self, value):
            validated.append(set(value))
            return original_validate(self, value)

        monkeypatch.setattr(access_identity.jwt, "decode", decode)
        monkeypatch.setattr(access_identity.JWTClaimsRegistry, "validate", validate)
        identity = await verifier.verify(assertion)
        assert identity == AccessIdentity(SUBJECT, EMAIL, body["exp"],
                                          hashlib.sha256(assertion.encode()).hexdigest())
        assert decoded == [["RS256"]] and validated
        assert len(issuer.calls) == 1
        assert SUBJECT not in repr(identity) and EMAIL not in repr(identity)
        assert identity.assertion_digest not in repr(identity) and assertion not in caplog.text
        with pytest.raises(FrozenInstanceError):
            identity.email = "different@example.invalid"
        await verifier.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("name", ["iss", "aud", "sub", "email", "exp", "iat", "type"])
def test_required_claim_missing(rsa_key, name):
    async def scenario():
        issuer = CertIssuer(rsa_key)
        verifier = issuer.verifier()
        body = claims()
        del body[name]
        try:
            await refused(verifier, signed(rsa_key, body=body))
        finally:
            await verifier.aclose()
    asyncio.run(scenario())


@pytest.mark.parametrize("change", [
    {"iss": ISSUER + "/"}, {"iss": "https://other.cloudflareaccess.com"}, {"iss": [ISSUER]},
    {"aud": "other"}, {"aud": ["other"]}, {"aud": [AUDIENCE, 1]}, {"aud": []},
    {"type": "service"}, {"type": "APP"}, {"type": ["app"]},
    {"sub": ""}, {"sub": " "}, {"sub": "bad\nsubject"}, {"sub": "名字"},
    {"sub": "s" * 257}, {"sub": 2},
    {"email": "other@example.invalid"}, {"email": EMAIL + " "}, {"email": " " + EMAIL},
    {"email": "operator+tag@example.invalid"}, {"email": "opérator@example.invalid"},
    {"email": [EMAIL]}, {"email": "operator"}, {"email": "@example.invalid"},
    {"email": "operator@"}, {"email": "operator\n@example.invalid"},
    {"service_token_id": "synthetic-service-token"}, {"service_token_status": None},
    {"common_name": "synthetic-client-id"},
    {"exp": True}, {"iat": False}, {"nbf": True},
    {"exp": "9999999999"}, {"iat": "1"}, {"nbf": "1"},
    {"exp": float("nan")}, {"iat": float("inf")}, {"nbf": float("-inf")},
    {"exp": None}, {"iat": []}, {"nbf": None},
    {"exp": 1}, {"iat": 9999999999}, {"nbf": 9999999999},
])
def test_strict_claims_and_service_tokens_fail_closed(rsa_key, change):
    async def scenario():
        verifier = CertIssuer(rsa_key).verifier()
        try:
            await refused(verifier, signed(rsa_key, body=claims(**change)))
        finally:
            await verifier.aclose()
    asyncio.run(scenario())


def test_nbf_is_optional_audience_may_be_string_or_matching_list_and_email_is_ascii_lower(rsa_key):
    async def scenario():
        issuer = CertIssuer(rsa_key)
        verifier = issuer.verifier()
        try:
            for audience in (AUDIENCE, ["different-resource", AUDIENCE]):
                body = claims(aud=audience, email=EMAIL.upper())
                body.pop("nbf")
                result = await verifier.verify(signed(rsa_key, body=body))
                assert result.email == EMAIL and result.subject == SUBJECT
            assert len(issuer.calls) == 1
        finally:
            await verifier.aclose()
    asyncio.run(scenario())


def test_numeric_expiration_boundaries_and_order(rsa_key, monkeypatch):
    async def scenario():
        monkeypatch.setattr(access_identity.time, "time", lambda: 1000.0)
        verifier = CertIssuer(rsa_key).verifier()
        try:
            for change in ({"exp": 1000}, {"iat": 1000.001}, {"nbf": 1000.001},
                           {"exp": 1001, "iat": 1001}, {"exp": 1001, "nbf": 1001}):
                body = claims(exp=2000, iat=900, nbf=900)
                body.update(change)
                await refused(verifier, signed(rsa_key, body=body))
            result = await verifier.verify(signed(rsa_key, body=claims(exp=1000.01, iat=1000, nbf=1000)))
            assert result.expires_at == 1000.01
        finally:
            await verifier.aclose()
    asyncio.run(scenario())


@pytest.mark.parametrize("value", ["", "a" * 16385, "非ASCII", "a b.c.d", "a\nb.c.d", "a.b.c\x7f", None, b"a.b.c"])
def test_bad_assertion_never_fetches(rsa_key, value):
    async def scenario():
        issuer = CertIssuer(rsa_key)
        verifier = issuer.verifier()
        try:
            with pytest.raises(APIError) as failure:
                await verifier.verify(value)
            assert failure.value.status == 401 and issuer.calls == []
        finally:
            await verifier.aclose()
    asyncio.run(scenario())


@pytest.mark.parametrize("header", [
    {"kid": ""}, {"kid": "unknown-key"}, {"kid": []}, {"kid": "a" * 257},
    {"kid": "url/unsafe"}, {"typ": "at+jwt"}, {"typ": None},
    {"jku": "https://attacker.invalid/jwks"}, {"x5u": "https://attacker.invalid/certificate"},
    {"crit": []},
])
def test_untrusted_headers_never_select_external_keys(rsa_key, header):
    async def scenario():
        issuer = CertIssuer(rsa_key)
        verifier = issuer.verifier()
        try:
            if isinstance(header.get("kid", ""), list) or header == {"typ": None}:
                # The mature signer correctly refuses malformed header types;
                # alter an existing signed envelope only to exercise rejection.
                _, payload, signature = signed(rsa_key).split(".")
                raw_header = json.dumps({"alg": "RS256", "kid": rsa_key.kid, **header}).encode()
                encoded = base64.urlsafe_b64encode(raw_header).rstrip(b"=").decode()
                assertion = f"{encoded}.{payload}.{signature}"
            else:
                assertion = signed(rsa_key, header=header)
            await refused(verifier, assertion)
            assert len(issuer.calls) <= 1
        finally:
            await verifier.aclose()
    asyncio.run(scenario())


@pytest.mark.parametrize("raw_header", [
    b"null", b"[]", b'"alg"', b"1", b"false", b"{", b'{"alg":null}',
])
def test_malformed_unverified_header_is_a_safe_401_without_key_fetch(rsa_key, raw_header):
    async def scenario():
        issuer = CertIssuer(rsa_key)
        verifier = issuer.verifier()
        _, payload, signature = signed(rsa_key).split(".")
        header = base64.urlsafe_b64encode(raw_header).rstrip(b"=").decode()
        assertion = f"{header}.{payload}.{signature}"
        try:
            await refused(verifier, assertion)
            assert issuer.calls == []
        finally:
            await verifier.aclose()
    asyncio.run(scenario())


def test_no_kid_wrong_signature_and_algorithm_confusion(rsa_key, other_key):
    async def scenario():
        issuer = CertIssuer(rsa_key)
        verifier = issuer.verifier()
        try:
            no_kid = jwt.encode({"alg": "RS256"}, claims(), rsa_key)
            await refused(verifier, no_kid)
            wrong_key = signed(other_key, header={"kid": rsa_key.kid})
            await refused(verifier, wrong_key)
            oct_key = jwk.OctKey.generate_key(256)
            hs = jwt.encode({"alg": "HS256", "kid": rsa_key.kid}, claims(), oct_key)
            await refused(verifier, hs)
            ec = jwk.ECKey.generate_key("P-256")
            es = jwt.encode({"alg": "ES256", "kid": rsa_key.kid}, claims(), ec)
            await refused(verifier, es)
            assert len(issuer.calls) == 1
        finally:
            await verifier.aclose()
    asyncio.run(scenario())


@pytest.mark.parametrize("change", [
    {"kty": "oct"}, {"kty": "EC"}, {"alg": "HS256"}, {"alg": "RS512"},
    {"use": "enc"}, {"key_ops": ["sign"]}, {"key_ops": ["verify", "encrypt"]},
    {"key_ops": "verify"}, {"kid": ""}, {"kid": []}, {"kid": "unsafe/id"},
    {"n": ""}, {"n": "!" * 342}, {"n": "A" * 1367}, {"n": "A" * 341},
    {"e": ""}, {"e": "!"}, {"e": "AQAB" * 4},
    {"d": "AQAB"}, {"p": "AQAB"}, {"q": "AQAB"}, {"dp": "AQAB"},
    {"dq": "AQAB"}, {"qi": "AQAB"}, {"oth": []},
])
def test_invalid_public_rsa_key_rejects_entire_response(rsa_key, change):
    async def scenario():
        issuer = CertIssuer(rsa_key)
        issuer.body["keys"][0].update(change)
        verifier = issuer.verifier()
        try:
            await refused(verifier, signed(rsa_key), 503)
            assert not verifier._keys
        finally:
            await verifier.aclose()
    asyncio.run(scenario())


@pytest.mark.parametrize("body", [None, [], {}, {"keys": None}, {"keys": {}}, {"keys": []},
                                   {"keys": [None]}, {"keys": ["RSA"]}])
def test_invalid_jwks_shape(rsa_key, body):
    async def scenario():
        issuer = CertIssuer(rsa_key)
        issuer.body = body
        verifier = issuer.verifier()
        try:
            await refused(verifier, signed(rsa_key), 503)
        finally:
            await verifier.aclose()
    asyncio.run(scenario())


def test_duplicate_kids_count_and_private_key_material_rejected(rsa_key):
    async def scenario():
        issuer = CertIssuer(rsa_key)
        verifier = issuer.verifier()
        try:
            issuer.body["keys"] *= 2
            await refused(verifier, signed(rsa_key), 503)
            issuer.body = {"keys": [{**rsa_key.as_dict(private=False), "kid": str(n)} for n in range(17)]}
            verifier._retry_after = 0
            await refused(verifier, signed(rsa_key), 503)
            issuer.body = {"keys": [rsa_key.as_dict(private=True)]}
            verifier._retry_after = 0
            await refused(verifier, signed(rsa_key), 503)
        finally:
            await verifier.aclose()
    asyncio.run(scenario())


def test_rsa_key_below_2048_bits_is_refused(rsa_key):
    async def scenario():
        with pytest.warns(UserWarning):
            weak = jwk.RSAKey.generate_key(1024, parameters={"kid": rsa_key.kid})
        issuer = CertIssuer(rsa_key)
        issuer.body = {"keys": [weak.as_dict(private=False)]}
        verifier = issuer.verifier()
        try:
            await refused(verifier, signed(rsa_key), 503)
        finally:
            await verifier.aclose()
    asyncio.run(scenario())


@pytest.mark.parametrize("raw", [b"{", b"[]", b"NaN", b'{"keys":[],"keys":[]}',
                                  b"\xff", b'{"unused":' + b"[" * 1100 + b"0" + b"]" * 1100 + b"}",
                                  b" " * 65537])
def test_jwks_json_and_size_are_bounded(rsa_key, raw):
    async def scenario():
        issuer = CertIssuer(rsa_key)
        issuer.raw = raw
        verifier = issuer.verifier()
        try:
            await refused(verifier, signed(rsa_key), 503)
        finally:
            await verifier.aclose()
    asyncio.run(scenario())


@pytest.mark.parametrize("status,headers", [
    (302, {"location": "https://attacker.invalid/keys"}), (401, {}), (429, {}), (500, {}),
    (200, {"content-type": "text/html"}), (200, {"content-encoding": "gzip"}),
    (200, {"content-encoding": "br"}), (200, {"content-length": "65537"}),
    (200, {"content-length": "-1"}), (200, {"content-length": "NaN"}),
])
def test_http_failures_no_redirect_no_compression(rsa_key, status, headers):
    async def scenario():
        issuer = CertIssuer(rsa_key)
        issuer.status = status
        issuer.headers.update(headers)
        if headers.get("content-encoding") in {"gzip", "br"}:
            issuer.raw = b"not-compressed-and-never-decoded"
        verifier = issuer.verifier()
        try:
            await refused(verifier, signed(rsa_key), 503)
            assert len(issuer.calls) == 1
        finally:
            await verifier.aclose()
    asyncio.run(scenario())


class ByteStream(httpx.AsyncByteStream):
    def __init__(self, value):
        self.value, self.closed = value, False

    async def __aiter__(self):
        for index in range(0, len(self.value), 13):
            yield self.value[index:index + 13]

    async def aclose(self):
        self.closed = True


def test_unbuffered_json_and_jwk_set_content_type_close_response(rsa_key):
    async def scenario():
        stream = ByteStream(json.dumps({"keys": [rsa_key.as_dict(private=False)]}).encode())
        calls = []

        def handler(request):
            calls.append(request)
            return httpx.Response(200, headers={"content-type": "application/jwk-set+json"}, stream=stream)

        verifier = AccessVerifier(settings(), transport=httpx.MockTransport(handler))
        try:
            assert (await verifier.verify(signed(rsa_key))).subject == SUBJECT
            assert stream.closed and len(calls) == 1
        finally:
            await verifier.aclose()
    asyncio.run(scenario())


def test_unbuffered_oversize_closes_and_never_accepts(rsa_key):
    async def scenario():
        stream = ByteStream(b" " * 65537)
        verifier = AccessVerifier(settings(), transport=httpx.MockTransport(
            lambda request: httpx.Response(200, headers={"content-type": "application/json"}, stream=stream)))
        try:
            await refused(verifier, signed(rsa_key), 503)
            assert stream.closed
        finally:
            await verifier.aclose()
    asyncio.run(scenario())


def test_fixed_transport_disables_proxy_redirects_and_sends_no_assertion(rsa_key, monkeypatch):
    async def scenario():
        monkeypatch.setenv("HTTPS_PROXY", "http://proxy.invalid:1234")
        original = access_identity.httpx.AsyncClient
        options = []

        def client(*args, **kwargs):
            options.append(kwargs)
            return original(*args, **kwargs)

        monkeypatch.setattr(access_identity.httpx, "AsyncClient", client)
        issuer = CertIssuer(rsa_key)
        verifier = issuer.verifier()
        try:
            assertion = signed(rsa_key)
            await verifier.verify(assertion)
            assert options[0]["trust_env"] is False and options[0]["follow_redirects"] is False
            assert str(issuer.calls[0].url) == CERTS
            assert assertion not in str(issuer.calls[0].headers) and assertion not in str(issuer.calls[0].url)
        finally:
            await verifier.aclose()
    asyncio.run(scenario())


@pytest.mark.parametrize("change", [
    {"access_team_domain": "http://synthetic-team.cloudflareaccess.com"},
    {"access_team_domain": ISSUER + "/"}, {"access_team_domain": ISSUER + ":443"},
    {"access_team_domain": ISSUER + "/path"}, {"access_team_domain": ISSUER + "?x=1"},
    {"access_team_domain": "https://synthetic-team.cloudflareaccess.com.attacker.invalid"},
    {"access_team_domain": "https://attacker@synthetic-team.cloudflareaccess.com"},
    {"access_team_domain": "https://cloudflareaccess.com"}, {"access_team_domain": "https://127.0.0.1"},
    {"access_audience": "not-an-audience"}, {"access_audience": "g" * 64},
    {"access_allowed_emails": []}, {"access_allowed_emails": [" " + EMAIL]},
    {"access_allowed_emails": ["operátor@example.invalid"]}, {"access_allowed_emails": None},
])
def test_invalid_operator_configuration_fails_closed(change):
    with pytest.raises(APIError) as failure:
        AccessVerifier(settings(**change), transport=httpx.MockTransport(lambda _: None))
    assert failure.value.status == 503 and failure.value.code == "access_unavailable"


def test_cache_validates_every_assertion_without_repeated_fetch(rsa_key, monkeypatch):
    async def scenario():
        clock = [1000.0]
        monkeypatch.setattr(access_identity.time, "monotonic", lambda: clock[0])
        issuer = CertIssuer(rsa_key)
        issuer.headers["cache-control"] = "max-age=9999999"
        verifier = issuer.verifier()
        try:
            good = signed(rsa_key)
            await verifier.verify(good)
            for _ in range(5):
                clock[0] += 59
                await verifier.verify(good)
            assert len(issuer.calls) == 1
            clock[0] = 1300
            await verifier.verify(good)
            assert len(issuer.calls) == 2
            await refused(verifier, signed(rsa_key, body=claims(email="outsider@example.invalid")))
            assert len(issuer.calls) == 2
        finally:
            await verifier.aclose()
    asyncio.run(scenario())


@pytest.mark.parametrize("headers,ttl", [
    ({"cache-control": "max-age=20"}, 20),
    ({"cache-control": "max-age=20", "age": "5"}, 15),
    ({"cache-control": "no-cache"}, 0), ({"cache-control": "no-store"}, 0),
    ({"cache-control": "max-age=invalid"}, 0), ({"cache-control": "max-age=999999999999"}, 0),
])
def test_cache_never_outlives_http_or_local_maximum(rsa_key, monkeypatch, headers, ttl):
    async def scenario():
        clock = [1000.0]
        monkeypatch.setattr(access_identity.time, "monotonic", lambda: clock[0])
        issuer = CertIssuer(rsa_key)
        issuer.headers.update(headers)
        verifier = issuer.verifier()
        try:
            await verifier.verify(signed(rsa_key))
            assert verifier._expires == 1000.0 + ttl
            clock[0] += ttl
            await verifier.verify(signed(rsa_key))
            assert len(issuer.calls) == 2
        finally:
            await verifier.aclose()
    asyncio.run(scenario())


def test_unknown_kid_refresh_is_global_rate_limited_and_rotation_supported(rsa_key, other_key, monkeypatch):
    async def scenario():
        clock = [1000.0]
        monkeypatch.setattr(access_identity.time, "monotonic", lambda: clock[0])
        issuer = CertIssuer(rsa_key)
        verifier = issuer.verifier()
        try:
            await verifier.verify(signed(rsa_key))
            rotated = signed(other_key)
            for index in range(20):
                await refused(verifier, signed(other_key, header={"kid": "unknown-" + str(index)}))
            assert len(issuer.calls) == 1
            clock[0] += 30
            issuer.body["keys"].append(other_key.as_dict(private=False))
            assert (await verifier.verify(rotated)).subject == SUBJECT
            assert len(issuer.calls) == 2
            await verifier.verify(signed(rsa_key))
            await refused(verifier, signed(other_key, header={"kid": "still-unknown"}))
            assert len(issuer.calls) == 2
        finally:
            await verifier.aclose()
    asyncio.run(scenario())


def test_initial_unknown_kid_does_not_create_unbounded_negative_cache(rsa_key, other_key, monkeypatch):
    async def scenario():
        clock = [1000.0]
        monkeypatch.setattr(access_identity.time, "monotonic", lambda: clock[0])
        issuer = CertIssuer(rsa_key)
        verifier = issuer.verifier()
        try:
            for index in range(50):
                await refused(verifier, signed(other_key, header={"kid": "random-" + str(index)}))
            assert len(issuer.calls) == 1 and len(verifier._keys) == 1
            clock[0] += 30
            await refused(verifier, signed(other_key))
            assert len(issuer.calls) == 2 and len(verifier._keys) == 1
        finally:
            await verifier.aclose()
    asyncio.run(scenario())


def test_expired_cache_fetch_failure_never_uses_stale_and_retries_are_bounded(rsa_key, monkeypatch):
    async def scenario():
        clock = [1000.0]
        monkeypatch.setattr(access_identity.time, "monotonic", lambda: clock[0])
        issuer = CertIssuer(rsa_key)
        verifier = issuer.verifier()
        try:
            assertion = signed(rsa_key)
            await verifier.verify(assertion)
            clock[0] += 300
            issuer.failure = httpx.ConnectError("synthetic-private-upstream-error")
            await refused(verifier, assertion, 503)
            assert not verifier._keys
            for _ in range(5):
                await refused(verifier, assertion, 503)
            assert len(issuer.calls) == 2
            clock[0] += 5
            issuer.failure = None
            assert (await verifier.verify(assertion)).subject == SUBJECT
            assert len(issuer.calls) == 3
        finally:
            await verifier.aclose()
    asyncio.run(scenario())


def test_refresh_concurrent_requests_share_one_network_fetch(rsa_key):
    async def scenario():
        started, release = asyncio.Event(), asyncio.Event()
        issuer = CertIssuer(rsa_key)

        async def handler(request):
            started.set()
            await release.wait()
            return issuer.respond(request)

        verifier = AccessVerifier(settings(), transport=httpx.MockTransport(handler))
        try:
            tasks = [asyncio.create_task(verifier.verify(signed(rsa_key))) for _ in range(25)]
            await started.wait()
            release.set()
            results = await asyncio.gather(*tasks)
            assert len(results) == 25 and len(issuer.calls) == 1
            assert all(item.subject == SUBJECT for item in results)
        finally:
            await verifier.aclose()
    asyncio.run(scenario())


def test_concurrent_failed_fetch_has_one_attempt_and_all_safe_failures(rsa_key):
    async def scenario():
        started, release = asyncio.Event(), asyncio.Event()
        calls = []

        async def handler(request):
            calls.append(request)
            started.set()
            await release.wait()
            raise httpx.ReadTimeout("synthetic-private-network-details")

        verifier = AccessVerifier(settings(), transport=httpx.MockTransport(handler))
        try:
            tasks = [asyncio.create_task(refused(verifier, signed(rsa_key), 503)) for _ in range(15)]
            await started.wait()
            release.set()
            errors = await asyncio.gather(*tasks)
            assert len(calls) == 1 and len(errors) == 15 and not verifier._keys
        finally:
            await verifier.aclose()
    asyncio.run(scenario())


def test_total_timeout_and_cancel_release_refresh_lock(rsa_key, monkeypatch):
    async def scenario():
        monkeypatch.setattr(access_identity, "_FETCH_SECONDS", 0.01)
        started = asyncio.Event()

        async def handler(request):
            started.set()
            await asyncio.Event().wait()

        verifier = AccessVerifier(settings(), transport=httpx.MockTransport(handler))
        try:
            await refused(verifier, signed(rsa_key), 503)
            assert not verifier._lock.locked() and not verifier._keys
            verifier._retry_after = 0
            started.clear()
            task = asyncio.create_task(verifier.verify(signed(rsa_key)))
            await started.wait()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert not verifier._lock.locked() and not verifier._keys
        finally:
            await verifier.aclose()
    asyncio.run(scenario())


def test_aclose_drops_cached_keys_and_rejects_further_verification(rsa_key):
    async def scenario():
        issuer = CertIssuer(rsa_key)
        verifier = issuer.verifier()
        assertion = signed(rsa_key)
        await verifier.verify(assertion)
        await verifier.aclose()
        assert not verifier._keys and verifier._client.is_closed
        await refused(verifier, assertion, 503)
        assert len(issuer.calls) == 1
    asyncio.run(scenario())


def request_for(headers, verifier=None):
    app = SimpleNamespace(state=SimpleNamespace(access_verifier=verifier))
    return Request({"type": "http", "method": "GET", "scheme": "https", "path": "/admin/",
                    "headers": headers, "app": app, "query_string": b"", "server": ("admin.invalid", 443)})


@pytest.mark.parametrize("headers", [
    [], [(b"cookie", b"CF_Authorization=not-an-assertion")],
    [(b"cf-access-authenticated-user-email", EMAIL.encode())],
    [(b"authorization", b"Bearer ordinary-session")],
    [(b"cf-access-jwt-assertion", b"a"), (b"cf-access-jwt-assertion", b"b")],
    [(b"CF-ACCESS-JWT-ASSERTION", b"a"), (b"cf-access-jwt-assertion", b"b")],
    [(b"cf-access-jwt-assertion", b"a" * 16385)],
    [(b"cf-access-jwt-assertion", b"non-ascii-\xff")],
    [(b"cf-access-jwt-assertion", b"")],
])
def test_verified_access_rejects_absent_duplicate_and_malformed_without_fallback(headers):
    class NeverVerifier:
        async def verify(self, assertion):
            raise AssertionError("Invalid headers must never call the verifier")

    async def scenario():
        with pytest.raises(APIError) as failure:
            await verified_access(request_for(headers, NeverVerifier()))
        assert failure.value.status == 401
    asyncio.run(scenario())


def test_verified_access_uses_only_assertion_and_requires_lifecycle_verifier(rsa_key):
    async def scenario():
        issuer = CertIssuer(rsa_key)
        verifier = issuer.verifier()
        assertion = signed(rsa_key)
        headers = [(b"cf-access-jwt-assertion", assertion.encode()),
                   (b"cf-access-authenticated-user-email", b"attacker@example.invalid"),
                   (b"cookie", b"allira_admin=old-cookie; CF_Authorization=untrusted"),
                   (b"authorization", b"Bearer other-session")]
        try:
            result = await verified_access(request_for(headers, verifier))
            assert result.email == EMAIL
            with pytest.raises(APIError) as failure:
                await verified_access(request_for(headers))
            assert failure.value.status == 503
        finally:
            await verifier.aclose()
    asyncio.run(scenario())


def test_errors_and_logs_never_echo_assertion_claims_jwks_or_transport_details(rsa_key, caplog):
    async def scenario():
        issuer = CertIssuer(rsa_key)
        verifier = issuer.verifier()
        assertion = signed(rsa_key, body=claims(email="private-disallowed@example.invalid"))
        try:
            error = await refused(verifier, assertion)
            assert error.__cause__ is None
            issuer.failure = httpx.ConnectError("private-path-and-credentials-sentinel")
            verifier._expires = 0
            unavailable = await refused(verifier, signed(rsa_key), 503)
            assert unavailable.__cause__ is None
            combined = caplog.text + error.message + unavailable.message
            for secret in (assertion, EMAIL, SUBJECT, ISSUER, rsa_key.as_dict()["n"],
                           "private-disallowed@example.invalid", "private-path-and-credentials-sentinel"):
                assert secret not in combined
        finally:
            await verifier.aclose()
    asyncio.run(scenario())
