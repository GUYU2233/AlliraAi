"""Create a private loopback PostgreSQL cluster for the pinned Logto deployment.

Run only as the existing low-privilege allira user on hklite-lv4. This helper never
installs system packages, starts a database, edits a live cluster, or prints secrets.
Download/extract the pinned Debian packages first; see the identity operations guide.
"""

from __future__ import annotations

import json
import os
import pwd
import secrets
import stat
import subprocess
from pathlib import Path

ROOT = Path("/var/lib/allira/postgresql")
BIN = ROOT / "runtime/usr/lib/postgresql/17/bin"
LIB = ROOT / "runtime/usr/lib/x86_64-linux-gnu"
DATA = ROOT / "data"
SOCKET = ROOT / "socket"
ENVIRONMENT = ROOT / "logto-db.env"


def private_directory(path: Path) -> None:
    if path.is_symlink() or path.resolve() != path:
        raise RuntimeError("Unexpected database installation path.")
    if not path.exists():
        path.mkdir(mode=0o700)
    info = path.stat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
        raise RuntimeError("Database directories must be private and owned by allira.")


def exclusive_file(path: Path, content: str) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as output:
        output.write(content)
        output.flush()
        os.fsync(output.fileno())


def main() -> None:
    os.umask(0o077)
    if pwd.getpwuid(os.getuid()).pw_name != "allira":
        raise RuntimeError("Run this setup only as allira on the remote development host.")
    private_directory(ROOT)
    if DATA.exists() or DATA.is_symlink() or ENVIRONMENT.exists() or ENVIRONMENT.is_symlink():
        raise RuntimeError("Refusing to replace an existing database or credential file.")
    private_directory(SOCKET)
    environment = dict(os.environ, LD_LIBRARY_PATH=str(LIB), LC_ALL="C.UTF-8")
    result = subprocess.run(
        [str(BIN / "initdb"), "-D", str(DATA), "--username=allira", "--encoding=UTF8",
         "--locale=C.UTF-8", "--auth-local=peer", "--auth-host=scram-sha-256"],
        env=environment, capture_output=True, text=True, timeout=60, check=False,
    )
    if result.returncode:
        raise RuntimeError("Private PostgreSQL initialization failed; details suppressed.")
    # Include-only file keeps the initdb-produced configuration intact. No wildcard
    # address or trust authentication is ever used, including the first launch.
    with (DATA / "postgresql.conf").open("a", encoding="utf-8") as output:
        output.write("\ninclude = 'allira-loopback.conf'\n")
        output.flush()
        os.fsync(output.fileno())
    exclusive_file(DATA / "allira-loopback.conf", (
        "listen_addresses = '127.0.0.1'\n"
        "port = 54329\n"
        "unix_socket_directories = '/var/lib/allira/postgresql/socket'\n"
        "unix_socket_permissions = 0700\n"
        "password_encryption = 'scram-sha-256'\n"
        "max_connections = 50\n"
        "shared_buffers = '128MB'\n"
        "log_statement = 'none'\n"
        "log_min_error_statement = 'panic'\n"
        "log_connections = off\n"
        "log_disconnections = off\n"
        "logging_collector = off\n"
    ))
    # Replace only the newly generated HBA file before the first launch. Peer is
    # limited to the service account; every TCP connection needs SCRAM credentials.
    (DATA / "pg_hba.conf").write_text(
        "local all allira peer\n"
        "host all all 127.0.0.1/32 scram-sha-256\n",
        encoding="utf-8",
    )
    os.chmod(DATA / "pg_hba.conf", 0o600)
    password = secrets.token_hex(32)
    exclusive_file(ENVIRONMENT, f"DB_URL=postgresql://logto:{password}@127.0.0.1:54329/logto\n")
    print(json.dumps({"ok": True, "clusterInitialized": True, "started": False,
                      "host": "127.0.0.1", "port": 54329, "secretsPrinted": False}))


if __name__ == "__main__":
    main()
