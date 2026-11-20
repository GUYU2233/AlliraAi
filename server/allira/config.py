"""Configuration is owned by the server, never supplied by the chat client."""

import ipaddress
import re
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="ALLIRA_", env_file=".env", extra="ignore", hide_input_in_errors=True,
    )

    environment: Literal["development", "production"] = "development"
    database_path: str = "allira.dev.sqlite3"
    allow_dev_auth: bool = False
    cors_origins: list[str] = Field(default_factory=list)
    session_ttl_seconds: int = Field(default=86400, ge=60, le=604800)
    registration_enabled: bool = True
    legacy_registration_enabled: bool = False
    # Legacy is a compatibility/test backend, never the default authentication core.
    auth_backend: Literal["legacy", "oidc"] = "oidc"
    # Operational gate: credentials alone must not advertise unavailable channels.
    oidc_enabled: bool = True
    oidc_issuer_url: str = ""
    oidc_client_id: str = ""
    oidc_client_secret: str = Field(default="", repr=False)
    oidc_redirect_uri: str = ""
    oidc_simulated: bool = False
    sms_provider: Literal["disabled", "gateway", "test-file"] = "disabled"
    sms_gateway_url: str = Field(default="", repr=False)
    sms_gateway_token: str = Field(default="", repr=False)
    sms_test_outbox_root: str = "/var/lib/allira/identity-tests"
    sms_test_outbox_path: str = ""
    phone_identity_hmac_key: str = Field(default="", repr=False)
    phone_code_ttl_seconds: Literal[300] = 300
    phone_resend_seconds: Literal[60] = 60
    social_mode: Literal["disabled", "tencent", "test"] = "disabled"
    social_state_ttl_seconds: Literal[300] = 300
    social_pending_ttl_seconds: Literal[600] = 600
    wechat_client_id: str = ""
    wechat_client_secret: str = Field(default="", repr=False)
    wechat_redirect_uri: str = ""
    qq_client_id: str = ""
    qq_client_secret: str = Field(default="", repr=False)
    qq_redirect_uri: str = ""
    browser_session_ttl_seconds: int = Field(default=604800, ge=60, le=2592000)
    backup_directory: str = "/var/lib/allira/backups/automatic"
    # Administrator diagnostics read bounded private metadata, never a dump or DB_URL.
    identity_backup_directory: str = "/var/lib/allira/logto/backups"
    context_recent_turns: int = Field(default=8, ge=1, le=50)
    summary_max_characters: int = Field(default=6000, ge=100, le=20000)
    initial_energy: int = Field(default=100, ge=0, le=1000000)
    economy_energy_cost: int = Field(default=1, ge=1, le=1000000)
    fast_energy_cost: int = Field(default=2, ge=1, le=1000000)
    run_rate_per_minute: int = Field(default=20, ge=1, le=1000)
    safety_blocked_terms: list[str] = Field(default_factory=list, repr=False)
    fast_upstream_base_url: str = ""
    fast_upstream_model: str = ""
    fast_upstream_api_key: str | None = Field(default=None, repr=False)
    fallback_to_economy: bool = True
    provider: Literal["mock", "openai"] = "mock"
    upstream_base_url: str = "https://api.openai.com/v1"
    upstream_model: str = ""
    upstream_api_key: str | None = Field(default=None, repr=False)
    upstream_max_tokens: int = Field(default=2048, ge=1, le=16384)
    run_timeout_seconds: float = Field(default=180, gt=0, le=1800)
    max_prompt_characters: int = Field(default=120000, ge=1000)
    max_output_characters: int = Field(default=60000, ge=1, le=1000000)
    max_concurrent_runs: int = Field(default=8, ge=1, le=128)
    mock_chunk_delay_seconds: float = Field(default=0, ge=0, le=5)
    console_enabled: bool = False
    console_dist_path: str = "../apps/dev-console/dist"
    admin_enabled: bool = False
    # Access is the deployment default; link is an explicit loopback-only legacy fixture.
    admin_auth_backend: Literal["link", "access"] = "access"
    admin_public_origin: str = ""
    access_team_domain: str = ""
    access_audience: str = ""
    access_allowed_emails: list[str] = Field(default_factory=list, repr=False)
    admin_session_ttl_seconds: int = Field(default=3600, ge=60, le=86400)
    admin_login_ttl_seconds: int = Field(default=300, ge=30, le=900)

    @model_validator(mode="after")
    def validate_settings(self) -> "Settings":
        if self.environment == "production" and self.allow_dev_auth:
            raise ValueError("Development authentication must be disabled in production.")
        if self.environment == "production" and (self.admin_enabled or self.console_enabled):
            raise ValueError("Development consoles and administration must be disabled in production.")
        if self.provider != "mock" and self.mock_chunk_delay_seconds:
            raise ValueError("Mock delay cannot be used with a real model provider.")
        if self.provider == "openai" and not self.upstream_model:
            raise ValueError("ALLIRA_UPSTREAM_MODEL is required for the openai provider.")
        if self.fast_energy_cost < self.economy_energy_cost:
            raise ValueError("Fast energy cost must not be lower than economy energy cost.")
        if self.fast_upstream_model and self.provider != "openai":
            raise ValueError("A real fast model cannot be configured while using the mock provider.")
        if "*" in self.cors_origins:
            raise ValueError("CORS requires explicit origins; wildcard is not supported.")
        access_values = (self.admin_public_origin, self.access_team_domain,
                         self.access_audience, self.access_allowed_emails)
        if any(access_values):
            if not all(access_values):
                raise ValueError("Access requires complete operator-owned configuration.")
            if not re.fullmatch(
                r"https://[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.cloudflareaccess\.com",
                self.access_team_domain,
            ) or not re.fullmatch(r"[a-f0-9]{64}", self.access_audience):
                raise ValueError("Invalid fixed Access identity configuration.")
            self._validate_admin_origin(self.admin_public_origin)
            if not 1 <= len(self.access_allowed_emails) <= 20:
                raise ValueError("Invalid Access administrator allowlist.")
            for email in self.access_allowed_emails:
                if (not email.isascii() or not 3 <= len(email) <= 254
                        or email.count("@") != 1 or email.startswith("@") or email.endswith("@")
                        or any(ord(c) <= 32 or ord(c) == 127 for c in email)):
                    raise ValueError("Invalid Access administrator allowlist.")
            self.access_allowed_emails = sorted({email.lower() for email in self.access_allowed_emails})
            if self.admin_auth_backend != "access":
                raise ValueError("Public administration requires Access authentication.")
        oidc_values = (self.oidc_issuer_url, self.oidc_client_id,
                       self.oidc_client_secret, self.oidc_redirect_uri)
        if any(oidc_values):
            if not all(oidc_values):
                raise ValueError("OIDC requires complete operator-owned configuration.")
            self._validate_oidc_url(self.oidc_issuer_url)
            self._validate_oidc_url(self.oidc_redirect_uri, callback=True)
            if (not self.oidc_client_id.isascii() or not 1 <= len(self.oidc_client_id) <= 256
                    or any(ord(c) <= 32 or ord(c) == 127 for c in self.oidc_client_id)):
                raise ValueError("Invalid OIDC client configuration.")
            if not 1 <= len(self.oidc_client_secret.encode("utf-8")) <= 4096:
                raise ValueError("Invalid OIDC client configuration.")
        if self.auth_backend == "oidc" and (
            self.sms_provider != "disabled" or self.social_mode != "disabled"
        ):
            raise ValueError("OIDC owns authentication; legacy identity channels must be disabled.")
        if self.oidc_simulated and (
            self.auth_backend != "oidc" or not all(oidc_values)
            or self.environment != "development" or self.provider != "mock"
        ):
            raise ValueError("Simulated OIDC requires explicit development Mock configuration.")
        if self.sms_provider != "disabled" or self.social_mode != "disabled":
            try:
                key_bytes = self.phone_identity_hmac_key.encode("utf-8")
            except UnicodeEncodeError:
                raise ValueError("Invalid identity security configuration.") from None
            if not 32 <= len(key_bytes) <= 4096:
                raise ValueError("A private identity HMAC key is required when identity channels are enabled.")
        if self.sms_provider == "gateway":
            from .sms import GatewaySmsSender

            GatewaySmsSender(self.sms_gateway_url, self.sms_gateway_token)
        if self.sms_provider == "test-file" and (
            self.environment != "development" or self.provider != "mock"
            or not self.sms_test_outbox_path
        ):
            raise ValueError("A test SMS outbox requires explicit development Mock configuration.")
        if self.social_mode == "test" and (
            self.environment != "development" or self.provider != "mock"
            or self.sms_provider != "test-file"
        ):
            raise ValueError("Simulated social login requires development Mock and a private test SMS outbox.")
        if self.social_mode != "disabled":
            for name in ("wechat", "qq"):
                client_id = getattr(self, name + "_client_id")
                secret = getattr(self, name + "_client_secret")
                callback = getattr(self, name + "_redirect_uri")
                configured = bool(callback) if self.social_mode == "test" else bool(client_id or secret or callback)
                if not configured:
                    continue
                if self.social_mode == "tencent" and not (client_id and secret and callback):
                    raise ValueError("Social login requires complete operator-owned configuration.")
                self._validate_social_callback(callback, name, self.social_mode == "test")
        return self

    @staticmethod
    def _validate_admin_origin(value: str) -> None:
        try:
            parsed = urlsplit(value)
            host = parsed.hostname or ""
            valid = (
                value.isascii() and len(value) <= 253 and value == f"https://{host}"
                and parsed.scheme == "https" and parsed.port is None
                and parsed.username is None and parsed.password is None
                and not parsed.path and not parsed.query and not parsed.fragment
                and re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", host) is not None
                and "." in host and ".." not in host
                and all(1 <= len(label) <= 63 and not label.startswith("-")
                        and not label.endswith("-") for label in host.split("."))
                and not host.endswith((".localhost", ".local", ".internal", ".invalid", ".test"))
            )
            try:
                ipaddress.ip_address(host)
                valid = False
            except ValueError:
                pass
        except (ValueError, TypeError):
            valid = False
        if not valid:
            raise ValueError("Invalid fixed public administrator origin.")

    @staticmethod
    def _validate_oidc_url(value: str, *, callback: bool = False) -> None:
        try:
            parsed = urlsplit(value)
            host = parsed.hostname or ""
            try:
                loopback = ipaddress.ip_address(host).is_loopback
            except ValueError:
                loopback = host == "localhost"
            valid = (
                value.isascii() and 1 <= len(value) <= 2048
                and not any(ord(c) <= 32 or ord(c) == 127 for c in value)
                and "\\" not in value and "%" not in parsed.netloc
                and parsed.username is None and parsed.password is None
                and bool(host) and not parsed.query and not parsed.fragment
                and not parsed.netloc.endswith(":")
                and (parsed.port is None or 1 <= parsed.port <= 65535)
                and (parsed.scheme == "https" or (
                    parsed.scheme == "http" and loopback and parsed.port is not None
                ))
                and (not callback or parsed.path == "/api/v1/auth/oidc/callback")
            )
        except (ValueError, TypeError):
            valid = False
        if not valid:
            raise ValueError("Invalid fixed OIDC endpoint configuration.")

    @staticmethod
    def _validate_social_callback(value: str, provider: str, simulated: bool) -> None:
        try:
            parsed = urlsplit(value)
            host = parsed.hostname or ""
            try:
                address = ipaddress.ip_address(host)
            except ValueError:
                address = None
            clean = (
                value.isascii() and 1 <= len(value) <= 2048
                and not any(ord(c) <= 32 or ord(c) == 127 for c in value)
                and "\\" not in value and "?" not in value and "#" not in value
                and parsed.username is None and parsed.password is None
                and parsed.path == "/api/v1/auth/social/" + provider + "/callback"
                and bool(host) and not parsed.query and not parsed.fragment
                and not parsed.netloc.endswith(":")
            )
            if simulated:
                valid = clean and parsed.scheme in {"http", "https"} and (
                    host == "localhost" or (address is not None and address.is_loopback)
                ) and (parsed.port is None or 1 <= parsed.port <= 65535)
            else:
                valid = clean and parsed.scheme == "https" and parsed.port is None and (
                    address is None and "." in host and host != "localhost"
                    and not host.endswith((".localhost", ".local", ".internal", ".invalid", ".test"))
                )
        except (ValueError, TypeError):
            valid = False
        if not valid:
            raise ValueError("Invalid fixed social callback configuration.")
