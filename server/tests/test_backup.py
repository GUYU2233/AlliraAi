"""Isolated synthetic databases only; no application constructors or live paths."""

import hashlib
import json
import os
import sqlite3
import threading
from contextlib import closing
from pathlib import Path

import pytest

from allira import backup_cli as backup


@pytest.fixture
def database(tmp_path):
    path = tmp_path / "source.sqlite3"
    with closing(sqlite3.connect(path)) as conn:
        conn.executescript("""
            PRAGMA user_version=7;
            CREATE TABLE accounts(userId TEXT PRIMARY KEY, password TEXT, energy INTEGER);
            INSERT INTO accounts VALUES('synthetic-user','synthetic-password-hash',123);
            CREATE TABLE runs(state TEXT); INSERT INTO runs VALUES('running');
            CREATE TABLE sessions(value TEXT);
            CREATE TABLE admin_sessions(value TEXT);
            CREATE TABLE admin_access_sessions(value TEXT);
            CREATE TABLE admin_login_codes(value TEXT);
            CREATE TABLE account_recovery_codes(value TEXT);
            CREATE TABLE phone_challenges(value TEXT);
            CREATE TABLE social_pending(value TEXT);
            CREATE TABLE social_oauth_states(value TEXT);
            CREATE TABLE social_browsers(value TEXT);
            CREATE TABLE oidc_pending(value TEXT);
            CREATE TABLE oidc_states(value TEXT);
            CREATE TABLE oidc_browsers(value TEXT);
            INSERT INTO oidc_pending VALUES('synthetic-oidc-pending');
            INSERT INTO oidc_states VALUES('synthetic-oidc-state');
            INSERT INTO oidc_browsers VALUES('synthetic-oidc-browser');
            INSERT INTO phone_challenges VALUES('synthetic-phone-proof');
            INSERT INTO social_pending VALUES('synthetic-pending');
            INSERT INTO social_oauth_states VALUES('synthetic-oauth-state');
            INSERT INTO social_browsers VALUES('synthetic-social-browser');
            INSERT INTO sessions VALUES('synthetic-session');
            INSERT INTO admin_sessions VALUES('synthetic-admin');
            INSERT INTO admin_access_sessions VALUES('synthetic-access-admin');
            INSERT INTO admin_login_codes VALUES('synthetic-code');
            INSERT INTO account_recovery_codes VALUES('synthetic-recovery');
        """)
        conn.commit()
    return path


def snapshot(database):
    result = backup.create_backup(database, database.parent / "backups")
    return database.parent / "backups" / result["backupName"]


def rewrite_manifest(directory, **updates):
    path = directory / backup.MANIFEST_FILE
    data = json.loads(path.read_text())
    data.update(updates)
    path.write_text(json.dumps(data))


def test_create_verify_restore_preserves_data_revokes_credentials(database):
    before = database.read_bytes()
    directory = snapshot(database)
    result = backup.verify_backup(directory)
    assert result["schemaVersion"] == 7
    assert str(database) not in json.dumps(result)
    assert "synthetic-password" not in json.dumps(result)
    for path in (directory, directory / backup.DATABASE_FILE, directory / backup.MANIFEST_FILE):
        assert path.stat().st_mode & 0o777 == (0o700 if path.is_dir() else 0o600)
    target = database.parent / "restored.sqlite3"
    assert backup.restore_backup(directory, target)["sessionsRevoked"]
    with closing(sqlite3.connect(target)) as conn:
        assert conn.execute("SELECT * FROM accounts").fetchone() == (
            "synthetic-user", "synthetic-password-hash", 123)
        assert conn.execute("SELECT state FROM runs").fetchone() == ("running",)
        for table in backup.REVOKE_TABLES:
            if table != "browser_sessions":
                assert conn.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0] == 0
        assert conn.execute("PRAGMA integrity_check").fetchone() == ("ok",)
    assert target.stat().st_mode & 0o777 == 0o600
    assert database.read_bytes() == before


def test_wal_and_concurrent_writes(database):
    with closing(sqlite3.connect(database)) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("CREATE TABLE counters(n INTEGER)")
        conn.execute("INSERT INTO counters VALUES(1)")
        conn.commit()
        stop = threading.Event()
        errors = []

        def writer():
            try:
                with closing(sqlite3.connect(database)) as other:
                    while not stop.is_set():
                        other.execute("UPDATE counters SET n=n+1")
                        other.commit()
            except sqlite3.Error as exc:
                errors.append(type(exc).__name__)

        thread = threading.Thread(target=writer)
        thread.start()
        try:
            directory = snapshot(database)
        finally:
            stop.set()
            thread.join(timeout=10)
        assert not thread.is_alive()
        assert not errors
        assert backup.verify_backup(directory)["schemaVersion"] == 7
        with closing(sqlite3.connect(directory / backup.DATABASE_FILE)) as restored:
            assert restored.execute("SELECT n FROM counters").fetchone()[0] >= 1


def test_readonly_source(database):
    database.chmod(0o400)
    before = database.read_bytes()
    snapshot(database)
    assert database.read_bytes() == before
    assert database.stat().st_mode & 0o777 == 0o400


@pytest.mark.parametrize("value", [0, 101, -1, True, "7"])
def test_bad_retention(database, value):
    with pytest.raises(backup.BackupError, match="invalid_retention"):
        backup.create_backup(database, database.parent / "backups", value)


def test_permissions_and_existing_lock(database):
    output = database.parent / "backups"
    output.mkdir(mode=0o755)
    with pytest.raises(backup.BackupError, match="insecure_directory"):
        backup.create_backup(database, output)
    output.chmod(0o700)
    lock = output / backup.LOCK_FILE
    lock.write_text('{"pid":99999999}')
    with pytest.raises(backup.BackupError, match="backup_locked"):
        backup.create_backup(database, output)
    assert lock.exists()


def test_symlinks_and_relative_paths(database):
    alias = database.parent / "alias"
    alias.symlink_to(database)
    with pytest.raises(backup.BackupError, match="symlink_rejected"):
        backup.create_backup(alias, database.parent / "backups")
    alias.unlink()
    alias.symlink_to(database.parent, target_is_directory=True)
    with pytest.raises(backup.BackupError, match="symlink_rejected"):
        backup.create_backup(alias / database.name, database.parent / "backups")
    with pytest.raises(backup.BackupError, match="absolute_path_required"):
        backup.create_backup(Path("source.sqlite3"), database.parent / "backups")
    with pytest.raises(backup.BackupError):
        backup.create_backup(database.parent / "missing", database.parent / "backups")


@pytest.mark.parametrize("field,value,code", [
    ("version", 99, "unsupported_manifest_version"),
    ("databaseFile", "../source.sqlite3", "invalid_manifest"),
    ("databaseFile", "/etc/passwd", "invalid_manifest"),
    ("schemaVersion", 8, "schema_mismatch"),
    ("size", 1, "checksum_mismatch"),
    ("sha256", "0" * 64, "checksum_mismatch"),
    ("createdAt", "nonsense", "invalid_manifest"),
    ("extra", "value", "invalid_manifest"),
])
def test_manifest_rejection(database, field, value, code):
    directory = snapshot(database)
    rewrite_manifest(directory, **{field: value})
    with pytest.raises(backup.BackupError, match=code):
        backup.verify_backup(directory)


def test_manifest_torn_duplicate_oversized(database):
    directory = snapshot(database)
    path = directory / backup.MANIFEST_FILE
    original = path.read_text()
    for value, code in [("{", "invalid_manifest"),
                        ('{"version":1,"version":1}', "invalid_manifest"),
                        (" " * 65537, "manifest_too_large")]:
        path.write_text(value)
        with pytest.raises(backup.BackupError, match=code):
            backup.verify_backup(directory)
    path.write_text(original)
    path.chmod(0o644)
    with pytest.raises(backup.BackupError, match="insecure_file"):
        backup.verify_backup(directory)


def test_corruption_even_if_hash_recomputed(database):
    directory = snapshot(database)
    path = directory / backup.DATABASE_FILE
    path.write_bytes(b"not a SQLite database" * 100)
    rewrite_manifest(directory, size=path.stat().st_size,
                     sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    with pytest.raises(backup.BackupError):
        backup.verify_backup(directory)


def test_foreign_key_violation(database):
    with closing(sqlite3.connect(database)) as conn:
        conn.executescript("""CREATE TABLE parent(id INTEGER PRIMARY KEY);
            CREATE TABLE child(parentId INTEGER REFERENCES parent(id));
            INSERT INTO child VALUES(123);""")
    with pytest.raises(backup.BackupError, match="sqlite_foreign_key_failed"):
        snapshot(database)


def test_verify_rejects_symlinked_members(database):
    directory = snapshot(database)
    path = directory / backup.DATABASE_FILE
    path.unlink()
    path.symlink_to(database)
    with pytest.raises(backup.BackupError, match="symlink_rejected"):
        backup.verify_backup(directory)


def test_target_exists_and_public_parent(database):
    directory = snapshot(database)
    before = database.read_bytes()
    with pytest.raises(backup.BackupError, match="target_exists"):
        backup.restore_backup(directory, database)
    assert before == database.read_bytes()
    public = database.parent / "public"
    public.mkdir(mode=0o755)
    with pytest.raises(backup.BackupError, match="insecure_directory"):
        backup.restore_backup(directory, public / "new.sqlite3")


def test_target_race_never_overwrites(database, monkeypatch):
    directory = snapshot(database)
    target = database.parent / "new.sqlite3"
    original = os.link

    def racing_link(source, destination, **kwargs):
        target.write_bytes(b"concurrent-owner")
        return original(source, destination, **kwargs)

    monkeypatch.setattr(os, "link", racing_link)
    with pytest.raises(backup.BackupError, match="target_exists"):
        backup.restore_backup(directory, target)
    assert target.read_bytes() == b"concurrent-owner"
    assert not list(database.parent.glob(".restore-*"))


def test_final_directory_never_overwritten(database, monkeypatch):
    directory = snapshot(database)
    occupied = database.parent / "occupied"
    occupied.mkdir()
    marker = occupied / "marker"
    marker.write_text("keep")
    with pytest.raises(backup.BackupError, match="backup_name_exists"):
        backup._publish_directory(directory, occupied)
    assert marker.read_text() == "keep"
    assert backup.verify_backup(directory)


def test_retention_only_validated_own_directories(database):
    first = snapshot(database)
    output = first.parent
    unrelated = output / "manual-not-a-backup"
    unrelated.mkdir()
    corrupt = output / ("allira-20000101T000000Z-" + "a" * 16)
    corrupt.mkdir(mode=0o700)
    (corrupt / "valuable").write_text("keep")
    alias = output / ("allira-20000101T000000Z-" + "b" * 16)
    alias.symlink_to(database.parent)
    result = backup.create_backup(database, output, retain=1)
    assert not first.exists()
    assert (output / result["backupName"]).is_dir()
    assert result["retentionWarning"] is True
    assert (corrupt / "valuable").read_text() == "keep"
    assert unrelated.exists() and alias.is_symlink() and database.exists()


def test_retention_failure_does_not_destroy_new_backup(database, monkeypatch):
    snapshot(database)

    def fail(*args):
        raise OSError("sensitive-path")

    monkeypatch.setattr(backup, "_remove_snapshot", fail)
    result = backup.create_backup(database, database.parent / "backups", retain=1)
    assert result["retentionWarning"]
    assert backup.verify_backup(database.parent / "backups" / result["backupName"])


def test_cli_sanitizes_errors(database, capsys):
    assert backup.main(["verify", "--backup-dir", str(database.parent / "secret-path")]) == 1
    output = capsys.readouterr()
    assert json.loads(output.out) == {"status": "error", "code": "path_missing"}
    assert not output.err
    assert backup.main(["create", "--retain", "private-value"]) == 1
    output = capsys.readouterr()
    assert json.loads(output.out)["code"] == "invalid_arguments"
    assert "private-value" not in output.out + output.err


def test_source_sidecar_symlink_rejected(database):
    sidecar = Path(str(database) + "-wal")
    sidecar.symlink_to(database)
    with pytest.raises(backup.BackupError, match="symlink_rejected"):
        snapshot(database)


def test_verify_does_not_write_files(database):
    directory = snapshot(database)
    before = {item.name: (item.read_bytes(), item.stat().st_mtime_ns)
              for item in directory.iterdir()}
    backup.verify_backup(directory)
    after = {item.name: (item.read_bytes(), item.stat().st_mtime_ns)
             for item in directory.iterdir()}
    assert before == after


def test_active_lock_rejects_concurrent_create(database):
    output = database.parent / "backups"
    output.mkdir(mode=0o700)
    with backup._lock(output), pytest.raises(backup.BackupError, match="backup_locked"):
        backup.create_backup(database, output)
    assert not (output / backup.LOCK_FILE).exists()


def test_restore_rejects_symlink_target_and_parent(database):
    directory = snapshot(database)
    target = database.parent / "target.sqlite3"
    target.symlink_to(database)
    with pytest.raises(backup.BackupError, match="symlink_rejected"):
        backup.restore_backup(directory, target)
    alias = database.parent / "alias"
    alias.symlink_to(database.parent)
    with pytest.raises(backup.BackupError, match="symlink_rejected"):
        backup.restore_backup(directory, alias / "new.sqlite3")


@pytest.mark.parametrize("suffix", ["-wal", "-shm", "-journal"])
@pytest.mark.parametrize("symlink", [False, True])
def test_restore_refuses_existing_target_sidecars(database, suffix, symlink):
    directory = snapshot(database)
    target = database.parent / "new.sqlite3"
    sidecar = Path(str(target) + suffix)
    if symlink:
        sidecar.symlink_to(database.parent / "missing")
    else:
        sidecar.write_bytes(b"preexisting-sidecar")
    with pytest.raises(backup.BackupError, match="target_sidecar_exists"):
        backup.restore_backup(directory, target)
    assert not target.exists()
    if symlink:
        assert sidecar.is_symlink()
    else:
        assert sidecar.read_bytes() == b"preexisting-sidecar"
    assert not list(database.parent.glob(".restore-*"))


@pytest.mark.parametrize("suffix", ["-wal", "-shm", "-journal"])
def test_restore_rechecks_sidecars_before_publish(database, monkeypatch, suffix):
    directory = snapshot(database)
    target = database.parent / "new.sqlite3"
    sidecar = Path(str(target) + suffix)
    original = backup._sync_file

    def race(path):
        original(path)
        sidecar.write_bytes(b"arrived-during-restore")

    monkeypatch.setattr(backup, "_sync_file", race)
    with pytest.raises(backup.BackupError, match="target_sidecar_exists"):
        backup.restore_backup(directory, target)
    assert not target.exists()
    assert sidecar.read_bytes() == b"arrived-during-restore"
    assert not list(database.parent.glob(".restore-*"))


def test_restore_rejects_real_orphan_wal_that_reintroduces_sessions(database):
    directory = snapshot(database)
    orphan = database.parent / "orphan.sqlite3"
    backup.restore_backup(directory, orphan)
    restored_bytes = orphan.read_bytes()
    # Produce a genuine committed WAL against the restored schema, then prove that
    # merely installing the desired main file does not prevent stale WAL replay.
    # Keep its writer open so SQLite does not checkpoint/unlink it on close.
    with closing(sqlite3.connect(orphan)) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA wal_autocheckpoint=0")
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.execute("INSERT INTO sessions VALUES('orphan-secret')")
        conn.execute("UPDATE accounts SET energy=999")
        conn.commit()
        target = database.parent / "restore.sqlite3"
        sidecar = Path(str(target) + "-wal")
        wal_bytes = Path(str(orphan) + "-wal").read_bytes()
        assert len(wal_bytes) > 32
        unsafe = database.parent / "unsafe-demo.sqlite3"
        unsafe.write_bytes(restored_bytes)
        Path(str(unsafe) + "-wal").write_bytes(wal_bytes)
        with closing(sqlite3.connect(unsafe)) as replayed:
            assert replayed.execute("SELECT count(*) FROM sessions").fetchone()[0] == 1
            assert replayed.execute("SELECT energy FROM accounts").fetchone()[0] == 999
        sidecar.write_bytes(wal_bytes)
        with pytest.raises(backup.BackupError, match="target_sidecar_exists"):
            backup.restore_backup(directory, target)
        assert not target.exists()
        assert sidecar.read_bytes() == wal_bytes
        assert backup.verify_backup(directory)


def test_backup_tree_cannot_be_its_own_retention_source(database):
    directory = snapshot(database)
    source = directory / backup.DATABASE_FILE
    before = source.read_bytes()
    with pytest.raises(backup.BackupError, match="invalid_output_directory"):
        backup.create_backup(source, directory.parent, retain=1)
    assert source.read_bytes() == before
    assert backup.verify_backup(directory)
    assert not (directory.parent / backup.LOCK_FILE).exists()
    assert len(list(directory.parent.glob("allira-*"))) == 1


def test_cli_success(database, capsys):
    assert backup.main(["create", "--database", str(database),
                        "--output-dir", str(database.parent / "backups")]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "ok"
    assert "synthetic" not in json.dumps(result)
    assert str(database.parent) not in json.dumps(result)
