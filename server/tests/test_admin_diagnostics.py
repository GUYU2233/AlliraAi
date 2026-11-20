"""Administrator-only configuration and bounded, metadata-only identity diagnostics."""

import builtins
import json
import os
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from allira.admin_db import AdminRepository
from allira.app import create_app
from allira.config import Settings
from allira.db import Database, now
from allira.operations import identity_backup_diagnostic, identity_diagnostic, route_overview
from allira.phone_identity import PhoneIdentityRepository

ORIGIN = "http://127.0.0.1:8000"
ISSUER = "http://127.0.0.1:3001/oidc"


def settings(**overrides):
    return Settings(
        _env_file=None, auth_backend="oidc", provider="mock", admin_enabled=True,
        oidc_issuer_url=ISSUER, oidc_client_id="private-client-sentinel",
        oidc_client_secret="private-secret-sentinel",
        oidc_redirect_uri=ORIGIN + "/api/v1/auth/oidc/callback",
        backup_directory="", identity_backup_directory="", **overrides,
    )


def write_json(path, value):
    path.write_text(json.dumps(value), encoding="utf-8")
    path.chmod(0o600)


def identity_snapshot(root, *, hours=0, suffix="abcdef012345", restore=False):
    root.mkdir(mode=0o700, exist_ok=True)
    stamp = datetime.now(UTC) - timedelta(hours=hours)
    directory = root / (stamp.strftime("%Y%m%dT%H%M%SZ_") + suffix)
    directory.mkdir(mode=0o700)
    dump = directory / "logto.pgdump"
    # Deliberately not a PostgreSQL dump, with a deliberately unverified digest.
    dump.write_bytes(b"private identity dump sentinel must never be opened by diagnostics")
    dump.chmod(0o600)
    manifest = {
        "formatVersion": 1, "logtoVersion": "1.46.0", "postgresMajor": 17,
        "dumpStartedAt": stamp.isoformat(), "dumpCompletedAt": stamp.isoformat(),
        "format": "pg_dump custom", "noOwner": True, "noAcl": True,
        "sha256": "0" * 64, "bytes": dump.stat().st_size,
        "containsSensitiveIdentityData": True, "remoteCopy": False,
        "businessSqliteIncluded": False, "clusterRolesIncluded": False,
        "runtimeSecretsIncluded": False,
    }
    write_json(directory / "manifest.json", manifest)
    if restore:
        write_json(directory / "restore-verification-abcdef012345.json", {
            "verifiedAt": (stamp + timedelta(seconds=1)).isoformat(),
            "sha256Verified": True, "nativeTocListed": True,
            "newDatabaseRestorePassed": True, "verificationDatabaseRemoved": True,
            "existingDatabaseOverwritten": False,
        })
    return directory, manifest


def test_identity_metadata_never_opens_dump_or_claims_current_verification(tmp_path, monkeypatch):
    root = tmp_path / "private"
    directory, manifest = identity_snapshot(root, restore=True)
    original_open, original_path_open, original_os_open = builtins.open, Path.open, os.open

    def guard_open(file, *args, **kwargs):
        assert str(file) != str(directory / "logto.pgdump")
        return original_open(file, *args, **kwargs)

    def guard_path_open(path, *args, **kwargs):
        assert path != directory / "logto.pgdump"
        return original_path_open(path, *args, **kwargs)

    def guard_os_open(path, *args, **kwargs):
        assert str(path) != str(directory / "logto.pgdump")
        return original_os_open(path, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", guard_open)
    monkeypatch.setattr(Path, "open", guard_path_open)
    monkeypatch.setattr(os, "open", guard_os_open)
    result = identity_backup_diagnostic(str(root))
    assert result.status == "recent" and result.configured is True
    assert result.databaseBytes == manifest["bytes"]
    assert result.restoreStatus == "recorded" and result.restoreVerifiedAt is not None
    assert result.checksumVerified is False
    serialized = result.model_dump_json()
    assert "private identity dump sentinel" not in serialized and str(tmp_path) not in serialized
    assert manifest["sha256"] not in serialized and "restore-verification" not in serialized


def test_identity_backup_missing_stale_and_future(tmp_path):
    assert identity_backup_diagnostic("").configured is False
    assert identity_backup_diagnostic(str(tmp_path / "missing")).status == "missing"
    root = tmp_path / "stale"
    identity_snapshot(root, hours=37)
    assert identity_backup_diagnostic(str(root)).status == "stale"
    future = tmp_path / "future"
    identity_snapshot(future, hours=-1)
    assert identity_backup_diagnostic(str(future)).status == "invalid"


@pytest.mark.parametrize("change", [
    {"formatVersion": True}, {"formatVersion": 2}, {"logtoVersion": "other"},
    {"postgresMajor": True}, {"format": "plain"}, {"noOwner": False},
    {"noAcl": False}, {"bytes": True}, {"bytes": 1}, {"sha256": "not-a-digest"},
    {"containsSensitiveIdentityData": False}, {"businessSqliteIncluded": True},
    {"clusterRolesIncluded": True}, {"runtimeSecretsIncluded": True},
    {"dumpCompletedAt": "2026-11-19T00:00:00"}, {"dumpStartedAt": "invalid"},
    {"dumpCompletedAt": "2026-11-19T00:00:00+08:00"},
])
def test_identity_backup_rejects_invalid_manifest(tmp_path, change):
    root = tmp_path / "private"
    directory, manifest = identity_snapshot(root)
    manifest.update(change)
    write_json(directory / "manifest.json", manifest)
    result = identity_backup_diagnostic(str(root))
    assert result.status == "invalid" and result.checksumVerified is False


@pytest.mark.parametrize("target", ["root", "directory", "manifest", "dump", "report"])
def test_identity_metadata_never_follows_symlinks(tmp_path, target):
    root = tmp_path / "private"
    directory, _ = identity_snapshot(root, restore=True)
    source = {
        "root": root, "directory": directory, "manifest": directory / "manifest.json",
        "dump": directory / "logto.pgdump",
        "report": directory / "restore-verification-abcdef012345.json",
    }[target]
    moved = source.with_name(source.name + ".original")
    source.rename(moved)
    source.symlink_to(moved, target_is_directory=moved.is_dir())
    result = identity_backup_diagnostic(str(root))
    assert result.restoreStatus == "invalid" if target == "report" else result.status == "invalid"
    assert result.checksumVerified is False


@pytest.mark.parametrize("target", ["root", "directory", "manifest", "dump", "report"])
def test_identity_metadata_rejects_public_permissions(tmp_path, target):
    root = tmp_path / "private"
    directory, _ = identity_snapshot(root, restore=True)
    source = {
        "root": root, "directory": directory, "manifest": directory / "manifest.json",
        "dump": directory / "logto.pgdump",
        "report": directory / "restore-verification-abcdef012345.json",
    }[target]
    source.chmod(0o755 if source.is_dir() else 0o644)
    result = identity_backup_diagnostic(str(root))
    assert result.restoreStatus == "invalid" if target == "report" else result.status == "invalid"


@pytest.mark.parametrize("target", ["manifest.json", "logto.pgdump", "restore-verification-abcdef012345.json"])
def test_identity_metadata_rejects_hardlinks(tmp_path, target):
    root = tmp_path / "private"
    directory, _ = identity_snapshot(root, restore=True)
    os.link(directory / target, tmp_path / "linked")
    result = identity_backup_diagnostic(str(root))
    assert result.restoreStatus == "invalid" if target.startswith("restore-") else result.status == "invalid"


@pytest.mark.parametrize("body", [
    '{"formatVersion":1,"formatVersion":1}', "[]", "NaN", "{", " " * 65537,
    '{"unused":' + "[" * 1100 + "0" + "]" * 1100 + "}",
])
def test_identity_manifest_read_is_bounded_strict_and_nonthrowing(tmp_path, body):
    root = tmp_path / "private"
    directory, _ = identity_snapshot(root)
    (directory / "manifest.json").write_text(body, encoding="utf-8")
    assert identity_backup_diagnostic(str(root)).status == "invalid"


@pytest.mark.parametrize("location", ["root", "latest"])
def test_identity_metadata_directory_scan_is_bounded(tmp_path, location):
    root = tmp_path / "private"
    directory, _ = identity_snapshot(root)
    target = root if location == "root" else directory
    for index in range(1001):
        (target / str(index)).touch(mode=0o600)
    result = identity_backup_diagnostic(str(root))
    assert result.status == "invalid" if location == "root" else result.restoreStatus == "invalid"


def test_identity_latest_invalid_snapshot_does_not_fall_back_to_older_success(tmp_path):
    root = tmp_path / "private"
    identity_snapshot(root, hours=1, restore=True)
    directory, _ = identity_snapshot(root)
    (directory / "manifest.json").write_text("{}")
    assert identity_backup_diagnostic(str(root)).status == "invalid"


def test_identity_report_is_optional_and_invalid_report_never_becomes_success(tmp_path):
    root = tmp_path / "private"
    directory, manifest = identity_snapshot(root)
    assert identity_backup_diagnostic(str(root)).restoreStatus == "not_reported"
    report = directory / "restore-verification-abcdef012345.json"
    write_json(report, {"verifiedAt": manifest["dumpCompletedAt"], "sha256Verified": True})
    result = identity_backup_diagnostic(str(root))
    assert result.status == "recent" and result.restoreStatus == "invalid"
    assert result.restoreVerifiedAt is None and result.checksumVerified is False


def test_identity_report_selection_uses_mtime_not_random_suffix(tmp_path):
    root = tmp_path / "private"
    directory, _ = identity_snapshot(root, restore=True)
    existing = directory / "restore-verification-abcdef012345.json"
    invalid_newer = directory / "restore-verification-000000000000.json"
    write_json(invalid_newer, {})
    os.utime(existing, ns=(1, 1))
    os.utime(invalid_newer, ns=(2, 2))
    assert identity_backup_diagnostic(str(root)).restoreStatus == "invalid"


def test_identity_counts_are_fixed_issuer_scoped_and_do_not_expose_identity(tmp_path):
    configured = settings()
    db = Database(":memory:", configured)
    try:
        with db.atomic():
            phones = PhoneIdentityRepository(db, configured)
            linked = phones.create_phone_account("+8613800000041", None, None)["userId"]
            other = phones.create_phone_account("+8613800000042", None, None)["userId"]
            phones.create_phone_account("+8613800000043", None, None)
            db.conn.execute("INSERT INTO oidc_identities VALUES (?, ?, ?, ?)",
                            (ISSUER, "private-subject-sentinel", linked, now()))
            db.conn.execute("INSERT INTO oidc_identities VALUES (?, ?, ?, ?)",
                            ("https://other.invalid/oidc", "other-subject", other, now()))
        db.create_session("developer-sentinel", "dev-diagnostic", 600)
        stamp = time.time()
        with db.atomic():
            for index, (issuer, expires) in enumerate([(ISSUER, stamp + 100), (ISSUER, stamp - 100),
                                                      ("https://other.invalid/oidc", stamp + 100)]):
                browser = "private-browser-" + str(index)
                db.conn.execute("INSERT INTO oidc_browsers VALUES (?, ?)", (browser, stamp + 100))
                db.conn.execute(
                    "INSERT INTO oidc_pending (pendingHash,browserHash,issuer,subject,phone,displayName,purpose,"
                    "startedAt,expiresAt,flowRevision) VALUES (?, ?, ?, ?, ?, ?, 'login', ?, ?, 0)",
                    ("private-pending-" + str(index), browser, issuer, "private-subject-sentinel",
                     "+8613800000041", "private-name", stamp, expires),
                )
        result = identity_diagnostic(db, configured)
        assert result.linkedAccounts == 1 and result.legacyAccounts == 2
        assert result.developerUsers == 1 and result.pendingFlows == 1
        assert result.configured and result.enabled and result.simulated is False
        assert result.serviceProbe == "not_performed" and result.administration == "separate"
        text = result.model_dump_json()
        for secret in (ISSUER, "private-subject-sentinel", "private-client-sentinel",
                       "private-secret-sentinel", "+8613800000041", "private-pending"):
            assert secret not in text
    finally:
        db.close()


def test_admin_routes_and_diagnostics_are_private_configuration_not_service_probes(tmp_path):
    configured = Settings(
        _env_file=None, auth_backend="oidc", oidc_enabled=False, admin_enabled=True,
        database_path=str(tmp_path / "private.sqlite3"), provider="openai",
        upstream_model="private-model-sentinel", upstream_api_key="private-key-sentinel",
        upstream_base_url="https://private-relay.invalid/v1", backup_directory="",
        identity_backup_directory="", oidc_issuer_url=ISSUER,
        oidc_client_id="private-client-sentinel", oidc_client_secret="private-secret-sentinel",
        oidc_redirect_uri=ORIGIN + "/api/v1/auth/oidc/callback",
    )
    with TestClient(create_app(configured), base_url=ORIGIN, client=("127.0.0.1", 10)) as client:
        assert client.get("/readyz").json() == {"status": "ready"}
        for endpoint in ("/api/v1/admin/routes", "/api/v1/admin/diagnostics"):
            assert client.get(endpoint).status_code == 401
        AdminRepository(client.app.state.db).issue_login("d" * 43, 300)
        response = client.post("/api/v1/admin/session", headers={"Origin": ORIGIN}, json={"code": "d" * 43})
        assert response.status_code == 200
        routes = client.get("/api/v1/admin/routes")
        assert routes.status_code == 200 and routes.headers["cache-control"] == "no-store"
        assert routes.json() == route_overview(configured).model_dump()
        assert routes.json()["items"] == client.get("/api/v1/models").json()["items"]
        assert routes.json()["configurationOnly"] is True
        assert routes.json()["items"][1]["configured"] is False
        diagnostic = client.get("/api/v1/admin/diagnostics")
        assert diagnostic.status_code == 200
        assert diagnostic.json()["identity"]["configured"] is True
        assert diagnostic.json()["identity"]["enabled"] is False
        assert diagnostic.json()["identity"]["serviceProbe"] == "not_performed"
        assert diagnostic.json()["identityBackup"]["checksumVerified"] is False
        assert "oidc_channel_disabled_or_unconfigured" in diagnostic.json()["warnings"]
        for response in (routes, diagnostic):
            for secret in (str(tmp_path), ISSUER, "private-model-sentinel", "private-key-sentinel",
                           "private-relay.invalid", "private-client-sentinel", "private-secret-sentinel"):
                assert secret not in response.text
        assert client.app.state.db.conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 0
        assert client.app.state.db.conn.execute("SELECT COUNT(*) FROM energy_wallets").fetchone()[0] == 0
        assert client.app.state.db.conn.execute("SELECT COUNT(*) FROM oidc_browsers").fetchone()[0] == 0
