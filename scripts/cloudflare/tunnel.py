#!/usr/bin/env python3
"""Prepare one dedicated admin Tunnel; never publish DNS or start a connector.

All secret material stays on the remote host. This wrapper checks credential
metadata only; the pinned official CLI alone reads cert.pem and credentials.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import uuid
from pathlib import Path

BASE = Path("/var/lib/allira/cloudflare")
BINARY = Path("/opt/allira/tools/cloudflared-2026.11.1/cloudflared")
BINARY_BYTES = 40_346_301
BINARY_SHA256 = "97bb3b02d23a5980fc9c15a1a270e692da1923f103f920d5b16d4931d5ab55e7"
VERSION = "2026.11.1"
COMMIT = "518d7f7969b764df628bb4b1088f0f154901dc4e"
CERTIFICATE = Path("/home/allira/.cloudflared/cert.pem")
NAME = "allira-admin-dsah"
HOSTNAME = "dsah.guyufl.cc.cd"
TEAM = "guyufl"
AUDIENCE = "5d0bf52323efc13e59ce561efbfa100b2c9f443f4e2b7b420aea6e381cbb6621"
ACCESS_APP = "85d627b2-b8dc-4d52-ab1e-8882ee8da2a0"
CONFIG = BASE / "config.yml"
STATE = BASE / "tunnel-state.json"
PENDING = BASE / "new-tunnel-credentials.json"
BOOTSTRAP = BASE / "create-config.yml"
ADMIN_PATH = r"^/admin/?$"
API_PATH = r"^/api/v1/admin/.*$"
ASSET_PATH = r"^/console-assets/admin-[A-Za-z0-9_-]+\.(js|css)$"


def safe_directory(path: Path) -> None:
    info = path.lstat()
    if (path.resolve() != path or not stat.S_ISDIR(info.st_mode)
            or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700):
        raise RuntimeError("Private Tunnel directory metadata is unsafe")


def safe_metadata(path: Path, *, maximum: int = 65_536) -> None:
    """Do not read cert or credential contents, including for hashing."""
    info = path.lstat()
    if (path.resolve() != path or not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600
            or info.st_nlink != 1 or not 0 < info.st_size <= maximum):
        raise RuntimeError("Private Tunnel file metadata is unsafe")


def read_public_private(path: Path) -> dict:
    """Read only our non-secret configuration/state, never credentials or cert."""
    if path not in {CONFIG, STATE, BOOTSTRAP}:
        raise RuntimeError("Unexpected non-secret Tunnel metadata path")
    safe_metadata(path)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor) as source:
        result = json.load(source)
    if not isinstance(result, dict):
        raise TypeError("Tunnel configuration must be an object")
    return result


def exclusive_json(path: Path, value: dict) -> None:
    if path.parent != BASE or path not in {CONFIG, STATE, BOOTSTRAP}:
        raise RuntimeError("Unexpected new Tunnel metadata path")
    safe_directory(BASE)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "w") as target:
        json.dump(value, target, indent=2, sort_keys=True)
        target.write("\n")
        target.flush()
        os.fsync(target.fileno())


def official_environment() -> dict[str, str]:
    # Do not inherit tokens, proxy variables, default config paths or credentials.
    return {"HOME": str(BASE), "PATH": "/usr/bin:/bin", "LANG": "C.UTF-8",
            "NO_AUTOUPDATE": "true"}


def verify_binary() -> None:
    info = BINARY.lstat()
    if (BINARY.resolve() != BINARY or not stat.S_ISREG(info.st_mode)
            or info.st_mode & 0o022 or info.st_size != BINARY_BYTES
            or hashlib.sha256(BINARY.read_bytes()).hexdigest() != BINARY_SHA256):
        raise RuntimeError("Pinned official cloudflared binary verification failed")


def official(arguments: list[str], *, timeout: int = 30) -> str:
    try:
        result = subprocess.run([str(BINARY), *arguments], env=official_environment(),
                                capture_output=True, timeout=timeout, check=False, text=True)
    except subprocess.TimeoutExpired:
        raise RuntimeError("Official CLI timed out; raw output suppressed; do not retry creation blindly") from None
    if result.returncode != 0:
        raise RuntimeError("Official CLI failed; raw output suppressed; inspect state before any retry")
    if len(result.stdout) > 1_048_576 or len(result.stderr) > 1_048_576:
        raise RuntimeError("Unexpected official CLI output size; raw output suppressed")
    return result.stdout


def tunnel_uuid(value: object) -> str:
    if not isinstance(value, str):
        raise TypeError("Expected canonical dedicated Tunnel UUID")
    result = str(uuid.UUID(value))
    if result != value:
        raise RuntimeError("Expected canonical dedicated Tunnel UUID")
    return result


def desired_config(identifier: str) -> dict:
    identifier = tunnel_uuid(identifier)
    access = {"required": True, "teamName": TEAM, "audTag": [AUDIENCE]}
    rules = [{"hostname": HOSTNAME, "path": path, "service": "http://127.0.0.1:8000",
              "originRequest": {"httpHostHeader": HOSTNAME, "access": dict(access)}}
             for path in (ADMIN_PATH, API_PATH, ASSET_PATH)]
    rules.append({"service": "http_status:404"})
    # JSON is a YAML subset; write deterministically without an additional parser.
    return {"tunnel": identifier, "credentials-file": str(BASE / f"{identifier}.json"),
            "no-autoupdate": True, "metrics": "127.0.0.1:0", "protocol": "http2",
            "loglevel": "warn", "disable-path-normalization": False, "ingress": rules}


def finalize_created(identifier: str) -> None:
    """Finish only an already-created dedicated Tunnel, without reading secrets."""
    identifier = tunnel_uuid(identifier)
    if any(os.path.lexists(path) for path in (STATE, CONFIG)):
        raise RuntimeError("Dedicated preparation already finalized; refusing overwrite")
    # Upstream deliberately creates credentials as 0400. Normalize only that safe
    # official output to the requested 0600; do not read or hash its contents.
    descriptor = os.open(PENDING, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(descriptor)
        if (PENDING.resolve() != PENDING or not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.getuid() or info.st_nlink != 1
                or stat.S_IMODE(info.st_mode) not in {0o400, 0o600}
                or not 0 < info.st_size <= 65_536):
            raise RuntimeError("Unsafe official pending credential metadata")
        os.fchmod(descriptor, 0o600)
    finally:
        os.close(descriptor)
    safe_metadata(PENDING)
    target = BASE / f"{identifier}.json"
    if PENDING.resolve() != PENDING or target.parent != BASE or os.path.lexists(target):
        raise RuntimeError("Unsafe dedicated credentials move target")
    # Both absolute paths above are checked, and the UUID target must not exist.
    os.rename(PENDING, target)
    safe_metadata(target)
    exclusive_json(STATE, {"name": NAME, "id": identifier, "hostname": HOSTNAME,
                           "version": VERSION, "commit": COMMIT, "accessApp": ACCESS_APP,
                           "createdOnly": True, "dnsChanged": False, "connectorStarted": False})
    exclusive_json(CONFIG, desired_config(identifier))
    validate()


def create() -> None:
    # These exclusive files also prevent an uncertain network result from causing
    # an automatic second Tunnel. No fallback to or mutation of existing tunnels.
    if any(os.path.lexists(path) for path in (STATE, CONFIG, PENDING, BOOTSTRAP)):
        raise RuntimeError("Dedicated Tunnel preparation already attempted; refusing automatic creation")
    safe_metadata(CERTIFICATE, maximum=65_536)
    exclusive_json(BOOTSTRAP, {})
    raw = official(["tunnel", "--config", str(BOOTSTRAP), "--origincert", str(CERTIFICATE),
                    "--no-autoupdate", "create", "--output", "json",
                    "--credentials-file", str(PENDING), NAME], timeout=90)
    # The official create JSON may contain additional information. Allowlist only
    # the non-secret id/name; never store or print the raw result or credentials.
    created = json.loads(raw)
    if not isinstance(created, dict) or created.get("name") != NAME:
        raise RuntimeError("Unexpected Tunnel create response; raw output suppressed")
    finalize_created(tunnel_uuid(created.get("id")))


def configuration() -> tuple[str, dict]:
    state = read_public_private(STATE)
    identifier = tunnel_uuid(state.get("id"))
    if (state.get("name") != NAME or state.get("hostname") != HOSTNAME
            or state.get("version") != VERSION or state.get("commit") != COMMIT
            or state.get("accessApp") != ACCESS_APP or state.get("createdOnly") is not True
            or state.get("dnsChanged") is not False or state.get("connectorStarted") is not False):
        raise RuntimeError("Unexpected dedicated Tunnel preparation state")
    config = read_public_private(CONFIG)
    if config != desired_config(identifier):
        raise RuntimeError("Dedicated ingress configuration differs from strict allowlist")
    safe_metadata(BASE / f"{identifier}.json")
    return identifier, config


def validate() -> None:
    identifier, config = configuration()
    official(["tunnel", "--config", str(CONFIG), "ingress", "validate"])
    # This command parses ingress only: no origin HTTP, connector, DNS or cert.
    cases = [(f"https://{HOSTNAME}/admin", 0), (f"https://{HOSTNAME}/admin/", 0),
             (f"https://{HOSTNAME}/api/v1/admin/diagnostics", 1),
             (f"https://{HOSTNAME}/api/v1/admin/", 1),
             (f"https://{HOSTNAME}/console-assets/admin-AbC_123-.js", 2),
             (f"https://{HOSTNAME}/console-assets/admin-XyZ_321-.css", 2)]
    blocked = ["/", "/test", "/self-test", "/admin-extra", "/admin/x", "/api/v1/admin",
               "/api/v1/auth/oidc/callback", "/api/v1/conversations", "/api/v1/health",
               "/console-assets/main-AbC.js", "/console-assets/admin-A.js.map",
               "/console-assets/admin-A.png", "/console-assets/public-icon-192-deadbeef.png",
               "/console-assets/admin-.js", "/console-assets/admin-a/b.js", "/oidc/auth", "/console"]
    cases += [(f"https://{HOSTNAME}{path}", 3) for path in blocked]
    cases += [("https://other.example/admin", 3), (f"https://sub.{HOSTNAME}/admin", 3)]
    for url, expected in cases:
        output = official(["tunnel", "--config", str(CONFIG), "ingress", "rule", url])
        matched = re.findall(r"Matched rule #(\d+)", output)
        if matched != [str(expected)]:
            raise RuntimeError("Official offline ingress allowlist test failed; raw output suppressed")
    print(json.dumps({"tunnelId": identifier, "name": NAME, "hostname": HOSTNAME,
                      "pinnedBinaryVerified": True, "privateCredentialMetadataSafe": True,
                      "officialIngressValidated": True, "offlineRuleCases": len(cases),
                      "originAccessRequired": True, "hostHeaderPreserved": True,
                      "pathNormalizationEnabled": True,
                      "configSha256": hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest(),
                      "credentialContentsReadByWrapper": False, "dnsChanged": False,
                      "connectorStarted": False, "originRequests": 0}))


def main() -> None:
    if os.getuid() == 0 or len(sys.argv) != 2 or sys.argv[1] not in {"create", "validate"}:
        raise RuntimeError("Run as allira with create or validate; no start/DNS command exists")
    os.umask(0o077)
    safe_directory(BASE)
    lock_path = BASE / "prepare.lock"
    descriptor = os.open(lock_path, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(descriptor)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1):
            raise RuntimeError("Unsafe dedicated Tunnel lock")
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        verify_binary()
        create() if sys.argv[1] == "create" else validate()
    finally:
        os.close(descriptor)


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, ValueError, TypeError, KeyError):
        print("Dedicated Tunnel preparation/validation failed; sensitive output suppressed", file=sys.stderr)
        raise SystemExit(1) from None
