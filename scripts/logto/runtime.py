#!/usr/bin/env python3
"""Load only Logto's private runtime file and exec its pinned upstream CLI/core.

No values from the runtime file are printed. This does not read Allira's provider
configuration, modify its database, or start an Allira API process.
"""
from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

BASE = Path("/var/lib/allira/logto")
RELEASE = BASE / "releases/v1.46.0"
NODE = "/opt/allira/tools/node24/bin/node"
ALLOWED = {
    "DB_URL", "ENDPOINT", "ADMIN_ENDPOINT", "PORT", "ADMIN_PORT", "NODE_ENV",
    "TRUST_PROXY_HEADER", "DISABLE_TELEMETRY", "DATABASE_STATEMENT_TIMEOUT",
    "ALLIRA_LOGTO_CLIENT_ID", "ALLIRA_LOGTO_CLIENT_SECRET", "ALLIRA_LOGTO_ISSUER",
    "ALLIRA_LOGTO_MANAGEMENT_ID", "ALLIRA_LOGTO_MANAGEMENT_SECRET",
}


def private_values(path: Path) -> dict[str, str]:
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor) as source:
        info = os.fstat(source.fileno())
        if info.st_uid != os.getuid() or not stat.S_ISREG(info.st_mode):
            raise RuntimeError("Wrong Logto runtime file owner/type")
        if info.st_mode & 0o077 or info.st_nlink != 1:
            raise RuntimeError("Logto runtime file must be private mode 0600 and nlink=1")
        values: dict[str, str] = {}
        for line in source:
            line = line.rstrip("\n")
            if not line or line.startswith("#"):
                continue
            key, separator, value = line.partition("=")
            if not separator or key not in ALLOWED or key in values or "\x00" in value:
                raise RuntimeError("Invalid or unexpected Logto runtime entry")
            values[key] = value
        return values


def main() -> None:
    if os.getuid() == 0:
        raise RuntimeError("Logto must run as allira")
    os.umask(0o077)
    values = private_values(BASE / "runtime.env")
    if values.get("ENDPOINT") != "http://127.0.0.1:3001" or values.get("ADMIN_ENDPOINT") != "http://127.0.0.1:3002":
        raise RuntimeError("Unexpected non-loopback endpoint")
    if values.get("PORT") != "3001" or values.get("ADMIN_PORT") != "3002":
        raise RuntimeError("Unexpected Logto ports")
    # Do not forward OIDC client secrets to upstream core unnecessarily.
    environment = {
        "PATH": "/opt/allira/tools/node24/bin:/usr/bin:/bin",
        "HOME": str(BASE), "LANG": "C.UTF-8", "NODE_ENV": "production",
        "DISABLE_TELEMETRY": "1", "TRUST_PROXY_HEADER": "false",
        **{key: value for key, value in values.items() if not key.startswith("ALLIRA_")},
    }
    if not environment.get("DB_URL", "").startswith("postgresql://"):
        raise RuntimeError("Missing private Postgres URL")
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from prepare import verify_prepared
    verify_prepared(RELEASE)
    command = sys.argv[1:]
    if command == ["start"]:
        os.chdir(RELEASE / "packages/core")
        os.execve(NODE, [NODE, "build/index.js"], environment)
    if not command or command[0] != "cli":
        raise RuntimeError("Use start or cli <upstream arguments>")
    os.chdir(RELEASE)
    os.execve(NODE, [NODE, "packages/cli/bin/logto.js", *command[1:]], environment)


if __name__ == "__main__":
    main()
