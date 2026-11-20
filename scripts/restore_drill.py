"""Same-host, offline restore drill; never replace the serving database or call a model.

Run on hklite-lv4 as allira, passing an already verified backup directory and a new
private drill directory. Sensitive data remains on that server. Do not download it.
"""

import argparse
import json
import os
import sqlite3
import stat
from pathlib import Path

from allira.backup_cli import BackupError, restore_backup, verify_backup
from allira.config import Settings
from allira.db import Database


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backup-dir", required=True, type=Path)
    parser.add_argument("--drill-dir", required=True, type=Path)
    args = parser.parse_args()
    try:
        # Both paths must be on the protected server, with no symlink ancestors.
        for path in (args.backup_dir, args.drill_dir):
            if not path.is_absolute() or ".." in path.parts:
                raise BackupError("absolute_path_required")
            for component in path.parents:
                if stat.S_ISLNK(component.lstat().st_mode):
                    raise BackupError("symlink_rejected")
        parent = args.drill_dir.parent
        info = parent.stat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o077:
            raise BackupError("insecure_directory")
        # Exclusive directory creation prevents restoring to an existing live path.
        args.drill_dir.mkdir(mode=0o700)
        verified = verify_backup(args.backup_dir)
        restored = restore_backup(args.backup_dir, args.drill_dir / "restored.sqlite3")
        settings = Settings(_env_file=None, database_path=str(args.drill_dir / "restored.sqlite3"),
                            provider="mock", fast_upstream_model="", mock_chunk_delay_seconds=0,
                            sms_provider="disabled", social_mode="disabled")
        database = Database(settings.database_path, settings)
        try:
            credentials = sum(database.conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
                              for table in ("sessions", "admin_access_sessions", "admin_sessions", "admin_login_codes",
                                            "account_recovery_codes", "phone_challenges", "social_browsers",
                                            "social_oauth_states", "social_pending", "oidc_browsers",
                                            "oidc_states", "oidc_pending"))
            if credentials:
                raise BackupError("credential_revocation_failed")
            # Apply exactly the startup recovery policy to the isolated copy only.
            database.recover_interrupted_runs()
            if database.conn.execute("SELECT COUNT(*) FROM runs WHERE status = 'running'").fetchone()[0]:
                raise BackupError("interrupted_runs_remain")
            if database.conn.execute("SELECT COUNT(*) FROM run_usage WHERE state = 'reserved'").fetchone()[0]:
                raise BackupError("reserved_energy_remains")
            if database.conn.execute("PRAGMA integrity_check").fetchall()[0][0] != "ok":
                raise BackupError("sqlite_integrity_failed")
            if database.conn.execute("PRAGMA foreign_key_check").fetchone() is not None:
                raise BackupError("sqlite_foreign_key_failed")
            schema = database.conn.execute("PRAGMA user_version").fetchone()[0]
        finally:
            database.close()
        print(json.dumps({"status": "ok", "schemaVersion": schema,
                          "checksumVerified": bool(verified["sha256"]),
                          "sessionsRevoked": restored["sessionsRevoked"],
                          "interruptedRunsRecovered": True, "scope": "same_host_offline_copy"}))
        return 0
    except (BackupError, OSError, ValueError, KeyError, sqlite3.Error):
        # Never expose private paths, chat records or raw SQLite errors.
        print(json.dumps({"status": "error", "code": "restore_drill_failed"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
