"""Offline, same-host SQLite snapshots; never start application recovery here.

Restore revokes authentication material but preserves passwords, energy and run state.
The normal application startup must run recover_interrupted_runs before serving a restore.
No keep-sessions option exists. Filesystem durability depends on local fsync support;
this is not remote disaster recovery. Paths must not be concurrently renamed by another
process running as the same OS user. Stale create locks require operator inspection.
"""

from __future__ import annotations

import argparse
import ctypes
import errno
import hashlib
import json
import os
import re
import secrets
import sqlite3
import stat
import sys
from contextlib import closing, contextmanager
from datetime import UTC, datetime
from functools import wraps
from pathlib import Path

DATABASE_FILE = "snapshot.sqlite3"
MANIFEST_FILE = "manifest.json"
LOCK_FILE = ".allira-backup.lock"
BACKUP_NAME = re.compile(r"allira-\d{8}T\d{6}Z-[0-9a-f]{16}\Z")
FIELDS = {"version", "createdAt", "source", "databaseFile", "schemaVersion", "sha256",
          "size", "modeSummary"}
REVOKE_TABLES = ("sessions", "admin_access_sessions", "admin_sessions", "admin_login_codes",
                 "account_recovery_codes", "browser_sessions", "phone_challenges",
                 "social_pending", "social_oauth_states", "social_browsers",
                 "oidc_pending", "oidc_states", "oidc_browsers")


class BackupError(Exception):
    """A sanitized, stable error code; never include underlying exception text."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _sanitized(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except BackupError:
            raise
        except Exception:  # noqa: BLE001 - sanitize boundary / best-effort retention
            raise BackupError("operation_failed") from None
    return wrapped


def _path(path: Path, *, exists: bool = True) -> Path:
    path = Path(path)
    if not path.is_absolute() or ".." in path.parts:
        raise BackupError("absolute_path_required")
    for part in reversed((path, *path.parents)):
        try:
            info = part.lstat()
        except FileNotFoundError:
            if part == path and not exists:
                continue
            raise BackupError("path_missing") from None
        if stat.S_ISLNK(info.st_mode):
            raise BackupError("symlink_rejected")
        if part != path and not stat.S_ISDIR(info.st_mode):
            raise BackupError("invalid_parent")
    return path


def _secure_dir(path: Path) -> None:
    _path(path)
    info = path.lstat()
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) & 0o077):
        raise BackupError("insecure_directory")


def _regular(path: Path, *, private: bool = False) -> None:
    _path(path)
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode):
        raise BackupError("regular_file_required")
    if private and (info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o077):
        raise BackupError("insecure_file")


def _sync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _sync_file(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _publish_directory(source: Path, target: Path) -> None:
    # Linux renameat2(RENAME_NOREPLACE): atomic directory publish, never overwrite.
    # Fail closed on platforms lacking this primitive rather than check-then-rename.
    libc = ctypes.CDLL(None, use_errno=True)
    rename = getattr(libc, "renameat2", None)
    if rename is None:
        raise BackupError("exclusive_rename_unavailable")
    result = rename(-100, os.fsencode(source), -100, os.fsencode(target), 1)
    if result != 0:
        if ctypes.get_errno() == errno.EEXIST:
            raise BackupError("backup_name_exists")
        raise BackupError("exclusive_rename_failed")


def _new_file(path: Path) -> None:
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    os.close(fd)


def _read_db(path: Path, *, immutable: bool = False) -> sqlite3.Connection:
    uri = path.as_uri() + "?mode=ro" + ("&immutable=1" if immutable else "")
    return sqlite3.connect(uri, uri=True, timeout=5)


def _checks(connection: sqlite3.Connection) -> int:
    for pragma in ("quick_check", "integrity_check"):
        if connection.execute(f"PRAGMA {pragma}").fetchall() != [("ok",)]:
            raise BackupError("sqlite_integrity_failed")
    if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
        raise BackupError("sqlite_foreign_key_failed")
    return connection.execute("PRAGMA user_version").fetchone()[0]


def _digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise BackupError("invalid_manifest")
        result[key] = value
    return result


def _verify(backup_dir: Path) -> dict:
    _secure_dir(backup_dir)
    manifest_path = backup_dir / MANIFEST_FILE
    database = backup_dir / DATABASE_FILE
    _regular(manifest_path, private=True)
    _regular(database, private=True)
    if manifest_path.stat().st_size > 65536:
        raise BackupError("manifest_too_large")
    try:
        manifest = json.loads(manifest_path.read_text(), object_pairs_hook=_pairs)
    except (ValueError, UnicodeError):
        raise BackupError("invalid_manifest") from None
    if not isinstance(manifest, dict) or set(manifest) != FIELDS:
        raise BackupError("invalid_manifest")
    if type(manifest["version"]) is not int or manifest["version"] != 1:
        raise BackupError("unsupported_manifest_version")
    if (manifest["source"] != "allira" or manifest["databaseFile"] != DATABASE_FILE
            or manifest["modeSummary"] != "directory=0700,database=0600,manifest=0600"
            or type(manifest["schemaVersion"]) is not int or manifest["schemaVersion"] < 0
            or type(manifest["size"]) is not int or manifest["size"] <= 0
            or not isinstance(manifest["sha256"], str)
            or re.fullmatch(r"[0-9a-f]{64}", manifest["sha256"]) is None
            or not isinstance(manifest["createdAt"], str)):
        raise BackupError("invalid_manifest")
    try:
        datetime.strptime(manifest["createdAt"], "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        raise BackupError("invalid_manifest") from None
    if database.stat().st_size != manifest["size"] or _digest(database) != manifest["sha256"]:
        raise BackupError("checksum_mismatch")
    with closing(_read_db(database, immutable=True)) as connection:
        if _checks(connection) != manifest["schemaVersion"]:
            raise BackupError("schema_mismatch")
    return {"backupName": backup_dir.name, **manifest}


@_sanitized
def verify_backup(backup_dir: Path) -> dict:
    """Read-only hash, manifest, schema and SQLite verification without app imports."""
    return _verify(Path(backup_dir))


@contextmanager
def _lock(output_dir: Path):
    path = output_dir / LOCK_FILE
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    except FileExistsError:
        raise BackupError("backup_locked") from None
    identity = os.fstat(fd)
    try:
        os.write(fd, json.dumps({"pid": os.getpid()}).encode())
        os.fsync(fd)
        yield
    finally:
        os.close(fd)
        current = path.lstat()
        if (current.st_dev, current.st_ino) == (identity.st_dev, identity.st_ino):
            path.unlink()
            _sync_dir(output_dir)


def _remove_snapshot(directory: Path, parent: Path) -> None:
    # Only our two exact regular files are removed; no recursive deletion follows links.
    if directory.resolve() != parent.resolve() / directory.name:
        raise BackupError("unsafe_retention_target")
    _secure_dir(directory)
    if {entry.name for entry in directory.iterdir()} != {DATABASE_FILE, MANIFEST_FILE}:
        raise BackupError("unsafe_retention_target")
    for name in (DATABASE_FILE, MANIFEST_FILE):
        _regular(directory / name, private=True)
    for name in (DATABASE_FILE, MANIFEST_FILE):
        (directory / name).unlink()
    directory.rmdir()


def _retain(output_dir: Path, retain: int, newest: Path) -> bool:
    candidates = []
    failed = False
    for directory in output_dir.iterdir():
        if not BACKUP_NAME.fullmatch(directory.name):
            continue
        try:
            result = _verify(directory)
            candidates.append((result["createdAt"], directory.name, directory))
        except Exception:  # noqa: BLE001 - sanitize boundary / best-effort retention
            failed = True
    # Always protect the just-created backup, including within a single timestamp second.
    older = sorted((item for item in candidates if item[2] != newest), reverse=True)
    for _, _, directory in older[max(0, retain - 1):]:
        try:
            _remove_snapshot(directory, output_dir)
        except Exception:  # noqa: BLE001 - sanitize boundary / best-effort retention
            failed = True
    _sync_dir(output_dir)
    return failed


@_sanitized
def create_backup(database: Path, output_dir: Path, retain: int = 7) -> dict:
    """Snapshot an existing SQLite database, including committed WAL, under a create lock."""
    database, output_dir = Path(database), Path(output_dir)
    if type(retain) is not int or not 1 <= retain <= 100:
        raise BackupError("invalid_retention")
    _regular(database)
    # SQLite may open these beside a WAL source; never let a sidecar redirect reads.
    for suffix in ("-wal", "-shm", "-journal"):
        sidecar = Path(str(database) + suffix)
        if sidecar.exists() or sidecar.is_symlink():
            _regular(sidecar)
    _path(output_dir, exists=False)
    if (output_dir == database or database in output_dir.parents
            or output_dir in database.parents):
        raise BackupError("invalid_output_directory")
    try:
        output_dir.mkdir(mode=0o700)
        _sync_dir(output_dir.parent)
    except FileExistsError:
        pass
    _secure_dir(output_dir)
    with _lock(output_dir):
        token = secrets.token_hex(8)
        timestamp = datetime.now(UTC)
        final = output_dir / (timestamp.strftime("allira-%Y%m%dT%H%M%SZ-") + token)
        temporary = output_dir / (".tmp-" + token)
        temporary.mkdir(mode=0o700)
        try:
            snapshot = temporary / DATABASE_FILE
            _new_file(snapshot)
            with closing(_read_db(database)) as source, closing(sqlite3.connect(snapshot)) as destination:
                source.backup(destination, pages=128)
                destination.execute("PRAGMA journal_mode=DELETE")
                schema_version = _checks(destination)
            _sync_file(snapshot)
            manifest = {"version": 1, "createdAt": timestamp.strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "source": "allira", "databaseFile": DATABASE_FILE,
                        "schemaVersion": schema_version, "size": snapshot.stat().st_size,
                        "sha256": _digest(snapshot),
                        "modeSummary": "directory=0700,database=0600,manifest=0600"}
            manifest_path = temporary / MANIFEST_FILE
            _new_file(manifest_path)
            with manifest_path.open("w") as stream:
                json.dump(manifest, stream, sort_keys=True)
                stream.flush()
                os.fsync(stream.fileno())
            _sync_dir(temporary)
            _publish_directory(temporary, final)
            _sync_dir(output_dir)
            result = {"backupName": final.name, **manifest}
            try:
                result["retentionWarning"] = _retain(output_dir, retain, final)
            except Exception:  # noqa: BLE001 - sanitize boundary / best-effort retention
                result["retentionWarning"] = True
            return result
        finally:
            if temporary.exists():
                for name in (DATABASE_FILE, MANIFEST_FILE):
                    entry = temporary / name
                    if entry.exists() and not entry.is_symlink():
                        entry.unlink()
                temporary.rmdir()
            # Never remove a published snapshot on retention or fsync failure.


def _restore_target_clear(target: Path) -> None:
    _path(target, exists=False)
    _secure_dir(target.parent)
    if target.exists():
        raise BackupError("target_exists")
    for suffix in ("-wal", "-shm", "-journal"):
        sidecar = Path(str(target) + suffix)
        # lstat catches dangling symlinks too; do not open or remove foreign files.
        try:
            sidecar.lstat()
        except FileNotFoundError:
            continue
        raise BackupError("target_sidecar_exists")


@_sanitized
def restore_backup(backup_dir: Path, target: Path) -> dict:
    """Restore only to a new offline path and revoke every known authentication table."""
    backup_dir, target = Path(backup_dir), Path(target)
    verified = _verify(backup_dir)
    _restore_target_clear(target)
    temporary = target.parent / (".restore-" + secrets.token_hex(8) + ".sqlite3")
    _new_file(temporary)
    try:
        with closing(_read_db(backup_dir / DATABASE_FILE, immutable=True)) as source:  # noqa: SIM117
            with closing(sqlite3.connect(temporary)) as destination:
                source.backup(destination, pages=128)
                destination.execute("PRAGMA journal_mode=DELETE")
                with destination:
                    tables = {row[0] for row in destination.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'")}
                    for table in REVOKE_TABLES:
                        if table in tables:
                            destination.execute(f'DELETE FROM "{table}"')
                    for table in REVOKE_TABLES:
                        if table in tables and destination.execute(
                                f'SELECT count(*) FROM "{table}"').fetchone()[0]:
                            raise BackupError("credential_revocation_failed")
                _checks(destination)
        _sync_file(temporary)
        _restore_target_clear(target)
        try:
            os.link(temporary, target, follow_symlinks=False)
        except FileExistsError:
            raise BackupError("target_exists") from None
        temporary.unlink()
        _sync_dir(target.parent)
    finally:
        if temporary.exists():
            temporary.unlink()
    return {"backupName": verified["backupName"], "schemaVersion": verified["schemaVersion"],
            "sessionsRevoked": True, "recoveryRequired": True}


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise BackupError("invalid_arguments")


def main(argv: list[str] | None = None) -> int:
    parser = _Parser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("create")
    create.add_argument("--database", required=True, type=Path)
    create.add_argument("--output-dir", required=True, type=Path)
    create.add_argument("--retain", type=int, default=7)
    verify = sub.add_parser("verify")
    verify.add_argument("--backup-dir", required=True, type=Path)
    restore = sub.add_parser("restore")
    restore.add_argument("--backup-dir", required=True, type=Path)
    restore.add_argument("--target", required=True, type=Path)
    try:
        args = parser.parse_args(argv)
        if args.command == "create":
            result = create_backup(args.database, args.output_dir, args.retain)
        elif args.command == "verify":
            result = verify_backup(args.backup_dir)
        else:
            result = restore_backup(args.backup_dir, args.target)
    except BackupError as error:
        print(json.dumps({"status": "error", "code": error.code}))
        return 1
    print(json.dumps({"status": "ok", **result}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
