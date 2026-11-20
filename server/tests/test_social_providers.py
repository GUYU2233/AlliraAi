"""Fixed Tencent endpoints and response handling, using only explicit MockTransport."""

import asyncio
import json
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from allira.social_providers import (
    MAX_RESPONSE_BYTES,
    SocialProviderError,
    TencentSocialProvider,
    TestSocialProvider,
    VerifiedSocialIdentity,
    default_social_providers,
)

CALLBACK = "https://allira.example.com/api/v1/auth/social/{}/callback"
TEST_CALLBACK = "http://127.0.0.1:8000/api/v1/auth/social/{}/callback"
APP_ID = "mock-app-123"
SECRET = "mock-provider-secret-never-logged"
SUBJECT = "mock-provider-openid"
TOKEN = "mock-provider-access-token"


def run(coroutine):
    return asyncio.run(coroutine)


@pytest.mark.parametrize("name", ["wechat", "qq"])
def test_authorize_url_uses_operator_callback_and_exact_website_scope(name):
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(500)))
    provider = TencentSocialProvider(name, APP_ID, SECRET, CALLBACK.format(name), client)
    parsed = urlsplit(provider.build_authorize_url("state-123", provider.redirect_uri))
    params = parse_qs(parsed.query)
    assert params["redirect_uri"] == [CALLBACK.format(name)]
    assert params["response_type"] == ["code"] and params["state"] == ["state-123"]
    assert SECRET not in parsed.query
    if name == "wechat":
        assert (parsed.netloc, parsed.path, parsed.fragment) == (
            "open.weixin.qq.com", "/connect/qrconnect", "wechat_redirect",
        )
        assert params["appid"] == [APP_ID] and params["scope"] == ["snsapi_login"]
    else:
        assert (parsed.netloc, parsed.path, parsed.fragment) == ("graph.qq.com", "/oauth2.0/authorize", "")
        assert params["client_id"] == [APP_ID] and params["scope"] == ["get_user_info"]
    with pytest.raises(SocialProviderError):
        provider.build_authorize_url("state-123", "https://attacker.example/callback")
    run(client.aclose())


def test_wechat_exchange_checks_profile_openid_without_claiming_scope_or_unionid(caplog):
    seen = []

    def handler(request):
        seen.append(request)
        assert request.headers["accept-encoding"] == "identity"
        if request.url.path == "/sns/oauth2/access_token":
            assert request.url.host == "api.weixin.qq.com"
            assert request.url.params == httpx.QueryParams({
                "appid": APP_ID, "secret": SECRET, "code": "one-code",
                "grant_type": "authorization_code",
            })
            return httpx.Response(200, json={"access_token": TOKEN, "openid": SUBJECT,
                                            "scope": "snsapi_userinfo", "unionid": "ignored"})
        assert request.url.path == "/sns/userinfo"
        assert request.url.params["access_token"] == TOKEN
        assert request.url.params["openid"] == SUBJECT
        return httpx.Response(200, json={"openid": SUBJECT, "nickname": " 朋友👋\u0000\u202e ",
                                        "unionid": "different-ignored"})

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            provider = TencentSocialProvider("wechat", APP_ID, SECRET, CALLBACK.format("wechat"), client)
            result = await provider.exchange_code("one-code", provider.redirect_uri)
            assert result == VerifiedSocialIdentity("wechat", APP_ID, SUBJECT, "朋友👋")
            assert not hasattr(result, "access_token") and not hasattr(result, "unionid")
            assert SUBJECT not in repr(result)
    run(scenario())
    assert len(seen) == 2
    assert SECRET not in caplog.text and TOKEN not in caplog.text


@pytest.mark.parametrize("form_token,jsonp", [(False, False), (True, True)])
def test_qq_exchange_accepts_documented_json_form_and_parse_only_jsonp(form_token, jsonp):
    seen = []

    def handler(request):
        seen.append(request)
        assert request.url.host == "graph.qq.com"
        if request.url.path == "/oauth2.0/token":
            assert request.url.params["redirect_uri"] == CALLBACK.format("qq")
            assert request.url.params["fmt"] == "json" and request.url.params["client_secret"] == SECRET
            return httpx.Response(200, text=f"access_token={TOKEN}&expires_in=3600") if form_token else httpx.Response(
                200, json={"access_token": TOKEN, "expires_in": 3600},
            )
        if request.url.path == "/oauth2.0/me":
            body = json.dumps({"client_id": APP_ID, "openid": SUBJECT})
            return httpx.Response(200, text="callback( " + body + " );" if jsonp else body)
        assert request.url.path == "/user/get_user_info"
        assert request.url.params["oauth_consumer_key"] == APP_ID
        assert request.url.params["openid"] == SUBJECT
        return httpx.Response(200, json={"ret": 0, "nickname": "QQ 朋友"})

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            provider = TencentSocialProvider("qq", APP_ID, SECRET, CALLBACK.format("qq"), client)
            assert (await provider.exchange_code("one-code", provider.redirect_uri)).display_name == "QQ 朋友"
    run(scenario())
    assert len(seen) == 3


@pytest.mark.parametrize("name,stage,body", [
    ("wechat", 0, {"errcode": 40029, "errmsg": "sensitive provider message"}),
    ("wechat", 0, {"openid": SUBJECT}),
    ("wechat", 0, {"access_token": TOKEN, "openid": []}),
    ("wechat", 1, {"openid": "different", "nickname": "x"}),
    ("wechat", 1, {"openid": SUBJECT, "errcode": 1}),
    ("qq", 0, {"error": 100, "error_description": "sensitive provider message"}),
    ("qq", 1, {"client_id": "wrong", "openid": SUBJECT}),
    ("qq", 1, {"client_id": APP_ID, "openid": ""}),
    ("qq", 1, {"client_id": APP_ID, "openid": {}}),
    ("qq", 1, {"client_id": APP_ID, "openid": SUBJECT, "error": 1}),
    ("qq", 1, {"client_id": APP_ID, "openid": SUBJECT, "errcode": 1}),
    ("qq", 2, {"ret": 0, "error": 1}),
    ("qq", 2, {"ret": "0", "nickname": "x"}),
    ("qq", 2, {"ret": False, "nickname": "x"}),
    ("qq", 2, {"ret": 1, "msg": "sensitive provider message"}),
])
def test_invalid_upstream_identity_never_becomes_verified(name, stage, body, caplog):
    count = 0

    def handler(request):
        nonlocal count
        index = count
        count += 1
        if index == stage:
            return httpx.Response(200, json=body)
        if index == 0:
            return httpx.Response(200, json={"access_token": TOKEN, "openid": SUBJECT})
        return httpx.Response(200, json={"client_id": APP_ID, "openid": SUBJECT})

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            provider = TencentSocialProvider(name, APP_ID, SECRET, CALLBACK.format(name), client)
            with pytest.raises(SocialProviderError) as raised:
                await provider.exchange_code("sensitive-code", provider.redirect_uri)
            assert raised.value.code == "social_provider_error"
            assert "sensitive" not in raised.value.message
    run(scenario())
    assert count == stage + 1 and "sensitive" not in caplog.text


class Chunks(httpx.AsyncByteStream):
    def __init__(self, pieces):
        self.pieces = pieces

    async def __aiter__(self):
        for piece in self.pieces:
            yield piece


@pytest.mark.parametrize("kind", [
    "redirect", "status", "invalid-json", "array", "duplicate-key", "nan", "invalid-utf8",
    "jsonp-script", "large-length", "negative-length", "unicode-length", "long-length",
    "stream-large", "compressed", "read-timeout",
])
def test_untrusted_http_is_bounded_and_errors_do_not_leak_sensitive_url(kind, caplog):
    seen = []

    def handler(request):
        seen.append(request)
        if kind == "read-timeout":
            raise httpx.ReadTimeout("secret token url " + str(request.url), request=request)
        responses = {
            "redirect": lambda: httpx.Response(302, headers={"location": "https://attacker.example/leak"}),
            "status": lambda: httpx.Response(500),
            "invalid-json": lambda: httpx.Response(200, text="sensitive-invalid"),
            "array": lambda: httpx.Response(200, text="[]"),
            "duplicate-key": lambda: httpx.Response(200, text='{"access_token":"one","access_token":"two"}'),
            "nan": lambda: httpx.Response(200, text='{"access_token":NaN}'),
            "invalid-utf8": lambda: httpx.Response(200, content=b"\xff"),
            "jsonp-script": lambda: httpx.Response(200, text='callback({"openid":"x"});alert(1)'),
            "large-length": lambda: httpx.Response(200, headers={"content-length": str(MAX_RESPONSE_BYTES + 1)}),
            "negative-length": lambda: httpx.Response(200, headers={"content-length": "-1"}),
            "unicode-length": lambda: httpx.Response(200, headers={"content-length": b"\xff"}),
            "long-length": lambda: httpx.Response(200, headers={"content-length": "0" * 11}),
            "stream-large": lambda: httpx.Response(200, stream=Chunks([b"x" * MAX_RESPONSE_BYTES, b"x"])),
            "compressed": lambda: httpx.Response(200, headers={"content-encoding": "gzip"}, content=b"not-gzip"),
        }
        return responses[kind]()

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True) as client:
            provider = TencentSocialProvider("wechat", APP_ID, SECRET, CALLBACK.format("wechat"), client)
            with pytest.raises(SocialProviderError):
                await provider.exchange_code("sensitive-code", provider.redirect_uri)
    run(scenario())
    assert len(seen) == 1 and SECRET not in caplog.text and TOKEN not in caplog.text


@pytest.mark.parametrize("callback", [
    "http://allira.example.com/api/v1/auth/social/qq/callback",
    "https://127.0.0.1/api/v1/auth/social/qq/callback",
    "https://[::1]/api/v1/auth/social/qq/callback",
    "https://localhost/api/v1/auth/social/qq/callback",
    "https://x.localhost/api/v1/auth/social/qq/callback",
    "https://x.internal/api/v1/auth/social/qq/callback",
    "https://allira.example.com:443/api/v1/auth/social/qq/callback",
    "https://user:password@allira.example.com/api/v1/auth/social/qq/callback",
    "https://@allira.example.com/api/v1/auth/social/qq/callback",
    "https://allira.example.com/api/v1/auth/social/qq/callback?",
    "https://allira.example.com/api/v1/auth/social/qq/callback#",
    "https://allira.example.com/api/v1/auth/social/qq/callback?evil=1",
    "https://allira.example.com/api/v1/auth/social/wechat/callback",
    "https://allira.example.com/api/v1/auth/social/qq/callback/",
    "https://allira.example.com\\@attacker.example/api/v1/auth/social/qq/callback",
    "https://allira.example.com/api/v1/auth/social/qq/callback\n",
])
def test_real_callback_is_fixed_https_operator_domain_and_exact_path(callback):
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(500)))
    with pytest.raises(SocialProviderError):
        TencentSocialProvider("qq", APP_ID, SECRET, callback, client)
    run(client.aclose())


def test_test_mode_has_random_single_use_codes_bound_to_state_and_fixed_subject():
    provider = TestSocialProvider("qq", TEST_CALLBACK.format("qq"))
    first = parse_qs(urlsplit(provider.build_authorize_url("first-state", provider.redirect_uri)).query)
    second = parse_qs(urlsplit(provider.build_authorize_url("second-state", provider.redirect_uri)).query)
    assert first["code"] != second["code"]
    assert provider.matches_state(first["code"][0], "first-state")
    assert not provider.matches_state(first["code"][0], "second-state")
    identity = run(provider.exchange_code(first["code"][0], provider.redirect_uri))
    assert identity.subject == "allira-test-subject-qq"
    with pytest.raises(SocialProviderError):
        run(provider.exchange_code(first["code"][0], provider.redirect_uri))
    with pytest.raises(SocialProviderError):
        run(provider.exchange_code("universal-test-code", provider.redirect_uri))
    provider._codes[second["code"][0]] = ("second-state", 0)
    assert not provider.matches_state(second["code"][0], "second-state")
    with pytest.raises(SocialProviderError):
        run(provider.exchange_code(second["code"][0], provider.redirect_uri))


def test_whole_exchange_has_one_total_deadline_not_only_per_request(monkeypatch):
    from allira import social_providers

    real_timeout = asyncio.timeout
    timeouts = []

    def bounded_timeout(seconds):
        timeouts.append(seconds)
        return real_timeout(0.005 if len(timeouts) == 1 else 10)

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(500))) as client:
            provider = TencentSocialProvider("qq", APP_ID, SECRET, CALLBACK.format("qq"), client)
            calls = 0

            async def slow_responses(*args, **kwargs):
                nonlocal calls
                calls += 1
                await asyncio.sleep(0.02)
                return {"access_token": TOKEN}

            monkeypatch.setattr(provider, "_get", slow_responses)
            monkeypatch.setattr(social_providers.asyncio, "timeout", bounded_timeout)
            with pytest.raises(SocialProviderError):
                await provider.exchange_code("code", provider.redirect_uri)
            assert calls == 1 and timeouts == [15]

    run(scenario())


def test_provider_factory_defaults_disabled_and_test_needs_all_three_safety_gates():
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(500)))
    assert default_social_providers(SimpleNamespace(social_mode="disabled"), client) == {}
    with pytest.raises(ValueError, match="Unsupported social authorization mode"):
        default_social_providers(SimpleNamespace(social_mode="unknown"), client)
    baseline = {
        "social_mode": "test", "environment": "development", "provider": "mock", "sms_provider": "test-file",
        "wechat_redirect_uri": TEST_CALLBACK.format("wechat"), "qq_redirect_uri": TEST_CALLBACK.format("qq"),
    }
    providers = default_social_providers(SimpleNamespace(**baseline), client)
    assert set(providers) == {"wechat", "qq"} and all(p.simulated for p in providers.values())
    for key, bad in [("environment", "production"), ("provider", "openai"), ("sms_provider", "gateway")]:
        with pytest.raises(ValueError):
            default_social_providers(SimpleNamespace(**{**baseline, key: bad}), client)
    run(client.aclose())
