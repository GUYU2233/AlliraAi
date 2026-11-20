"""Opt-in registered-account sessions, isolated from Bearer and administrator identities.

Cookies are host-only, HttpOnly and same-site. HTTP is accepted only on a verified
loopback peer/Host for this internal deployment; other hosts require HTTPS. Every
cookie read rejects cross-site contexts and every write also checks exact Origin/CSRF.
"""

import hashlib
import hmac
import secrets
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Annotated
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Request, Response

from .accounts import AccountUser, _account_user
from .admin import is_loopback, require_loopback, require_origin
from .config import Settings
from .db import APIError, Database
from .schemas import DTO, OK

COOKIE_NAME = "allira_user"
COOKIE_PATH = "/api/v1"


class BrowserSession(DTO):
    user: AccountUser
    csrfToken: str
    expiresAt: str


def csrf_token(token: str) -> str:
    return hmac.new(token.encode(), b"allira-account-csrf-v1", hashlib.sha256).hexdigest()


def require_browser_context(request: Request) -> None:
    host = request.headers.get("host", "")
    try:
        parsed = urlsplit(f"https://{host}")
        valid = (
            bool(host) and host == host.strip() and not host.endswith(":")
            and parsed.hostname is not None and parsed.username is None and parsed.password is None
            and not parsed.path and not parsed.query and not parsed.fragment
            and (parsed.port is None or parsed.port > 0)
        )
    except ValueError:
        valid = False
    if not valid:
        raise APIError(403, "origin_forbidden", "A valid browser origin is required.")
    if is_loopback(parsed.hostname or ""):
        require_loopback(request)
    elif request.url.scheme != "https":
        raise APIError(403, "https_required", "Browser sessions require HTTPS outside loopback.")
    if request.headers.get("sec-fetch-site") not in {None, "same-origin", "none"}:
        raise APIError(403, "origin_forbidden", "Cross-origin browser sessions are not allowed.")
    origin = request.headers.get("origin")
    if origin is not None:
        require_origin(request)


def require_browser_write(request: Request, token: str) -> None:
    require_origin(request)
    submitted = request.headers.get("x-csrf-token", "")
    if not hmac.compare_digest(submitted.encode(), csrf_token(token).encode()):
        raise APIError(403, "csrf_forbidden", "A valid account CSRF token is required.")


def authenticate_browser(request: Request) -> str:
    token = request.cookies.get(COOKIE_NAME, "")
    if not token:
        raise APIError(401, "unauthorized", "A valid session is required.")
    require_browser_context(request)
    db: Database = request.app.state.db
    user = db.authenticate(token, kind="browser")
    if not db.conn.execute("SELECT 1 FROM accounts WHERE userId = ?", (user,)).fetchone():
        raise APIError(401, "unauthorized", "A registered account browser session is required.")
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        require_browser_write(request, token)
    request.state.session_token = token
    request.state.auth_kind = "browser"
    return user


def clear_cookie(response: Response, *, secure: bool = False) -> None:
    response.delete_cookie(
        COOKIE_NAME, path=COOKIE_PATH, httponly=True, secure=secure, samesite="strict",
    )


def session_view(db: Database, token: str, user: str) -> BrowserSession:
    account = db.conn.execute("SELECT * FROM accounts WHERE userId = ?", (user,)).fetchone()
    row = db.conn.execute(
        "SELECT expiresAt FROM sessions WHERE tokenHash = ? AND userId = ? AND kind = 'browser'",
        (hashlib.sha256(token.encode()).hexdigest(), user),
    ).fetchone()
    if account is None or row is None:
        raise APIError(401, "unauthorized", "A registered account browser session is required.")
    return BrowserSession(
        user=_account_user(account), csrfToken=csrf_token(token),
        expiresAt=datetime.fromtimestamp(row["expiresAt"], UTC).isoformat().replace("+00:00", "Z"),
    )


def browser_sessions_router(settings: Settings, current_user: Callable[..., str]) -> APIRouter:
    router = APIRouter(prefix="/api/v1/auth/browser", tags=["browser sessions"])
    User = Annotated[str, Depends(current_user)]

    @router.post("/session", response_model=BrowserSession)
    async def exchange(request: Request, response: Response, user: User):
        require_browser_context(request)
        require_origin(request)
        if request.state.auth_kind != "bearer":
            raise APIError(401, "bearer_required", "Exchange requires a fresh account Bearer session.")
        db: Database = request.app.state.db
        if not db.conn.execute("SELECT 1 FROM accounts WHERE userId = ?", (user,)).fetchone():
            raise APIError(403, "account_required", "A registered account is required.")
        token = secrets.token_urlsafe(32)
        with db.atomic():
            # Explicit login rotates this browser's previous cookie and consumes the
            # short-lived exchange token. Other devices keep their independent sessions.
            old = request.cookies.get(COOKIE_NAME, "")
            if old and len(old) <= 200:
                db.conn.execute(
                    "DELETE FROM sessions WHERE tokenHash = ? AND kind = 'browser'",
                    (hashlib.sha256(old.encode()).hexdigest(),),
                )
            db.create_session(
                token, user, settings.browser_session_ttl_seconds,
                kind="browser", label="Browser",
            )
            db.revoke_session(request.state.session_token)
            result = session_view(db, token, user)
        response.set_cookie(
            COOKIE_NAME, token, path=COOKIE_PATH, max_age=settings.browser_session_ttl_seconds,
            httponly=True, samesite="strict", secure=request.url.scheme == "https",
        )
        return result

    @router.get("/session", response_model=BrowserSession)
    async def get_session(request: Request):
        user = authenticate_browser(request)
        return session_view(request.app.state.db, request.state.session_token, user)

    @router.post("/logout", response_model=OK)
    async def logout(request: Request, response: Response):
        authenticate_browser(request)
        request.app.state.db.revoke_session(request.state.session_token)
        clear_cookie(response, secure=request.url.scheme == "https")
        return OK()

    return router
