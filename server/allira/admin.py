"""Administrator API: verified Access identity plus an independent Cookie/CSRF binding."""

import hashlib
import hmac
import ipaddress
import re
import secrets
from datetime import UTC, datetime
from typing import Annotated
from urllib.parse import urlsplit
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response

from .access_identity import verified_access
from .admin_db import AdminRepository
from .config import Settings
from .db import APIError
from .economy import CdkIssue, CdkSummary, EnergyRepository, IssuedCdk, UsageOverview
from .operations import AdminDiagnostics, AdminRouteOverview, diagnostics, route_overview
from .schemas import (
    OK,
    AdminAccessIdentity,
    AdminAudit,
    AdminConversation,
    AdminDeviceSession,
    AdminIdentityFilter,
    AdminLogin,
    AdminOverview,
    AdminRun,
    AdminSession,
    AdminUser,
    AdminUserQuery,
    Message,
    Page,
    RevokedSessions,
    RunStatus,
)

COOKIE_NAME = "allira_admin"
COOKIE_PATH = "/api/v1/admin"


def is_loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def require_loopback(request: Request) -> None:
    """Reject DNS-rebinding hosts and non-loopback peers; never trust forwarding headers."""
    hosts = [value for name, value in request.scope.get("headers", []) if name.lower() == b"host"]
    host = hosts[0].decode("latin-1") if len(hosts) == 1 else ""
    try:
        parsed = urlsplit(f"http://{host}")
        # Accessing port also rejects malformed and out-of-range values.
        port = parsed.port
        valid = (
            bool(host) and host == host.strip() and not host.endswith(":")
            and parsed.hostname is not None and is_loopback(parsed.hostname)
            and parsed.username is None and parsed.password is None
            and not parsed.path and not parsed.query and not parsed.fragment
            and (port is None or port > 0)
        )
    except ValueError:
        valid = False
    if not valid or request.client is None or not is_loopback(request.client.host):
        raise APIError(403, "loopback_required", "This development console requires a loopback connection.")
    if request.headers.get("sec-fetch-site") == "cross-site":
        raise APIError(403, "origin_forbidden", "Cross-site administration is not allowed.")


def require_origin(request: Request) -> None:
    origin = request.headers.get("origin")
    expected = f"{request.url.scheme}://{request.headers.get('host', '')}"
    if origin is None or origin != expected:
        raise APIError(403, "origin_forbidden", "A matching same-origin request is required.")


def is_public_admin_request(request: Request, settings: Settings) -> bool:
    """Only an exact, single Host is eligible; forwarding headers have no authority."""
    hosts = [value for name, value in request.scope.get("headers", []) if name.lower() == b"host"]
    return bool(
        settings.admin_auth_backend == "access" and settings.admin_public_origin
        and len(hosts) == 1
        and hosts[0].decode("latin-1") == urlsplit(settings.admin_public_origin).netloc
    )


def require_admin_boundary(request: Request, settings: Settings) -> None:
    if is_public_admin_request(request, settings):
        if request.client is None or not is_loopback(request.client.host):
            raise APIError(403, "loopback_required", "Administration requires a trusted local connector.")
        if request.headers.get("sec-fetch-site") == "cross-site":
            # Access/IdP redirects may retain cross-site fetch metadata. Only a
            # top-level, read-only management document can proceed to JWT checks.
            navigation = (
                request.method in {"GET", "HEAD"} and request.url.path in {"/admin", "/admin/"}
                and request.headers.get("sec-fetch-mode") == "navigate"
                and request.headers.get("sec-fetch-dest") == "document"
            )
            if not navigation:
                raise APIError(403, "origin_forbidden", "Cross-site administration is not allowed.")
    else:
        require_loopback(request)
    if request.headers.get("origin") is not None:
        require_admin_origin(request, settings)


def require_admin_origin(request: Request, settings: Settings) -> None:
    if is_public_admin_request(request, settings):
        if request.headers.get("origin") != settings.admin_public_origin:
            raise APIError(403, "origin_forbidden", "A matching same-origin request is required.")
    else:
        require_origin(request)


def admin_asset_paths(settings: Settings) -> frozenset[str]:
    """Allow only the current HTML's two content-addressed admin assets, not a directory."""
    from pathlib import Path

    root = Path(settings.console_dist_path).expanduser().resolve()
    page = root / "admin" / "index.html"
    try:
        if page.is_symlink() or not page.is_file() or page.stat().st_size > 16384:
            return frozenset()
        text = page.read_text(encoding="utf-8")
        paths = re.findall(r'(?:src|href)="(/console-assets/admin-[A-Za-z0-9_-]+\.(?:js|css))"', text)
        if len(paths) != 2 or len(set(paths)) != 2 or {path.rsplit(".", 1)[-1] for path in paths} != {"js", "css"}:
            return frozenset()
        for path in paths:
            asset = root / "assets" / path.rsplit("/", 1)[-1]
            if asset.is_symlink() or not asset.is_file() or asset.resolve().parent != root / "assets":
                return frozenset()
        return frozenset(paths)
    except (OSError, UnicodeError):
        return frozenset()


def csrf_token(token: str) -> str:
    return hmac.new(token.encode(), b"allira-admin-csrf-v1", hashlib.sha256).hexdigest()


def session_view(token: str, expires: float) -> AdminSession:
    return AdminSession(
        csrfToken=csrf_token(token),
        expiresAt=datetime.fromtimestamp(expires, UTC).isoformat().replace("+00:00", "Z"),
    )


def admin_router(settings: Settings) -> APIRouter:
    def boundary(request: Request) -> None:
        require_admin_boundary(request, settings)

    router = APIRouter(
        prefix="/api/v1/admin", tags=["administration"], dependencies=[Depends(boundary)],
    )

    def repo(request: Request) -> AdminRepository:
        return AdminRepository(request.app.state.db)

    async def access_identity(request: Request):
        # Public-host middleware may already have verified this exact request.
        identity = getattr(request.state, "access_identity", None)
        return identity if identity is not None else await verified_access(request)

    async def current_admin(request: Request) -> str:
        token = request.cookies.get(COOKIE_NAME, "")
        if settings.admin_auth_backend == "access":
            identity = await access_identity(request)
            repo(request).authenticate_access(token, identity)
        else:
            repo(request).authenticate(token)
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            require_admin_origin(request, settings)
            submitted = request.headers.get("x-csrf-token", "")
            if not hmac.compare_digest(submitted.encode(), csrf_token(token).encode()):
                raise APIError(403, "csrf_forbidden", "A valid administrator CSRF token is required.")
        return token

    Admin = Annotated[str, Depends(current_admin)]

    @router.post("/session", response_model=AdminSession, response_model_exclude_none=True)
    async def login(body: AdminLogin, request: Request, response: Response):
        if settings.admin_auth_backend != "link":
            raise APIError(403, "admin_link_disabled", "Administrator link authentication is disabled.")
        require_admin_origin(request, settings)
        token = secrets.token_urlsafe(32)
        expires = repo(request).exchange_login(body.code, token, settings.admin_session_ttl_seconds)
        response.set_cookie(
            COOKIE_NAME, token, max_age=settings.admin_session_ttl_seconds,
            httponly=True, samesite="strict", secure=request.url.scheme == "https", path=COOKIE_PATH,
        )
        return session_view(token, expires)

    @router.get("/session", response_model=AdminSession, response_model_exclude_none=True)
    async def get_session(request: Request, response: Response):
        if settings.admin_auth_backend == "link":
            token = await current_admin(request)
            return session_view(token, repo(request).authenticate(token))
        identity = await access_identity(request)
        token = request.cookies.get(COOKIE_NAME, "")
        repository = repo(request)
        try:
            expires = repository.authenticate_access(token, identity)
        except APIError as exc:
            if exc.status != 401:
                raise
            token = secrets.token_urlsafe(32)
            expires = repository.create_access_session(token, identity, settings.admin_session_ttl_seconds)
            response.set_cookie(
                COOKIE_NAME, token, max_age=max(0, int(expires - datetime.now(UTC).timestamp())),
                httponly=True, samesite="strict", secure=True, path=COOKIE_PATH,
            )
        view = session_view(token, expires)
        view.authentication = "cloudflare-access"
        view.identity = AdminAccessIdentity(email=identity.email)
        view.logoutUrl = "/cdn-cgi/access/logout"
        return view

    @router.post("/logout", response_model=OK)
    async def logout(request: Request, response: Response, token: Admin):
        repo(request).logout(token)
        response.delete_cookie(
            COOKIE_NAME, path=COOKIE_PATH, httponly=True, samesite="strict",
            secure=settings.admin_auth_backend == "access" or request.url.scheme == "https",
        )
        return OK()

    @router.get("/overview", response_model=AdminOverview)
    async def overview(request: Request, token: Admin):
        return {**repo(request).overview(), "environment": settings.environment,
                "provider": settings.provider, "maxConcurrentRuns": settings.max_concurrent_runs}

    @router.get("/diagnostics", response_model=AdminDiagnostics)
    async def get_diagnostics(request: Request, token: Admin):
        return diagnostics(request, settings)

    @router.get("/routes", response_model=AdminRouteOverview)
    async def get_routes(request: Request, token: Admin):
        return route_overview(settings)

    @router.get("/users", response_model=Page[AdminUser])
    async def list_users(
        request: Request, token: Admin, cursor: str | None = Query(default=None, max_length=2048),
        limit: int = Query(default=50, ge=1, le=100), q: AdminUserQuery = "",
        identity: AdminIdentityFilter = "all",
    ):
        return repo(request).list_users(cursor, limit, q, identity)

    @router.get("/users/{user_id}/sessions", response_model=Page[AdminDeviceSession])
    async def list_user_sessions(
        user_id: str, request: Request, token: Admin,
        cursor: str | None = Query(default=None, max_length=100),
        limit: int = Query(default=50, ge=1, le=100),
    ):
        return repo(request).list_user_sessions(user_id, cursor, limit)

    @router.post("/users/{user_id}/sessions/{session_id}/revoke", response_model=RevokedSessions)
    async def revoke_user_session(user_id: str, session_id: UUID, request: Request, token: Admin):
        return RevokedSessions(revoked=repo(request).revoke_user_session(user_id, str(session_id)))

    @router.post("/users/{user_id}/revoke-sessions", response_model=RevokedSessions)
    async def revoke_user_sessions(user_id: str, request: Request, token: Admin):
        return RevokedSessions(revoked=repo(request).revoke_user_sessions(user_id))

    @router.get("/conversations", response_model=Page[AdminConversation])
    async def list_conversations(
        request: Request, token: Admin, cursor: str | None = None,
        userId: str | None = None, limit: int = Query(default=50, ge=1, le=100),
    ):
        return repo(request).list_conversations(cursor, limit, userId)

    @router.get("/conversations/{conversation_id}/messages", response_model=Page[Message])
    async def list_messages(
        conversation_id: str, request: Request, token: Admin, cursor: str | None = None,
        limit: int = Query(default=50, ge=1, le=100),
    ):
        return repo(request).list_messages(conversation_id, cursor, limit)

    @router.post("/conversations/{conversation_id}/delete", response_model=OK)
    async def delete_conversation(conversation_id: str, request: Request, token: Admin):
        repo(request).delete_conversation(conversation_id)
        return OK()

    @router.get("/runs", response_model=Page[AdminRun])
    async def list_runs(
        request: Request, token: Admin, cursor: str | None = None,
        status: RunStatus | None = None, userId: str | None = None,
        limit: int = Query(default=50, ge=1, le=100),
    ):
        return repo(request).list_runs(cursor, limit, status, userId)

    @router.get("/runs/{run_id}", response_model=AdminRun)
    async def get_run(run_id: str, request: Request, token: Admin):
        return repo(request).run(run_id)

    @router.post("/runs/{run_id}/cancel", response_model=AdminRun)
    async def cancel_run(run_id: str, request: Request, token: Admin):
        repository = repo(request)
        run = repository.run(run_id)
        # Persist state and the audit record in one transaction, before awaiting task cleanup.
        if run["status"] == "running":
            with repository.db.atomic():
                repository.audit("run.cancel_requested", "run", run_id)
                result = request.app.state.db.finish(run_id, "cancelled")
            # Return the committed snapshot: another request may delete the conversation
            # while provider cleanup awaits, without invalidating this successful cancel.
            await request.app.state.runtime.cancel(run_id)
            return {**result.model_dump(), "ownerId": run["ownerId"]}
        return run

    @router.get("/audit", response_model=Page[AdminAudit])
    async def list_audit(
        request: Request, token: Admin, cursor: str | None = None,
        limit: int = Query(default=50, ge=1, le=100),
    ):
        return repo(request).list_audit(cursor, limit)

    @router.post("/cdks", response_model=dict[str, list[IssuedCdk]])
    async def issue_cdks(body: CdkIssue, request: Request, token: Admin):
        return {"items": EnergyRepository(request.app.state.db, settings).issue(body)}

    @router.get("/cdks", response_model=Page[CdkSummary])
    async def list_cdks(
        request: Request, token: Admin, cursor: str | None = None,
        limit: int = Query(default=50, ge=1, le=100),
    ):
        return EnergyRepository(request.app.state.db, settings).list_cdks(cursor, limit)

    @router.post("/cdks/{prefix}/disable", response_model=OK)
    async def disable_cdk(prefix: str, request: Request, token: Admin):
        EnergyRepository(request.app.state.db, settings).disable_cdk(prefix)
        return OK()

    @router.get("/usage", response_model=UsageOverview)
    async def usage_overview(request: Request, token: Admin):
        return EnergyRepository(request.app.state.db, settings).overview()

    return router
