"""Exercise the standalone drill using isolated synthetic application databases only."""

import hashlib
import importlib.util
import json
import sqlite3
import sys
from contextlib import closing
from pathlib import Path
from uuid import uuid4

import pytest

from allira.backup_cli import create_backup
from allira.config import Settings
from allira.db import Database
from allira.schemas import ConversationInput, RunInput


@pytest.fixture
def drill_module():
    path = Path(__file__).resolve().parents[2] / "scripts" / "restore_drill.py"
    spec = importlib.util.spec_from_file_location("allira_restore_drill_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def snapshot(tmp_path):
    source = tmp_path / "synthetic-source.sqlite3"
    settings = Settings(_env_file=None, database_path=str(source), provider="mock")
    db = Database(str(source), settings)
    db.create_session("synthetic-private-token", "synthetic-user", 3600)
    chat = db.create_conversation("synthetic-user", ConversationInput(title="synthetic private title"))
    run, _ = db.create_run("synthetic-user", chat.id, RunInput(
        clientMessageId=uuid4(), content="synthetic private input",
    ), 120000)
    db.append_delta(run.id, "synthetic partial output")
    db.close()
    # Reproduce the former three-column session schema, not just its version number.
    with closing(sqlite3.connect(source)) as conn:
        conn.executescript("""
            ALTER TABLE sessions RENAME TO old_sessions;
            CREATE TABLE sessions(tokenHash TEXT PRIMARY KEY, userId TEXT NOT NULL, expiresAt REAL NOT NULL);
            INSERT INTO sessions SELECT tokenHash, userId, expiresAt FROM old_sessions;
            DROP TABLE old_sessions;
            DROP TABLE account_recovery_codes;
            DROP TABLE account_recovery_state;
            DROP TABLE conversation_forks;
            DROP TABLE conversation_tool_state;
            PRAGMA user_version=3;
        """)
        conn.commit()
    output = tmp_path / "backups"
    result = create_backup(source, output)
    parent = tmp_path / "private-drills"
    parent.mkdir(mode=0o700)
    return source, output / result["backupName"], parent / "new-drill", run.id


def invoke(drill_module, monkeypatch, capsys, backup, target):
    monkeypatch.setattr(sys, "argv", ["restore_drill.py", "--backup-dir", str(backup),
                                      "--drill-dir", str(target)])
    status = drill_module.main()
    captured = capsys.readouterr()
    assert captured.err == ""
    body = json.loads(captured.out)
    for private in [str(backup), str(target), "synthetic-private-token", "synthetic private"]:
        assert private not in captured.out
    return status, body


def test_restore_drill_upgrades_legacy_schema_and_refunds_copy_only(
    drill_module, snapshot, monkeypatch, capsys,
):
    source, backup, target, run_id = snapshot
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    status, body = invoke(drill_module, monkeypatch, capsys, backup, target)
    assert status == 0 and body == {
        "status": "ok", "schemaVersion": 8, "checksumVerified": True,
        "sessionsRevoked": True, "interruptedRunsRecovered": True,
        "scope": "same_host_offline_copy",
    }
    assert target.stat().st_mode & 0o777 == 0o700
    with closing(sqlite3.connect(target / "restored.sqlite3")) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 8
        assert len(conn.execute("PRAGMA table_info(sessions)").fetchall()) == 8
        for table in ("sessions", "admin_sessions", "admin_access_sessions",
                      "admin_login_codes", "account_recovery_codes"):
            assert conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0] == 0
        assert conn.execute("SELECT status,errorCode FROM runs WHERE id=?", (run_id,)).fetchone() == (
            "failed", "server_restarted",
        )
        assert conn.execute("SELECT state FROM run_usage WHERE runId=?", (run_id,)).fetchone()[0] == "refunded"
        assert conn.execute("SELECT balance FROM energy_wallets").fetchone()[0] == 100
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before
    with closing(sqlite3.connect(source)) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 3
        assert conn.execute("SELECT status FROM runs WHERE id=?", (run_id,)).fetchone()[0] == "running"
        assert conn.execute("SELECT state FROM run_usage WHERE runId=?", (run_id,)).fetchone()[0] == "reserved"
        assert conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 1


def test_existing_drill_directory_is_never_reused(drill_module, snapshot, monkeypatch, capsys):
    _, backup, target, _ = snapshot
    target.mkdir(mode=0o700)
    marker = target / "do-not-overwrite.txt"
    marker.write_text("private sentinel")
    status, body = invoke(drill_module, monkeypatch, capsys, backup, target)
    assert status == 1 and body == {"status": "error", "code": "restore_drill_failed"}
    assert marker.read_text() == "private sentinel"
    assert not (target / "restored.sqlite3").exists()


def test_corrupt_backup_failure_is_sanitized(drill_module, snapshot, monkeypatch, capsys):
    _, backup, target, _ = snapshot
    (backup / "manifest.json").write_text("private invalid manifest")
    status, body = invoke(drill_module, monkeypatch, capsys, backup, target)
    assert status == 1 and body == {"status": "error", "code": "restore_drill_failed"}
    assert not (target / "restored.sqlite3").exists()


@pytest.mark.parametrize("kind", ["relative", "public-parent", "symlink-parent"])
def test_unsafe_drill_location_is_refused(
    drill_module, snapshot, monkeypatch, capsys, tmp_path, kind,
):
    _, backup, target, _ = snapshot
    if kind == "relative":
        target = Path("relative-drill")
    elif kind == "public-parent":
        target.parent.chmod(0o755)
    else:
        link = tmp_path / "linked-parent"
        link.symlink_to(target.parent, target_is_directory=True)
        target = link / "new-drill"
    status, body = invoke(drill_module, monkeypatch, capsys, backup, target)
    assert status == 1 and body == {"status": "error", "code": "restore_drill_failed"}
    assert not target.exists()
