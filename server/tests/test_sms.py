"""SMS adapter tests use MockTransport/private temporary files, never real delivery."""

import asyncio
import json
import os
from types import SimpleNamespace

import httpx
import pytest

from allira.sms import (
    DisabledSmsSender,
    GatewaySmsSender,
    SmsDeliveryError,
    TestFileSmsSender,
    create_sms_sender,
)

PHONE = "+8613800138000"
CODE = "039182"


@pytest.mark.parametrize("url", [
    "http://sms.example.com/send", "https://localhost/send", "https://127.0.0.1/send",
    "https://[::1]/send", "https://192.168.1.2/send", "https://10.0.0.1/send",
    "https://169.254.169.254/send", "https://2130706433/send", "https://127.1/send",
    "https://sms.local/send", "https://sms.internal/send", "https://sms.lan/send",
    "https://sms.example.com:8443/send", "https://u:p@sms.example.com/send",
    "https://sms.example.com/send?key=secret", "https://sms.example.com/send#secret",
    "https://sms.example.com/send?", "https://sms.example.com/send#",
    "https://sms.example.com./send", "https://sms.example.com:/send",
    "https://sms.example.com\\@evil.com/send", "https://sms.example.com/\nfoo",
    "https://短信.example.com/send", "//sms.example.com/send", "", None,
])
def test_gateway_rejects_unsafe_endpoints_without_exposing_values(url):
    with pytest.raises(ValueError, match="^Invalid SMS gateway configuration\\.$"):
        GatewaySmsSender(url, "secret")


@pytest.mark.parametrize("key", ["", "has space", "has\nnewline", "密钥", "x" * 4097, None])
def test_gateway_rejects_header_injection(key):
    with pytest.raises(ValueError, match="^Invalid SMS gateway configuration\\.$"):
        GatewaySmsSender("https://sms.example.com/send", key)


def test_gateway_exact_trusted_contract_and_no_redirects():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"accepted": True})

    async def exercise():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True) as client:
            sender = GatewaySmsSender("https://sms.example.com/send", "test-secret", client=client)
            await sender.send(PHONE, CODE, "register")
            assert "test-secret" not in repr(sender)

    asyncio.run(exercise())
    assert len(seen) == 1
    request = seen[0]
    assert request.method == "POST" and str(request.url) == "https://sms.example.com/send"
    assert request.headers["authorization"] == "Bearer test-secret"
    assert request.headers["accept-encoding"] == "identity"
    assert json.loads(request.content) == {
        "phone": PHONE, "code": CODE, "purpose": "register", "expiresInSeconds": 300,
    }
    assert request.extensions["timeout"]["connect"] == 5


@pytest.mark.parametrize("status,body,headers", [
    (301, b"redirect-secret", {"location": "https://elsewhere.example.com/send"}),
    (401, b"private-upstream-error", {}), (500, b"private-upstream-error", {}),
    (200, b'{"accepted":false}', {}), (200, b'{"accepted":1}', {}),
    (200, b'[{"accepted":true}]', {}), (200, b"invalid json secret", {}),
    (200, b'{"accepted":true}', {"content-encoding": "gzip"}),
    (200, b'{"accepted":true}', {"content-length": "999999"}),
    (200, b'{"accepted":true}', {"content-length": "bad"}),
    (200, b'{"accepted":true,"padding":"' + b"x" * 65536 + b'"}', {}),
])
def test_gateway_rejects_untrusted_responses_and_sanitizes_errors(status, body, headers, caplog):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(status, content=body, headers=headers)

    async def exercise():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True) as client:
            sender = GatewaySmsSender("https://sms.example.com/send", "secret-key", client=client)
            with pytest.raises(SmsDeliveryError) as result:
                await sender.send(PHONE, CODE, "login")
            assert str(result.value) == "SMS delivery is unavailable."
            assert result.value.__cause__ is None and result.value.__suppress_context__

    asyncio.run(exercise())
    assert len(calls) == 1
    assert CODE not in caplog.text and "secret-key" not in caplog.text


def test_gateway_transport_error_has_no_exception_body():
    def handler(request):
        raise httpx.ReadTimeout("private gateway URL and code " + CODE, request=request)

    async def exercise():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(SmsDeliveryError) as result:
                await GatewaySmsSender("https://sms.example.com/send", "secret", client=client).send(
                    PHONE, CODE, "login",
                )
            assert CODE not in str(result.value)
            assert result.value.__cause__ is None

    asyncio.run(exercise())


@pytest.mark.parametrize("phone,code,purpose", [
    ("13800138000", CODE, "login"), (PHONE, "12345", "login"),
    (PHONE, "１２３４５６", "login"), (PHONE, CODE, "arbitrary"),
])
def test_gateway_validates_internal_payload_before_transport(phone, code, purpose):
    def handler(request):
        pytest.fail("Invalid delivery must not reach transport")

    async def exercise():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(SmsDeliveryError):
                await GatewaySmsSender("https://sms.example.com/send", "secret", client=client).send(
                    phone, code, purpose,
                )

    asyncio.run(exercise())


def test_disabled_sender_and_default_factory_never_send():
    assert isinstance(create_sms_sender(SimpleNamespace()), DisabledSmsSender)
    with pytest.raises(SmsDeliveryError):
        asyncio.run(DisabledSmsSender().send(PHONE, CODE, "login"))
    with pytest.raises(ValueError, match="Unsupported SMS provider"):
        create_sms_sender(SimpleNamespace(sms_provider="anything"))


def private_settings(tmp_path, **overrides):
    root = tmp_path / "private"
    root.mkdir(mode=0o700, exist_ok=True)
    return SimpleNamespace(
        **{"environment": "development", "provider": "mock", "sms_provider": "test-file",
           "sms_test_outbox_root": str(root), "sms_test_outbox_path": str(root / "outbox.jsonl"),
           **overrides},
    )


def test_private_outbox_is_explicit_mock_only_jsonl(tmp_path, caplog):
    settings = private_settings(tmp_path)
    sender = create_sms_sender(settings)
    asyncio.run(sender.send(PHONE, CODE, "social_bind"))
    asyncio.run(sender.send(PHONE, "000000", "set_password"))
    path = settings.sms_test_outbox_path
    assert os.stat(path).st_mode & 0o777 == 0o600
    with open(path, encoding="utf-8") as source:
        records = [json.loads(line) for line in source]
    assert records == [
        {"phone": PHONE, "code": CODE, "purpose": "social_bind", "expiresInSeconds": 300},
        {"phone": PHONE, "code": "000000", "purpose": "set_password", "expiresInSeconds": 300},
    ]
    assert CODE not in caplog.text and PHONE not in caplog.text


@pytest.mark.parametrize("overrides", [
    {"environment": "production"}, {"provider": "openai"}, {"sms_provider": "disabled"},
    {"sms_test_outbox_path": "relative.jsonl"}, {"sms_test_outbox_root": "relative"},
])
def test_private_outbox_refuses_unsafe_configuration(tmp_path, overrides):
    with pytest.raises(ValueError):
        TestFileSmsSender(private_settings(tmp_path, **overrides))


def test_outbox_refuses_outside_root_world_readable_parent_and_files(tmp_path):
    settings = private_settings(tmp_path, sms_test_outbox_path=str(tmp_path / "outside.jsonl"))
    with pytest.raises(ValueError):
        TestFileSmsSender(settings)
    settings = private_settings(tmp_path)
    os.chmod(settings.sms_test_outbox_root, 0o755)
    with pytest.raises(ValueError):
        TestFileSmsSender(settings)
    os.chmod(settings.sms_test_outbox_root, 0o700)
    with open(settings.sms_test_outbox_path, "w", encoding="utf-8"):
        pass
    os.chmod(settings.sms_test_outbox_path, 0o644)
    with pytest.raises(ValueError):
        TestFileSmsSender(settings)


def test_outbox_rechecks_symlinks_hardlinks_and_modes_on_each_send(tmp_path):
    settings = private_settings(tmp_path)
    sender = TestFileSmsSender(settings)
    path = settings.sms_test_outbox_path
    os.chmod(path, 0o644)
    with pytest.raises(SmsDeliveryError):
        asyncio.run(sender.send(PHONE, CODE, "login"))
    os.chmod(path, 0o600)
    os.link(path, tmp_path / "hardlink")
    with pytest.raises(SmsDeliveryError):
        asyncio.run(sender.send(PHONE, CODE, "login"))
    # A separately configured symlink is never followed, even to a private regular file.
    link = tmp_path / "private" / "link.jsonl"
    link.symlink_to(path)
    with pytest.raises(ValueError):
        TestFileSmsSender(private_settings(tmp_path, sms_test_outbox_path=str(link)))


def test_outbox_refuses_symlink_in_parent_chain(tmp_path):
    settings = private_settings(tmp_path)
    alias = tmp_path / "alias"
    alias.symlink_to(settings.sms_test_outbox_root, target_is_directory=True)
    with pytest.raises(ValueError):
        TestFileSmsSender(private_settings(tmp_path, sms_test_outbox_root=str(alias),
                                           sms_test_outbox_path=str(alias / "outbox.jsonl")))
