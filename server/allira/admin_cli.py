"""Administrator maintenance; deployed Access authentication never issues login links.

Link issuance exists only for explicitly selected loopback compatibility fixtures.
Use the API environment file. Only its Unix account (or root) should have DB access.
"""

import argparse
import json
import secrets
import sys
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

from .admin import is_loopback
from .admin_db import AdminRepository
from .config import Settings
from .db import Database


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="AlliraAi loopback development administration")
    parser.add_argument("--env-file", default=".env", help="API environment file (not copied or printed)")
    commands = parser.add_subparsers(dest="command", required=True)
    issue = commands.add_parser("issue-login", help="Issue one single-use link; invalidates older unused links")
    issue.add_argument("--base-url", default="http://127.0.0.1:8000", help="Your local SSH tunnel origin")
    issue.add_argument("--json", action="store_true", help="Machine output; contains a sensitive one-use link")
    commands.add_parser("revoke-sessions", help="Invalidate all administrator cookies and unused links")
    args = parser.parse_args(argv)
    try:
        settings = Settings(_env_file=args.env_file)
        if not settings.admin_enabled or settings.environment != "development":
            parser.error("Development administration must be enabled in the API environment.")
        path = Path(settings.database_path).expanduser().resolve()
        if settings.database_path == ":memory:" or not path.is_file():
            parser.error("The existing API database is required; start the API first.")
        if args.command == "issue-login":
            if settings.admin_auth_backend != "link":
                print("Administrator login links are disabled; use Cloudflare Access.", file=sys.stderr)
                return 1
            base = urlsplit(args.base_url)
            if (
                base.scheme not in {"http", "https"} or not base.hostname
                or not is_loopback(base.hostname) or base.username is not None
                or base.password is not None or base.path not in {"", "/"}
                or base.query or base.fragment or (base.port is not None and base.port < 1)
            ):
                parser.error("base-url must be an HTTP(S) loopback origin without credentials or a path.")
        db = Database(str(path), settings)
        try:
            repository = AdminRepository(db)
            if args.command == "revoke-sessions":
                count = repository.revoke_admin_sessions()
                print(json.dumps({"ok": True, "revoked": count}))
                return 0
            code = secrets.token_urlsafe(32)
            expires = repository.issue_login(code, settings.admin_login_ttl_seconds)
            url = f"{args.base_url.rstrip('/')}/admin/#code={code}"
            stamp = datetime.fromtimestamp(expires, UTC).isoformat().replace("+00:00", "Z")
            if args.json:
                print(json.dumps({"url": url, "expiresAt": stamp}))
            else:
                print("一次性管理登录链接（不要转发、录屏或写入日志）：")
                print(url)
                print(f"到期时间：{stamp}。打开后点击登录；新签发会使此前未使用链接失效。")
            return 0
        finally:
            db.close()
    except (OSError, ValueError):
        # Configuration errors can contain server credentials. Do not echo exception strings.
        print("Cannot issue administrator access; check the API environment and database permissions.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
