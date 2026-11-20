"""Public readiness is small; administrator diagnostics contain no paths or secrets."""

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from allira.admin_db import AdminRepository
from allira.app import create_app
from allira.config import Settings
from allira.operations import backup_diagnostic

ORIGIN = "http://127.0.0.1:8000"


def snapshot(root, *, hours=0):
    stamp = datetime.now(UTC) - timedelta(hours=hours)
    directory = root / (stamp.strftime("allira-%Y%m%dT%H%M%SZ-") + "abcdef0123456789")
    directory.mkdir(mode=0o700, parents=True)
    path = directory / "snapshot.sqlite3"
    path.write_bytes(b"diagnostic fixture is not a verified SQLite snapshot")
    path.chmod(0o600)
    manifest = {
        "version": 1, "createdAt": stamp.strftime("%Y-%m-%dT%H:%M:%SZ"), "source": "allira",
        "databaseFile": "snapshot.sqlite3", "schemaVersion": 4,
        "sha256": "0" * 64, "size": path.stat().st_size,
        "modeSummary": "directory=0700,database=0600,manifest=0600",
    }
    manifest_path = directory / "manifest.json"
    manifest_path.write_text(json.dumps(manifest))
    manifest_path.chmod(0o600)
    return directory, manifest


def test_public_readiness_and_private_diagnostics(tmp_path):
    private = tmp_path / "private-backups"
    private.mkdir(mode=0o700)
    snapshot(private)
    settings = Settings(_env_file=None, database_path=str(tmp_path / "private-database.sqlite3"),
                        admin_enabled=True, allow_dev_auth=True, backup_directory=str(private))
    with TestClient(create_app(settings), base_url=ORIGIN, client=("127.0.0.1", 10)) as client:
        assert client.get("/readyz").json() == {"status": "ready"}
        endpoint = "/api/v1/admin/diagnostics"
        assert client.get(endpoint).status_code == 401
        client.app.state.db.create_session("ordinary-token", "not-an-administrator", 600)
        assert client.get(endpoint, headers={"Authorization": "Bearer ordinary-token"}).status_code == 401
        AdminRepository(client.app.state.db).issue_login("b" * 43, 300)
        assert client.post("/api/v1/admin/session", headers={"Origin": ORIGIN},
                           json={"code": "b" * 43}).status_code == 200
        response = client.get(endpoint)
        assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
        body = response.json()
        assert body["version"] == "0.8.0" and body["schemaVersion"] == 8
        assert body["uptimeSeconds"] >= 0 and body["database"]["ok"] is True
        assert body["database"]["databaseBytes"] > 0
        assert body["runs"] == {"running": 0, "inFlight": 0, "closing": 0, "capacity": 8}
        assert body["sessions"] == {"bearer": 1, "browser": 0, "admin": 1}
        assert body["backup"]["status"] == "recent"
        assert "backup_freshness_is_not_restore_verification" in body["warnings"]
        for excluded in [str(tmp_path), settings.database_path, "ordinary-token", "private-database", "private-backups"]:
            assert excluded not in response.text
        original = client.app.state.db
        client.app.state.db = SimpleNamespace(conn=None)
        try:
            response = client.get("/readyz")
            assert response.status_code == 503 and response.json()["error"]["code"] == "not_ready"
            assert str(tmp_path) not in response.text and "conn" not in response.text
        finally:
            client.app.state.db = original


def test_backup_freshness_never_claims_checksum_or_restore_validation(tmp_path):
    root = tmp_path / "backups"
    root.mkdir(mode=0o700)
    directory, manifest = snapshot(root)
    result = backup_diagnostic(str(root))
    assert result.status == "recent" and result.configured and result.latestAt == manifest["createdAt"]
    assert 0 <= result.ageSeconds < 5
    # The intentionally incorrect SHA above is accepted as a freshness hint, not verified.
    (directory / "snapshot.sqlite3").write_bytes(b"short")
    assert backup_diagnostic(str(root)).status == "invalid"


def test_missing_stale_future_corrupt_and_insecure_backup_states(tmp_path):
    assert backup_diagnostic("").configured is False
    assert backup_diagnostic(str(tmp_path / "missing")).status == "missing"
    root = tmp_path / "stale"
    root.mkdir(mode=0o700)
    snapshot(root, hours=37)
    assert backup_diagnostic(str(root)).status == "stale"
    root.chmod(0o755)
    assert backup_diagnostic(str(root)).status == "invalid"
    root.chmod(0o700)
    future = tmp_path / "future"
    future.mkdir(mode=0o700)
    snapshot(future, hours=-1)
    assert backup_diagnostic(str(future)).status == "invalid"
    invalid = tmp_path / "invalid"
    invalid.mkdir(mode=0o700)
    directory, _ = snapshot(invalid)
    (directory / "manifest.json").write_text('{"version":1,"version":1}')
    assert backup_diagnostic(str(invalid)).status == "invalid"


@pytest.mark.parametrize("target", ["root", "directory", "manifest", "database"])
def test_diagnostic_never_follows_symlinks(tmp_path, target):
    root = tmp_path / "private"
    root.mkdir(mode=0o700)
    directory, _ = snapshot(root)
    source = {"root": root, "directory": directory,
              "manifest": directory / "manifest.json", "database": directory / "snapshot.sqlite3"}[target]
    moved = source.with_name(source.name + ".original")
    source.rename(moved)
    source.symlink_to(moved, target_is_directory=moved.is_dir())
    assert backup_diagnostic(str(root)).status == "invalid"


def test_directory_scan_is_bounded(tmp_path):
    root = tmp_path / "too-many"
    root.mkdir(mode=0o700)
    for index in range(1001):
        (root / str(index)).touch()
    assert backup_diagnostic(str(root)).status == "invalid"
