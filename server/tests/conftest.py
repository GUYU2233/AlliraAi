"""Keep regression suites isolated from live process credentials and network egress."""

import os

import httpx
import pytest


@pytest.fixture(autouse=True)
def isolated_environment_and_http(monkeypatch, tmp_path):
    # Settings(_env_file=None) alone still reads inherited process variables.
    # Neither the API service's environment nor a developer's .env may opt tests
    # into a paid provider or select the serving SQLite file.
    for name in list(os.environ):
        if name.startswith("ALLIRA_"):
            monkeypatch.delenv(name)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ALLIRA_PROVIDER", "mock")
    # Historical tests explicitly exercise the compatibility backend, never the
    # deployment default. New RP/integration fixtures opt into OIDC themselves.
    monkeypatch.setenv("ALLIRA_AUTH_BACKEND", "legacy")
    # One-time links are exercised only by historical loopback compatibility tests.
    # New Access fixtures select the deployment backend explicitly.
    monkeypatch.setenv("ALLIRA_ADMIN_AUTH_BACKEND", "link")
    # Compatibility fixtures exercise the historical username registration route only
    # under an explicit test opt-in; deployments default to verified phone registration.
    monkeypatch.setenv("ALLIRA_LEGACY_REGISTRATION_ENABLED", "true")

    async def no_async_network(*args, **kwargs):
        raise AssertionError("Regression tests must use an explicit mock/ASGI transport.")

    def no_sync_network(*args, **kwargs):
        raise AssertionError("Regression tests must use an explicit mock/ASGI transport.")

    # ASGITransport, TestClient's in-process transport and MockTransport are not
    # affected. Only the real socket transports are blocked.
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", no_async_network)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", no_sync_network)
