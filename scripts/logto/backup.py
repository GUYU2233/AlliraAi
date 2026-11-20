#!/usr/bin/env python3
"""Private native Logto PostgreSQL backup and new-target restore verification.

Uses the existing allira peer administrator, never widens the Logto runtime role.
No HTTP, business SQLite access, public listener, secret output, role creation,
existing-database restore, or scheduler changes. The dump contains sensitive data.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import stat
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import unquote, urlsplit

from runtime import BASE, private_values

PG_ROOT = Path("/var/lib/allira/postgresql/runtime")
PG_BIN = PG_ROOT / "usr/lib/postgresql/17/bin"
BACKUPS = BASE / "backups"
VERIFY_PREFIX = "allira_verify_logto_"


def private_directory(path: Path, *, create: bool = False) -> None:
    if create:
        path.mkdir(mode=0o700, exist_ok=True)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise RuntimeError("Backup directory must be an owner-only real directory")
    if path.resolve() != path:
        raise RuntimeError("Symlinked backup directory is forbidden")


def digest(path: Path) -> str:
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def private_file(path: Path) -> None:
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077 or info.st_nlink != 1:
        raise RuntimeError("Backup file must be an owner-only regular file")
    if path.resolve() != path:
        raise RuntimeError("Symlinked backup file is forbidden")


def environment(database: str) -> dict[str, str]:
    return {
        "PATH": "/usr/bin:/bin", "HOME": str(BASE), "LANG": "C.UTF-8",
        "LD_LIBRARY_PATH": str(PG_ROOT / "usr/lib/x86_64-linux-gnu"),
        "PGHOST": "/var/lib/allira/postgresql/socket", "PGPORT": "54329",
        "PGUSER": "allira", "PGDATABASE": database, "PGCONNECT_TIMEOUT": "10",
    }


def run(tool: str, arguments: list[str], *, database: str = "postgres", sql: str | None = None) -> bytes:
    process = subprocess.run(
        [str(PG_BIN / tool), *arguments], env=environment(database),
        input=None if sql is None else sql.encode(), capture_output=True, timeout=180, check=False,
    )
    if process.returncode:
        # libpq diagnostics can contain connection metadata; do not print them.
        raise RuntimeError(f"Native {tool} failed with exit {process.returncode}; no private diagnostics printed")
    return process.stdout


def query(sql: str, *, database: str = "postgres") -> str:
    return run("psql", ["--no-psqlrc", "--no-password", "--tuples-only", "--no-align", "--set=ON_ERROR_STOP=1"],
               database=database, sql=sql).decode().strip()


def source_database() -> str:
    url = urlsplit(private_values(BASE / "runtime.env")["DB_URL"])
    database = unquote(url.path.removeprefix("/"))
    if url.scheme != "postgresql" or url.hostname != "127.0.0.1" or url.port != 54329 or database != "logto":
        raise RuntimeError("Unexpected source PostgreSQL database; refusing backup")
    return database


def fresh_destination() -> Path:
    private_directory(BASE)
    private_directory(BACKUPS, create=True)
    name = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ_") + secrets.token_hex(6)
    destination = BACKUPS / name
    if destination.parent != BACKUPS or destination.exists():
        raise RuntimeError("Backup destination is not fresh")
    destination.mkdir(mode=0o700)
    private_directory(destination)
    return destination


def create_backup() -> Path:
    source = source_database()
    if query("SELECT current_user='allira' AND rolsuper AND rolcreatedb FROM pg_roles WHERE rolname=current_user;") != "t":
        raise RuntimeError("Existing peer administrator unavailable; no roles will be changed")
    destination = fresh_destination()
    dump = destination / "logto.pgdump"
    descriptor = os.open(dump, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    os.close(descriptor)
    started = datetime.now(UTC).isoformat()
    run("pg_dump", ["--no-password", "--format=custom", "--no-owner", "--no-acl", "--file", str(dump)], database=source)
    private_file(dump)
    dump_digest = digest(dump)
    manifest = {
        "formatVersion": 1, "logtoVersion": "1.46.0", "postgresMajor": 17,
        "dumpStartedAt": started, "dumpCompletedAt": datetime.now(UTC).isoformat(),
        "format": "pg_dump custom", "noOwner": True, "noAcl": True,
        "sha256": dump_digest, "bytes": dump.stat().st_size,
        "containsSensitiveIdentityData": True, "remoteCopy": False,
        "businessSqliteIncluded": False, "clusterRolesIncluded": False,
        "runtimeSecretsIncluded": False,
        "consistency": "One native PostgreSQL dump snapshot; not an atomic cross-store Allira snapshot",
    }
    target = destination / "manifest.json"
    with target.open("x") as output:
        json.dump(manifest, output, indent=2)
        output.write("\n")
    os.chmod(target, 0o600)
    return destination


def verify_backup(destination: Path) -> dict[str, object]:
    if not destination.is_absolute() or destination.parent != BACKUPS or not re.fullmatch(r"\d{8}T\d{6}Z_[0-9a-f]{12}", destination.name):
        raise RuntimeError("Verify target must be one exact private generated backup directory")
    private_directory(BASE)
    private_directory(BACKUPS)
    private_directory(destination)
    dump, manifest_path = destination / "logto.pgdump", destination / "manifest.json"
    private_file(dump)
    private_file(manifest_path)
    manifest = json.loads(manifest_path.read_text())
    if manifest.get("sha256") != digest(dump) or manifest.get("bytes") != dump.stat().st_size:
        raise RuntimeError("Private dump integrity check failed")
    toc = run("pg_restore", ["--list", str(dump)])
    if b"TABLE DATA" not in toc or b"users" not in toc or b"applications" not in toc:
        raise RuntimeError("Dump TOC is missing expected identity data")
    verification_database = VERIFY_PREFIX + secrets.token_hex(12)
    if not re.fullmatch(r"allira_verify_logto_[0-9a-f]{24}", verification_database) or verification_database in {"logto", "postgres", "template0", "template1"}:
        raise RuntimeError("Unsafe generated restore database name")
    # Identifier alphabet is fixed above. No caller-supplied SQL identifier is used.
    created = False
    try:
        query(f'CREATE DATABASE "{verification_database}" TEMPLATE template0 ENCODING \'UTF8\';')
        created = True
        query(f'REVOKE CONNECT ON DATABASE "{verification_database}" FROM PUBLIC;')
        run("pg_restore", ["--no-password", "--exit-on-error", "--no-owner", "--no-acl", "--dbname", verification_database, str(dump)])
        restored = json.loads(query("""SELECT json_build_object(
            'tables',(SELECT count(*) FROM information_schema.tables WHERE table_schema='public' AND table_type='BASE TABLE'),
            'users',(SELECT count(*) FROM public.users),
            'applications',(SELECT count(*) FROM public.applications),
            'tenants',(SELECT count(*) FROM public.tenants)
        )::text;""", database=verification_database))
        if restored["tables"] < 20 or restored["applications"] < 1 or restored["tenants"] < 2:
            raise RuntimeError("Restored schema/data is unexpectedly incomplete")
    finally:
        if created:
            # Revalidate the exact generated target immediately before DROP.
            if not re.fullmatch(r"allira_verify_logto_[0-9a-f]{24}", verification_database):
                raise RuntimeError("Refusing unsafe restore-test cleanup")
            query(f'DROP DATABASE "{verification_database}";')
    report = {
        "verifiedAt": datetime.now(UTC).isoformat(), "sha256Verified": True,
        "nativeTocListed": True, "newDatabaseRestorePassed": True,
        "verificationDatabaseRemoved": True, "existingDatabaseOverwritten": False,
        "restoredCounts": restored, "logtoRuntimeRolePrivilegesChanged": False,
        "alliraBusinessDatabaseAccessed": False, "publicServiceStarted": False,
        "rolesAndPrivateRuntimeStillRequiredForDeployment": True,
    }
    report_path = destination / ("restore-verification-" + secrets.token_hex(6) + ".json")
    with report_path.open("x") as output:
        json.dump(report, output, indent=2)
        output.write("\n")
    os.chmod(report_path, 0o600)
    return report


def main() -> None:
    if os.getuid() == 0 or os.environ.get("USER") != "allira":
        raise RuntimeError("Run only as the existing allira account")
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["create", "create-and-verify", "verify"])
    parser.add_argument("--backup", type=Path)
    arguments = parser.parse_args()
    if arguments.action == "verify":
        if arguments.backup is None:
            raise RuntimeError("verify needs --backup with an exact generated directory")
        destination = arguments.backup
    else:
        if arguments.backup is not None:
            raise RuntimeError("Creating backups never accepts an existing destination")
        destination = create_backup()
    report = verify_backup(destination) if arguments.action != "create" else {}
    print(json.dumps({"backupDirectory": str(destination), "privateNativeBackupCreated": arguments.action != "verify", **report}))


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, OSError, ValueError, KeyError, subprocess.TimeoutExpired) as error:
        print(f"Logto native backup failed: {type(error).__name__}; private details suppressed", file=sys.stderr)
        raise SystemExit(1) from None
