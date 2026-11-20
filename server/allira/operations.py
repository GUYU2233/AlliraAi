"""Bounded, non-sensitive operational checks; never probe a model or perform a backup.

A recent manifest is a freshness hint, not a verified checksum or a restore drill.
The public readiness endpoint discloses no database paths, counts or provider data.
"""

import json
import os
import re
import sqlite3
import stat
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from fastapi import Request

from .config import Settings
from .db import APIError, now
from .safety import ModelRoute, configured_routes
from .schemas import DTO

API_VERSION = "0.8.0"
BACKUP_NAME = re.compile(r"allira-\d{8}T\d{6}Z-[0-9a-f]{16}\Z")
BACKUP_STALE_SECONDS = 36 * 60 * 60
MAX_BACKUP_ENTRIES = 1000


class DatabaseDiagnostic(DTO):
    ok: bool
    journalMode: str
    databaseBytes: int
    walBytes: int


class RunDiagnostic(DTO):
    running: int
    inFlight: int
    closing: int
    capacity: int


class SessionDiagnostic(DTO):
    bearer: int
    browser: int
    admin: int


class BackupDiagnostic(DTO):
    configured: bool
    latestAt: str | None
    ageSeconds: int | None
    status: Literal["missing", "recent", "stale", "invalid"]


class IdentityDiagnostic(DTO):
    backend: Literal["legacy", "oidc"]
    configured: bool
    enabled: bool
    simulated: bool
    linkedAccounts: int
    legacyAccounts: int
    developerUsers: int
    pendingFlows: int
    serviceProbe: Literal["not_performed"] = "not_performed"
    administration: Literal["separate"] = "separate"


class IdentityBackupDiagnostic(BackupDiagnostic):
    databaseBytes: int | None = None
    restoreStatus: Literal["not_reported", "recorded", "invalid"] = "not_reported"
    restoreVerifiedAt: str | None = None
    # Metadata about an earlier drill is never an integrity check of the dump now.
    checksumVerified: Literal[False] = False


class AdminRouteLimits(DTO):
    runTimeoutSeconds: float
    maxConcurrentRuns: int
    maxPromptCharacters: int
    maxOutputCharacters: int
    upstreamMaxTokens: int
    runRatePerMinute: int


class AdminRouteOverview(DTO):
    items: list[ModelRoute]
    limits: AdminRouteLimits
    configurationOnly: Literal[True] = True


class AdminDiagnostics(DTO):
    version: str
    startedAt: str
    uptimeSeconds: int
    schemaVersion: int
    database: DatabaseDiagnostic
    runs: RunDiagnostic
    sessions: SessionDiagnostic
    backup: BackupDiagnostic
    identity: IdentityDiagnostic
    identityBackup: IdentityBackupDiagnostic
    warnings: list[str]


def initialize_operations(app) -> None:
    app.state.started_at = now()
    app.state.started_monotonic = time.monotonic()


def readiness(request: Request) -> dict[str, str]:
    try:
        if request.app.state.db.conn.execute("SELECT 1").fetchone()[0] != 1:
            raise sqlite3.DatabaseError
    except (sqlite3.Error, AttributeError):
        raise APIError(503, "not_ready", "The service is not ready.") from None
    return {"status": "ready"}


def _no_duplicates(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate manifest key.")
        result[key] = value
    return result


def _no_symlinks(path: Path) -> None:
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError("Invalid path.")
    for component in (path, *path.parents):
        if stat.S_ISLNK(component.lstat().st_mode):
            raise ValueError("Unsafe path.")


def backup_diagnostic(directory: str) -> BackupDiagnostic:
    result = BackupDiagnostic(configured=bool(directory), latestAt=None, ageSeconds=None, status="missing")
    if not directory:
        return result
    try:
        root = Path(directory)
        if not root.exists() and not root.is_symlink():
            return result
        _no_symlinks(root)
        info = root.stat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o077:
            raise ValueError("Insecure backup directory.")
        names = []
        with os.scandir(root) as entries:
            for index, entry in enumerate(entries):
                if index >= MAX_BACKUP_ENTRIES:
                    raise ValueError("Backup directory entry limit exceeded.")
                if BACKUP_NAME.fullmatch(entry.name):
                    names.append(entry.name)
        if not names:
            return result
        latest = root / max(names)
        _no_symlinks(latest)
        latest_info = latest.stat()
        if (not stat.S_ISDIR(latest_info.st_mode) or latest_info.st_uid != os.geteuid()
                or stat.S_IMODE(latest_info.st_mode) & 0o077):
            raise ValueError("Invalid backup directory.")
        manifest_path = latest / "manifest.json"
        _no_symlinks(manifest_path)
        info = manifest_path.stat()
        if (not stat.S_ISREG(info.st_mode) or info.st_size > 65536
                or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o077):
            raise ValueError("Invalid manifest file.")
        snapshot = latest / "snapshot.sqlite3"
        _no_symlinks(snapshot)
        snapshot_info = snapshot.stat()
        if (not stat.S_ISREG(snapshot_info.st_mode) or snapshot_info.st_uid != os.geteuid()
                or stat.S_IMODE(snapshot_info.st_mode) & 0o077):
            raise ValueError("Invalid snapshot file.")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"), object_pairs_hook=_no_duplicates)
        if (not isinstance(manifest, dict) or manifest.get("version") != 1
                or type(manifest.get("version")) is not int
                or manifest.get("source") != "allira"
                or manifest.get("databaseFile") != "snapshot.sqlite3"
                or not isinstance(manifest.get("createdAt"), str)
                or not isinstance(manifest.get("sha256"), str)
                or re.fullmatch(r"[0-9a-f]{64}", manifest["sha256"]) is None
                or type(manifest.get("size")) is not int or manifest["size"] <= 0
                or manifest["size"] != snapshot_info.st_size
                or type(manifest.get("schemaVersion")) is not int or manifest["schemaVersion"] < 0
                or manifest.get("modeSummary") != "directory=0700,database=0600,manifest=0600"):
            raise ValueError("Invalid manifest.")
        timestamp = datetime.strptime(manifest["createdAt"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
        age = int(datetime.now(UTC).timestamp() - timestamp.timestamp())
        if age < -300:
            raise ValueError("Future backup timestamp.")
        result.latestAt = manifest["createdAt"]
        result.ageSeconds = max(0, age)
        result.status = "stale" if age > BACKUP_STALE_SECONDS else "recent"
    except (OSError, ValueError, TypeError, OverflowError, RecursionError):
        result.status = "invalid"
    return result


def route_overview(settings: Settings) -> AdminRouteOverview:
    """Expose policy, not keys, model names, destinations or model connectivity."""
    return AdminRouteOverview(
        items=configured_routes(settings),
        limits=AdminRouteLimits(
            runTimeoutSeconds=settings.run_timeout_seconds,
            maxConcurrentRuns=settings.max_concurrent_runs,
            maxPromptCharacters=settings.max_prompt_characters,
            maxOutputCharacters=settings.max_output_characters,
            upstreamMaxTokens=settings.upstream_max_tokens,
            runRatePerMinute=settings.run_rate_per_minute,
        ),
    )


def identity_diagnostic(db, settings: Settings) -> IdentityDiagnostic:
    configured = all((settings.oidc_issuer_url, settings.oidc_client_id,
                      settings.oidc_client_secret, settings.oidc_redirect_uri))
    enabled = settings.auth_backend == "oidc" and configured and settings.oidc_enabled
    linked = db.conn.execute(
        "SELECT COUNT(*) FROM oidc_identities WHERE issuer = ?", (settings.oidc_issuer_url,),
    ).fetchone()[0]
    registered = db.conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0]
    developers = db.conn.execute(
        "SELECT COUNT(*) FROM users u WHERE NOT EXISTS (SELECT 1 FROM accounts a WHERE a.userId = u.id)",
    ).fetchone()[0]
    stamp = time.time()
    pending = db.conn.execute(
        "SELECT (SELECT COUNT(*) FROM oidc_states WHERE issuer = ? AND expiresAt > ? "
        "AND status IN ('issued', 'exchanging')) + "
        "(SELECT COUNT(*) FROM oidc_pending WHERE issuer = ? AND expiresAt > ?)",
        (settings.oidc_issuer_url, stamp, settings.oidc_issuer_url, stamp),
    ).fetchone()[0]
    return IdentityDiagnostic(
        backend=settings.auth_backend, configured=configured, enabled=enabled,
        simulated=enabled and settings.oidc_simulated, linkedAccounts=linked,
        legacyAccounts=max(0, registered - linked), developerUsers=developers, pendingFlows=pending,
    )


def _private_directory(path: Path) -> None:
    _no_symlinks(path)
    info = path.lstat()
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) & 0o077):
        raise ValueError("Invalid private metadata directory.")


def _private_regular(path: Path):
    _no_symlinks(path)
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) & 0o077 or info.st_nlink != 1):
        raise ValueError("Invalid private metadata file.")
    return info


def _private_json(path: Path) -> dict:
    before = _private_regular(path)
    if before.st_size > 65536:
        raise ValueError("Metadata size limit exceeded.")
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        opened = os.fstat(descriptor)
        if ((opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)
                or not stat.S_ISREG(opened.st_mode) or opened.st_uid != os.geteuid()
                or opened.st_nlink != 1 or stat.S_IMODE(opened.st_mode) & 0o077):
            raise ValueError("Metadata changed during open.")
        with os.fdopen(descriptor, "rb", closefd=False) as source:
            raw = source.read(65537)
        if len(raw) > 65536:
            raise ValueError("Metadata size limit exceeded.")
    finally:
        os.close(descriptor)
    value = json.loads(raw.decode("utf-8"), object_pairs_hook=_no_duplicates,
                       parse_constant=lambda _: _invalid_metadata_constant())
    if not isinstance(value, dict):
        raise TypeError("Invalid metadata document.")
    return value


def _invalid_metadata_constant():
    raise ValueError("Non-finite metadata constant.")


def _metadata_time(value) -> datetime:
    if not isinstance(value, str) or not 20 <= len(value) <= 40 or "T" not in value:
        raise ValueError("Invalid metadata timestamp.")
    timestamp = datetime.fromisoformat(value)
    if timestamp.tzinfo is None or timestamp.utcoffset().total_seconds() != 0:
        raise ValueError("Metadata timestamps must be UTC.")
    if timestamp.timestamp() > time.time() + 300:
        raise ValueError("Future metadata timestamp.")
    return timestamp


def _bounded_directory_names(path: Path) -> list[str]:
    names = []
    with os.scandir(path) as entries:
        for index, entry in enumerate(entries):
            if index >= MAX_BACKUP_ENTRIES:
                raise ValueError("Metadata directory entry limit exceeded.")
            names.append(entry.name)
    return names


def identity_backup_diagnostic(directory: str) -> IdentityBackupDiagnostic:
    """Read one bounded private manifest/report; never open a dump, hash it or connect to PG."""
    result = IdentityBackupDiagnostic(configured=bool(directory), latestAt=None,
                                      ageSeconds=None, status="missing")
    if not directory:
        return result
    try:
        root = Path(directory)
        if not root.is_absolute() or ".." in root.parts:
            raise ValueError("Invalid metadata root.")
        if not root.exists() and not root.is_symlink():
            return result
        _private_directory(root)
        candidates = [name for name in _bounded_directory_names(root)
                      if re.fullmatch(r"\d{8}T\d{6}Z_[0-9a-f]{12}", name)]
        if not candidates:
            return result
        latest = root / max(candidates)
        _private_directory(latest)
        dump_info = _private_regular(latest / "logto.pgdump")
        manifest = _private_json(latest / "manifest.json")
        if (type(manifest.get("formatVersion")) is not int or manifest["formatVersion"] != 1
                or manifest.get("logtoVersion") != "1.46.0"
                or type(manifest.get("postgresMajor")) is not int or manifest["postgresMajor"] != 17
                or manifest.get("format") != "pg_dump custom"
                or manifest.get("noOwner") is not True or manifest.get("noAcl") is not True
                or manifest.get("containsSensitiveIdentityData") is not True
                or manifest.get("businessSqliteIncluded") is not False
                or manifest.get("clusterRolesIncluded") is not False
                or manifest.get("runtimeSecretsIncluded") is not False
                or not isinstance(manifest.get("sha256"), str)
                or re.fullmatch(r"[0-9a-f]{64}", manifest["sha256"]) is None
                or type(manifest.get("bytes")) is not int or manifest["bytes"] <= 0
                or manifest["bytes"] != dump_info.st_size):
            raise ValueError("Invalid identity backup manifest.")
        started = _metadata_time(manifest.get("dumpStartedAt"))
        completed = _metadata_time(manifest.get("dumpCompletedAt"))
        if completed < started:
            raise ValueError("Invalid backup timestamp order.")
        age = max(0, int(time.time() - completed.timestamp()))
        result.latestAt, result.ageSeconds, result.databaseBytes = (
            completed.isoformat(), age, dump_info.st_size,
        )
        result.status = "stale" if age > BACKUP_STALE_SECONDS else "recent"
        # Reports have random suffixes. Select by mtime then name, not by a
        # fabricated ordering of those suffixes. This still describes a record,
        # never current restore validity. A malformed selected report is explicit.
        try:
            reports = []
            for name in _bounded_directory_names(latest):
                if re.fullmatch(r"restore-verification-[0-9a-f]{12}\.json", name):
                    info = _private_regular(latest / name)
                    reports.append((info.st_mtime_ns, name))
            if reports:
                report = _private_json(latest / max(reports)[1])
                verified = _metadata_time(report.get("verifiedAt"))
                if (verified < completed or report.get("sha256Verified") is not True
                        or report.get("nativeTocListed") is not True
                        or report.get("newDatabaseRestorePassed") is not True
                        or report.get("verificationDatabaseRemoved") is not True
                        or report.get("existingDatabaseOverwritten") is not False):
                    raise ValueError("Invalid identity restore record.")
                result.restoreStatus = "recorded"
                result.restoreVerifiedAt = verified.isoformat()
        except (OSError, ValueError, TypeError, OverflowError, RecursionError):
            result.restoreStatus = "invalid"
    except (OSError, ValueError, TypeError, OverflowError, RecursionError):
        result.status = "invalid"
    return result


def _file_size(path: Path) -> int:
    try:
        info = path.lstat()
        return info.st_size if stat.S_ISREG(info.st_mode) else 0
    except OSError:
        return 0


def diagnostics(request: Request, settings: Settings) -> AdminDiagnostics:
    db = request.app.state.db
    warnings = []
    ok, schema, journal = True, 0, "unknown"
    try:
        readiness(request)
        schema = db.conn.execute("PRAGMA user_version").fetchone()[0]
        journal = str(db.conn.execute("PRAGMA journal_mode").fetchone()[0])
        running = {row[0] for row in db.conn.execute("SELECT id FROM runs WHERE status = 'running'")}
        counts = {row["kind"]: row["count"] for row in db.conn.execute(
            "SELECT kind, COUNT(*) AS count FROM sessions WHERE expiresAt > ? GROUP BY kind", (time.time(),),
        )}
        admin_count = db.conn.execute(
            "SELECT COUNT(*) FROM admin_sessions WHERE expiresAt > ?", (time.time(),),
        ).fetchone()[0]
    except (sqlite3.Error, APIError):
        ok, running, counts, admin_count = False, set(), {}, 0
        warnings.append("database_unavailable")
    in_flight = request.app.state.runtime.in_flight_run_ids()
    backup = backup_diagnostic(settings.backup_directory)
    identity_backup = identity_backup_diagnostic(settings.identity_backup_directory)
    try:
        identity = identity_diagnostic(db, settings)
    except sqlite3.Error:
        identity = IdentityDiagnostic(
            backend=settings.auth_backend, configured=False, enabled=False, simulated=False,
            linkedAccounts=0, legacyAccounts=0, developerUsers=0, pendingFlows=0,
        )
        warnings.append("identity_metadata_unavailable")
    if settings.auth_backend == "oidc" and not identity.enabled:
        warnings.append("oidc_channel_disabled_or_unconfigured")
    if identity_backup.status != "recent":
        warnings.append("identity_backup_" + identity_backup.status)
    warnings.append("identity_service_not_probed")
    warnings.append("identity_backup_metadata_is_not_current_restore_verification")
    warnings.append("local_session_revocation_is_not_global_sso_revocation")
    if backup.status != "recent":
        warnings.append("backup_" + backup.status)
    if settings.allow_dev_auth:
        warnings.append("development_auth_enabled_loopback_only")
    if settings.provider == "openai" and not settings.fast_upstream_model:
        warnings.append("fast_route_not_configured")
    if settings.provider == "mock":
        warnings.append("mock_provider_no_real_model_probe")
    if not settings.safety_blocked_terms:
        warnings.append("content_filter_rules_empty")
    warnings.append("backup_freshness_is_not_restore_verification")
    database_bytes, wal_bytes = 0, 0
    if settings.database_path != ":memory:":
        path = Path(settings.database_path).expanduser()
        database_bytes, wal_bytes = _file_size(path), _file_size(Path(str(path) + "-wal"))
    return AdminDiagnostics(
        version=API_VERSION, startedAt=request.app.state.started_at,
        uptimeSeconds=max(0, int(time.monotonic() - request.app.state.started_monotonic)),
        schemaVersion=schema,
        database=DatabaseDiagnostic(ok=ok, journalMode=journal, databaseBytes=database_bytes, walBytes=wal_bytes),
        runs=RunDiagnostic(running=len(running), inFlight=len(in_flight),
                           closing=len(in_flight - running), capacity=settings.max_concurrent_runs),
        sessions=SessionDiagnostic(bearer=counts.get("bearer", 0), browser=counts.get("browser", 0), admin=admin_count),
        backup=backup, identity=identity, identityBackup=identity_backup, warnings=warnings,
    )
