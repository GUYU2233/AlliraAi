#!/usr/bin/env python3
"""Configure the private upstream Logto instance through its official Management API.

No Allira business database is read or written. Secret-bearing API responses stay
in memory/private files and are never printed. Mock channels are maintenance-only.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import stat
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from runtime import BASE, private_values

ENDPOINT = "http://127.0.0.1:3001"
ISSUER = ENDPOINT + "/oidc"
RESOURCE = "https://default.logto.app/api"
RUNTIME = BASE / "runtime.env"
CALLBACK = "http://127.0.0.1:8000/api/v1/auth/oidc/callback"
PUBLIC_DIST = Path("/opt/allira/workspace/apps/dev-console/dist")
PUBLIC_ICON_PATH = re.compile(r"/console-assets/public-icon-192-([a-f0-9]{16})\.png")


def private_text(path: Path) -> str:
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor) as source:
        info = os.fstat(source.fileno())
        if info.st_uid != os.getuid() or not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or info.st_nlink != 1:
            raise RuntimeError("Unsafe private Logto file")
        return source.read()


def save_runtime(values: dict[str, str]) -> None:
    if RUNTIME.resolve() != Path("/var/lib/allira/logto/runtime.env"):
        raise RuntimeError("Unexpected private runtime target")
    private_values(RUNTIME)
    descriptor = os.open(RUNTIME, os.O_WRONLY | os.O_TRUNC | os.O_NOFOLLOW)
    with os.fdopen(descriptor, "w") as target:
        target.write("".join(f"{key}={value}\n" for key, value in values.items()))
        target.flush()
        os.fsync(target.fileno())


def request(path: str, *, method: str = "GET", body: object | None = None,
            token: str | None = None, basic: tuple[str, str] | None = None,
            form: dict[str, str] | None = None) -> object:
    if not path.startswith(("/api/", "/oidc/")):
        raise RuntimeError("Non-local Logto API path")
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    if basic:
        headers["Authorization"] = "Basic " + base64.b64encode(":".join(basic).encode()).decode()
    data = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body).encode()
    if form is not None:
        headers["Content-Type"] = "application/x-www-form-urlencoded"
        data = urllib.parse.urlencode(form).encode()
    req = urllib.request.Request(ENDPOINT + path, method=method, data=data, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            content = response.read()
            return json.loads(content) if content else None
    except urllib.error.HTTPError as error:
        try:
            code = json.loads(error.read()).get("code", "upstream_error")
        except (ValueError, AttributeError):
            code = "upstream_error"
        raise RuntimeError(f"Logto API {method} {path.split('?')[0]} returned {error.code}: {code}") from None


def management_token(values: dict[str, str]) -> str:
    result = request("/oidc/token", method="POST", basic=(values["ALLIRA_LOGTO_MANAGEMENT_ID"], values["ALLIRA_LOGTO_MANAGEMENT_SECRET"]),
                     form={"grant_type": "client_credentials", "resource": RESOURCE, "scope": "all"})
    return result["access_token"]


def public_asset_bytes(path: Path, *, maximum: int) -> bytes:
    """Read only the normal public build, rejecting links and writable assets."""
    root = Path("/opt/allira/workspace")
    if not path.is_relative_to(PUBLIC_DIST) or not path.is_relative_to(root):
        raise RuntimeError("Unexpected public asset target")
    for directory in reversed(path.parent.relative_to(root).parents):
        parent = root / directory
        info = parent.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_mode & 0o022:
            raise RuntimeError("Unsafe public asset directory")
    # The parents iterator above ends before the immediate parent itself.
    info = path.parent.lstat()
    if (not stat.S_ISDIR(info.st_mode) or info.st_mode & 0o022
            or info.st_uid != os.getuid() or path.resolve() != path):
        raise RuntimeError("Unsafe public asset directory")
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor, "rb") as source:
        info = os.fstat(source.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or info.st_mode & 0o022 or info.st_nlink != 1 or info.st_size > maximum):
            raise RuntimeError("Unsafe public asset file")
        result = source.read(maximum + 1)
        if not result or len(result) > maximum:
            raise RuntimeError("Invalid public asset size")
        return result


def public_branding_url() -> str:
    manifest = json.loads(public_asset_bytes(PUBLIC_DIST / "test/manifest.webmanifest", maximum=65_536))
    if not isinstance(manifest, dict) or not isinstance(manifest.get("icons"), list):
        raise TypeError("Invalid public manifest")
    icons = [item for item in manifest["icons"] if isinstance(item, dict) and item.get("sizes") == "192x192"]
    if len(icons) != 1 or icons[0].get("type") != "image/png" or icons[0].get("purpose") != "any":
        raise RuntimeError("Expected one public 192 PNG icon")
    src = icons[0].get("src")
    match = PUBLIC_ICON_PATH.fullmatch(src) if isinstance(src, str) else None
    if match is None:
        raise RuntimeError("Unexpected public icon path")
    icon = public_asset_bytes(PUBLIC_DIST / "assets" / Path(src).name, maximum=262_144)
    if (hashlib.sha256(icon).hexdigest()[:16] != match.group(1)
            or icon[:16] != b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
            or icon[16:24] != (192).to_bytes(4, "big") * 2):
        raise RuntimeError("Public icon hash or dimensions mismatch")
    return "http://127.0.0.1:8000" + src


def branding_local(token: str) -> None:
    url = public_branding_url()
    before = request("/api/sign-in-exp", token=token)
    connectors_before = request("/api/connectors", token=token)
    if not isinstance(before, dict) or not isinstance(before.get("branding"), dict):
        raise TypeError("Unexpected sign-in experience response")
    if connectors_before != [] or before.get("hideLogtoBranding") is not False:
        raise RuntimeError("Expected disabled channels and visible Logto signature")
    branding = {**before["branding"], "logoUrl": url, "darkLogoUrl": url}
    # The official API replaces this JSONB column; merge its other keys first.
    request("/api/sign-in-exp", method="PATCH", token=token, body={"branding": branding})
    after = request("/api/sign-in-exp", token=token)
    connectors_after = request("/api/connectors", token=token)
    expected = {**before, "branding": branding}
    if after != expected or connectors_after != connectors_before:
        raise RuntimeError("Post-update branding-only verification failed")
    print(json.dumps({"brandingLocal": True, "publicIconValidated": True,
                      "onlyBrandingChanged": True, "authenticationPolicyUnchanged": True,
                      "poweredByLogtoPreserved": True, "connectors": 0, "secretsPrinted": False}))


def bootstrap(values: dict[str, str]) -> None:
    token = private_text(BASE / "admin-access-token")
    applications = request("/api/applications", token=token)
    for name in ("AlliraAi localhost", "AlliraAi lifecycle management"):
        if any(item["name"] == name for item in applications):
            raise RuntimeError("Existing Allira application: inspect rather than silently duplicate")
    client = request("/api/applications", method="POST", token=token, body={
        "name": "AlliraAi localhost", "type": "Traditional", "isThirdParty": False,
        "description": "Private loopback OIDC integration; no token exchange impersonation",
        "oidcClientMetadata": {"redirectUris": [CALLBACK], "postLogoutRedirectUris": ["http://127.0.0.1:8000/"]},
        "customClientMetadata": {"alwaysIssueRefreshToken": False},
    })
    secrets = request(f"/api/applications/{client['id']}/secrets", token=token)
    if len(secrets) != 1 or not secrets[0].get("value"):
        raise RuntimeError("Unexpected RP application secret response")
    values.update({"ALLIRA_LOGTO_CLIENT_ID": client["id"], "ALLIRA_LOGTO_CLIENT_SECRET": secrets[0]["value"], "ALLIRA_LOGTO_ISSUER": ISSUER})
    save_runtime(values)
    manager = request("/api/applications", method="POST", token=token, body={
        "name": "AlliraAi lifecycle management", "type": "MachineToMachine", "isThirdParty": False,
        "description": "Private server-only identity lifecycle management, never a user sign-in client",
    })
    roles = request("/api/roles", token=token)
    selected = [role["id"] for role in roles if role["name"] == "Logto Management API access" and role["type"] == "MachineToMachine"]
    if len(selected) != 1:
        raise RuntimeError("Missing standard OSS Management API role")
    request(f"/api/applications/{manager['id']}/roles", method="POST", token=token, body={"roleIds": selected})
    secrets = request(f"/api/applications/{manager['id']}/secrets", token=token)
    if len(secrets) != 1 or not secrets[0].get("value"):
        raise RuntimeError("Unexpected manager application secret response")
    values.update({"ALLIRA_LOGTO_MANAGEMENT_ID": manager["id"], "ALLIRA_LOGTO_MANAGEMENT_SECRET": secrets[0]["value"]})
    save_runtime(values)
    check = management_token(values)
    request("/api/users", token=check)
    print(json.dumps({"clientsCreated": True, "managementClientVerified": True, "issuer": ISSUER, "redirectUri": CALLBACK, "secretsPrinted": False}))


def mock_on(token: str) -> None:
    if os.environ.get("ALLIRA_LOGTO_MAINTENANCE") != "1":
        raise RuntimeError("Explicit ALLIRA_LOGTO_MAINTENANCE=1 is required")
    connectors = request("/api/connectors", token=token)
    if connectors:
        raise RuntimeError("Expected no connectors before mock maintenance")
    factories = request("/api/connector-factories", token=token)
    for identifier, package in (("mock-short-message-service", "connector-mock-sms"), ("mock-social-connector", "connector-mock-social")):
        if not any(item["id"] == identifier for item in factories):
            raise RuntimeError("Official mock factory not loaded; restart managed service after link")
        # Use the upstream bundled dummy template, never real provider credentials.
        config = json.loads((BASE / "releases/v1.46.0/packages/connectors" / package / "docs/config-template.json").read_text())
        request("/api/connectors", method="POST", token=token, body={"connectorId": identifier, "config": config})
    request("/api/sign-in-exp", method="PATCH", token=token, body={
        "signUp": {"identifiers": ["phone"], "password": False, "verify": True},
        "signIn": {"methods": [{"identifier": "phone", "password": False, "verificationCode": True, "isPasswordPrimary": False}]},
        "socialSignIn": {"automaticAccountLinking": False, "skipRequiredIdentifiers": False},
        "socialSignInConnectorTargets": ["mock-social"],
        "passwordPolicy": {"rejectPwned": False},
    })
    print(json.dumps({"maintenanceMockEnabled": True, "phoneRequired": True, "newPasswordRequired": False, "automaticAccountLinking": False, "realSms": False, "realSocial": False}))


def mock_off(token: str) -> None:
    connectors = request("/api/connectors", token=token)
    if any(item["connectorId"] not in {"mock-short-message-service", "mock-social-connector"} for item in connectors):
        raise RuntimeError("Unexpected non-mock connector; refusing automated cleanup")
    for connector in connectors:
        request(f"/api/connectors/{connector['id']}", method="DELETE", token=token)
    record = Path("/tmp/logto/mock_sms_record.txt")
    if record.exists():
        if record.resolve() != Path("/tmp/logto/mock_sms_record.txt"):
            raise RuntimeError("Unexpected official mock record target")
        private_text(record)
        record.unlink()
    print(json.dumps({"maintenanceMockEnabled": False, "smsConnectors": 0, "socialConnectors": 0, "mockOtpRemoved": not record.exists()}))


def clean_native(token: str) -> None:
    if os.environ.get("ALLIRA_LOGTO_MAINTENANCE") != "1":
        raise RuntimeError("Explicit maintenance opt-in required for native fixture cleanup")
    users = request("/api/users", token=token)
    removed = 0
    for user in users:
        identities = user.get("identities", {})
        identity = identities.get("mock-social", {})
        if str(identity.get("userId", "")).startswith("allira-native-social-"):
            if set(identities) != {"mock-social"}:
                raise RuntimeError("Unexpected linked identity on native fixture; refusing cleanup")
            request("/api/users/" + urllib.parse.quote(user["id"], safe=""), method="DELETE", token=token)
            removed += 1
    print(json.dumps({"nativeFixturesRemoved": removed, "alliraApiRequests": 0}))


def status(token: str) -> None:
    config = request("/api/sign-in-exp", token=token)
    connectors = request("/api/connectors", token=token)
    users = request("/api/users", token=token)
    print(json.dumps({"issuer": ISSUER, "signUp": config["signUp"], "signIn": config["signIn"], "socialSignIn": config["socialSignIn"], "connectorIds": [item["connectorId"] for item in connectors], "defaultTenantUsers": len(users), "secretsPrinted": False}))


def main() -> None:
    if os.getuid() == 0:
        raise RuntimeError("Run as allira")
    os.umask(0o077)
    action = sys.argv[1] if len(sys.argv) == 2 else ""
    values = private_values(RUNTIME)
    if action == "bootstrap":
        bootstrap(values)
        return
    token = management_token(values)
    if action == "branding-local":
        branding_local(token)
    elif action == "mock-on":
        mock_on(token)
    elif action == "mock-off":
        mock_off(token)
    elif action == "status":
        status(token)
    elif action == "clean-native":
        clean_native(token)
    else:
        raise RuntimeError("Use bootstrap, branding-local, mock-on, mock-off, clean-native or status")


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, TypeError, KeyError, urllib.error.URLError) as error:
        print(f"Logto management failed: {error}", file=sys.stderr)
        raise SystemExit(1) from None
