"""Application factory. Start with one Uvicorn worker; never expose dev auth publicly."""

import asyncio
import logging
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

import httpx
from fastapi import Depends, FastAPI, Header, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException

from .access_identity import AccessVerifier, verified_access
from .accounts import accounts_router
from .admin import (
    admin_asset_paths,
    admin_router,
    is_public_admin_request,
    require_admin_boundary,
    require_loopback,
)
from .browser_sessions import authenticate_browser, browser_sessions_router, clear_cookie
from .companion import companion_router
from .config import Settings
from .context_memory import context_memory_router
from .conversation_tools import conversation_tools_router
from .data_controls import data_controls_router
from .db import APIError, Database
from .economy import economy_router
from .http_limits import RequestBodyLimit
from .identity_lifecycle import clear_transient_identity_state
from .identity_security import identity_security_router
from .imports import imports_router
from .memory import memory_router
from .oidc_identity import oidc_identity_router
from .operations import API_VERSION, initialize_operations, readiness
from .phone_identity import phone_identity_router
from .providers import MockProvider, ModelProvider, OpenAICompatibleProvider
from .runtime import RunRuntime
from .safety import public_router
from .schemas import (
    OK,
    Conversation,
    ConversationInput,
    ConversationState,
    ConversationUpdate,
    CurrentSession,
    DevSessionInput,
    ErrorResponse,
    Message,
    Page,
    Run,
    RunInput,
    ServerInfo,
    Session,
)
from .sms import create_sms_sender
from .social_identity import social_identity_router
from .social_providers import default_social_providers


def create_app(
    settings: Settings | None = None, provider: ModelProvider | None = None,
    fast_provider: ModelProvider | None = None,
) -> FastAPI:
    settings = settings or Settings()
    # Tencent website token requests use query parameters. Do not let generic HTTP
    # INFO/DEBUG request logging expose OAuth codes, app secrets or access tokens.
    for name in ("httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.WARNING)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        db = Database(settings.database_path, settings)
        try:
            db.recover_interrupted_runs()
            clear_transient_identity_state(db)
            app.state.access_verifier = None
            if settings.admin_auth_backend == "access":
                # Retire every previous link/Cookie at deployment or restart.
                with db.atomic():
                    db.conn.execute("DELETE FROM admin_login_codes")
                    db.conn.execute("DELETE FROM admin_sessions")
                if settings.admin_enabled and settings.access_team_domain:
                    app.state.access_verifier = AccessVerifier(settings)
            async with (
                httpx.AsyncClient(timeout=httpx.Timeout(60, connect=10)) as client,
                httpx.AsyncClient(timeout=httpx.Timeout(15, connect=5), trust_env=False,
                                  follow_redirects=False) as identity_client,
            ):
                selected_provider = provider
                if selected_provider is None:
                    selected_provider = (
                        MockProvider() if settings.provider == "mock" else OpenAICompatibleProvider(
                            settings.upstream_base_url, settings.upstream_model,
                            settings.upstream_api_key, client=client,
                            max_tokens=settings.upstream_max_tokens,
                        )
                    )
                selected_fast = fast_provider
                if selected_fast is None:
                    if settings.provider == "mock":
                        selected_fast = selected_provider
                    elif settings.fast_upstream_model:
                        selected_fast = OpenAICompatibleProvider(
                            settings.fast_upstream_base_url or settings.upstream_base_url,
                            settings.fast_upstream_model, settings.fast_upstream_api_key,
                            client=client, max_tokens=settings.upstream_max_tokens,
                        )
                app.state.db = db
                app.state.settings = settings
                app.state.sms_sender = create_sms_sender(settings)
                app.state.social_providers = default_social_providers(settings, identity_client)
                app.state.runtime = RunRuntime(db, selected_provider, settings, selected_fast)
                initialize_operations(app)
                try:
                    yield
                finally:
                    await app.state.runtime.close()
        finally:
            verifier = getattr(app.state, "access_verifier", None)
            if verifier is not None:
                await verifier.aclose()
            db.close()

    errors = {
        status: {"model": ErrorResponse}
        for status in (400, 401, 402, 403, 404, 409, 413, 422, 429, 500)
    }
    app = FastAPI(
        title="AlliraAi API", version=API_VERSION, lifespan=lifespan,
        description="UI-independent development API. One process, SQLite, REST and replayable SSE.",
        responses=errors,
    )
    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware, allow_origins=settings.cors_origins,
            allow_methods=["GET", "POST"], allow_headers=["Authorization", "Content-Type"],
            allow_credentials=False,
        )

    def error_response(status: int, code: str, message: str) -> JSONResponse:
        return JSONResponse({"error": {"code": code, "message": message}}, status_code=status)

    @app.exception_handler(APIError)
    async def handle_api_error(request: Request, exc: APIError):
        return error_response(exc.status, exc.code, exc.message)

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(request: Request, exc: RequestValidationError):
        # Pydantic's raw errors can contain input content. Return no raw bodies or tokens.
        return error_response(422, "validation_error", "Request parameters are invalid.")

    @app.exception_handler(HTTPException)
    async def handle_http_error(request: Request, exc: HTTPException):
        return error_response(exc.status_code, "http_error", "Request could not be processed.")

    @app.exception_handler(Exception)
    async def handle_unexpected_error(request: Request, exc: Exception):
        return error_response(500, "internal_error", "An internal error occurred.")

    # Exact current asset names, rebuilt at process startup after deployment.
    exposed_admin_assets = admin_asset_paths(settings) if settings.console_enabled else frozenset()

    @app.middleware("http")
    async def development_boundary(request: Request, call_next):
        path = request.url.path
        host_is_public = is_public_admin_request(request, settings)
        if settings.admin_public_origin and not host_is_public:
            try:
                # Unknown or ambiguous hosts never become a second public business origin.
                require_loopback(request)
            except APIError as exc:
                response = error_response(exc.status, exc.code, exc.message)
                response.headers["Cache-Control"] = "no-store"
                return response
        if host_is_public:
            try:
                require_admin_boundary(request, settings)
                # No normalization may turn an ambiguous public path into a business route.
                raw = request.scope.get("raw_path", b"").split(b"?", 1)[0]
                if (not settings.admin_enabled or not settings.console_enabled
                        or b"%" in raw or bytes([92]) in raw or b"//" in raw
                        or any(part in {".", ".."} for part in path.split("/"))
                        or not (path in {"/admin", "/admin/"} or path in exposed_admin_assets
                                or path.startswith("/api/v1/admin/"))):
                    raise APIError(404, "not_found", "Resource is not published on this host.")
                request.state.access_identity = await verified_access(request)
            except APIError as exc:
                response = error_response(exc.status, exc.code, exc.message)
                response.headers["Cache-Control"] = "no-store"
                response.headers["X-Content-Type-Options"] = "nosniff"
                response.headers["Referrer-Policy"] = "no-referrer"
                return response
        if settings.auth_backend == "oidc" and (
            path in {"/api/v1/auth/register", "/api/v1/auth/login", "/api/v1/auth/recover",
                     "/api/v1/account/password", "/api/v1/account/recovery", "/api/v1/account/erase"}
            or path.startswith(("/api/v1/auth/phone/", "/api/v1/auth/social/",
                                "/api/v1/account/phone/"))
        ):
            # Close the previous authentication core server-side, not merely in UI.
            # Only OIDC's explicit migration endpoint may check an old credential;
            # it cannot issue an account session without a fresh verified OIDC proof.
            response = error_response(403, "oidc_auth_required",
                                      "Use the configured identity provider for authentication.")
            response.headers["Cache-Control"] = "no-store"
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["Referrer-Policy"] = "no-referrer"
            return response
        is_console = path in {"/test", "/admin"} or path.startswith(
            ("/test/", "/admin/", "/console-assets/", "/api/v1/admin/"),
        )
        if is_console:
            try:
                if path in {"/admin", "/admin/"} or path.startswith("/api/v1/admin/") or host_is_public:
                    require_admin_boundary(request, settings)
                else:
                    require_loopback(request)
            except APIError as exc:
                response = error_response(exc.status, exc.code, exc.message)
                response.headers["Cache-Control"] = "no-store"
                return response
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        if path.startswith("/api/") or is_console:
            response.headers["Cache-Control"] = "no-store"
        if is_console:
            response.headers["X-Frame-Options"] = "DENY"
            response.headers["Content-Security-Policy"] = (
                "default-src 'none'; script-src 'self'; style-src 'self'; "
                "connect-src 'self'; img-src 'self'; manifest-src 'self'; worker-src 'self'; base-uri 'none'; "
                "form-action 'self'; frame-ancestors 'none'; object-src 'none'"
            )
        return response

    bearer = HTTPBearer(auto_error=False)

    async def current_user(
        request: Request,
        credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    ) -> str:
        if request.headers.get("authorization") is not None:
            # Never fall back to an ambient cookie for a malformed/expired explicit token.
            if credentials is None or credentials.scheme.lower() != "bearer":
                raise APIError(401, "unauthorized", "A valid session is required.")
            user = request.app.state.db.authenticate(credentials.credentials, kind="bearer")
            request.state.session_token = credentials.credentials
            request.state.auth_kind = "bearer"
            return user
        return authenticate_browser(request)

    User = Annotated[str, Depends(current_user)]

    @app.get("/healthz", tags=["operations"])
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz", tags=["operations"])
    async def readyz(request: Request) -> dict[str, str]:
        return readiness(request)

    @app.get("/api/v1/info", response_model=ServerInfo, tags=["operations"])
    async def info():
        return ServerInfo(
            environment=settings.environment, provider=settings.provider,
            allowDevAuth=settings.allow_dev_auth, consoleEnabled=settings.console_enabled,
            adminEnabled=settings.admin_enabled,
        )

    @app.get("/api/v1/session", response_model=CurrentSession, tags=["session"])
    async def get_session(user: User):
        return CurrentSession(userId=user)

    @app.post("/api/v1/session/logout", response_model=OK, tags=["session"])
    async def logout(request: Request, response: Response, user: User):
        request.app.state.db.revoke_session(request.state.session_token)
        if request.state.auth_kind == "browser":
            clear_cookie(response, secure=request.url.scheme == "https")
        return OK()

    if settings.allow_dev_auth:
        @app.post("/api/v1/dev/session", response_model=Session, tags=["development"])
        async def dev_session(body: DevSessionInput, request: Request):
            if body.userId.lower().startswith("acct_"):
                raise APIError(403, "reserved_identity", "Account identities cannot use developer authentication.")
            token = secrets.token_urlsafe(32)
            request.app.state.db.create_session(token, body.userId, settings.session_ttl_seconds)
            return Session(accessToken=token)

    @app.post("/api/v1/conversations", response_model=Conversation, status_code=201, tags=["chat"])
    async def create_conversation(body: ConversationInput, request: Request, user: User):
        return request.app.state.db.create_conversation(user, body)

    @app.get("/api/v1/conversations", response_model=Page[Conversation], tags=["chat"])
    async def list_conversations(
        request: Request, user: User, cursor: str | None = None,
        limit: int = Query(default=50, ge=1, le=100),
    ):
        return request.app.state.db.list_conversations(user, cursor, limit)

    @app.get("/api/v1/conversations/{conversation_id}", response_model=Conversation, tags=["chat"])
    async def get_conversation(conversation_id: str, request: Request, user: User):
        return request.app.state.db.conversation(user, conversation_id)

    @app.get(
        "/api/v1/conversations/{conversation_id}/messages", response_model=Page[Message], tags=["chat"],
    )
    async def list_messages(
        conversation_id: str, request: Request, user: User, cursor: str | None = None,
        limit: int = Query(default=50, ge=1, le=100),
    ):
        return request.app.state.db.list_messages(user, conversation_id, cursor, limit)

    @app.get(
        "/api/v1/conversations/{conversation_id}/messages/recent",
        response_model=Page[Message], tags=["chat"],
    )
    async def recent_messages(
        conversation_id: str, request: Request, user: User, before: str | None = None,
        limit: int = Query(default=50, ge=1, le=100),
    ):
        return request.app.state.db.list_recent_messages(user, conversation_id, before, limit)

    @app.get(
        "/api/v1/conversations/{conversation_id}/state",
        response_model=ConversationState, tags=["chat"],
    )
    async def conversation_state(conversation_id: str, request: Request, user: User):
        return request.app.state.db.conversation_state(user, conversation_id)

    @app.post(
        "/api/v1/conversations/{conversation_id}/update", response_model=Conversation, tags=["chat"],
    )
    async def update_conversation(
        conversation_id: str, body: ConversationUpdate, request: Request, user: User,
    ):
        return request.app.state.db.update_conversation(user, conversation_id, body)

    @app.post(
        "/api/v1/conversations/{conversation_id}/delete", response_model=OK, tags=["chat"],
    )
    async def delete_conversation(conversation_id: str, request: Request, user: User):
        request.app.state.db.delete_conversation(user, conversation_id)
        return OK()

    @app.post(
        "/api/v1/conversations/{conversation_id}/runs", response_model=Run,
        status_code=202, tags=["chat"],
    )
    async def create_run(conversation_id: str, body: RunInput, request: Request, user: User):
        run, prompt = request.app.state.db.create_run(
            user, conversation_id, body, settings.max_prompt_characters, settings.max_concurrent_runs,
            recent_turns=settings.context_recent_turns,
            summary_max_characters=settings.summary_max_characters,
            in_flight_run_ids=request.app.state.runtime.in_flight_run_ids(),
        )
        if prompt is not None:
            request.app.state.runtime.start(run.id, prompt)
        return run

    @app.get("/api/v1/runs/{run_id}", response_model=Run, tags=["chat"])
    async def get_run(run_id: str, request: Request, user: User):
        return request.app.state.db.run(user, run_id)

    @app.post("/api/v1/runs/{run_id}/cancel", response_model=Run, tags=["chat"])
    async def cancel_run(run_id: str, request: Request, user: User):
        run = request.app.state.db.run(user, run_id)
        if run.status == "running":
            return await request.app.state.runtime.cancel(run_id)
        return run

    @app.get(
        "/api/v1/runs/{run_id}/events", tags=["chat"], response_class=StreamingResponse,
        responses={200: {"description": "Ordered, replayable run events.",
                         "content": {"text/event-stream": {"schema": {"type": "string"}}}}},
    )
    async def stream_events(
        run_id: str, request: Request, user: User,
        after: int = Query(default=0, ge=0, le=9007199254740991),
        last_event_id: str | None = Header(default=None),
    ):
        # Cursor is explicit and run-local, not a message/conversation cursor.
        if last_event_id is not None:
            raise APIError(400, "invalid_cursor", "Use the explicit after query parameter.")
        db = request.app.state.db
        db.run(user, run_id)  # Authorize before creating/streaming a 200 response.
        if after > db.max_event_id(run_id):
            raise APIError(400, "invalid_cursor", "Event cursor is ahead of this run.")

        async def events():
            cursor = after
            heartbeat_at = asyncio.get_running_loop().time()
            yield ": connected\n\n"
            while True:
                if await request.is_disconnected():
                    return  # Client departure does not cancel the producer task.
                try:
                    db.authenticate(request.state.session_token, kind=request.state.auth_kind)
                except APIError:
                    return  # Revocation/expiry disconnects the reader, not generation.
                batch = db.events(run_id, cursor)
                for event in batch:
                    cursor = event["eventId"]
                    yield f"id: {cursor}\nevent: {event['type']}\ndata: {event['data']}\n\n"
                if len(batch) == 100:
                    continue
                try:
                    status = db.run(user, run_id).status
                except APIError as exc:
                    if exc.status == 404:
                        return  # The owner/admin may delete a terminal conversation mid-replay.
                    raise
                if status != "running":
                    # The producer may finish while earlier yields are being sent. Drain its
                    # committed terminal event before ending the subscriber connection.
                    if cursor >= db.max_event_id(run_id):
                        return
                    continue
                current = asyncio.get_running_loop().time()
                if current - heartbeat_at >= 10:
                    yield ": heartbeat\n\n"
                    heartbeat_at = current
                await asyncio.sleep(0.05)

        return StreamingResponse(
            events(), media_type="text/event-stream",
            headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
        )

    app.include_router(accounts_router(settings, current_user))
    app.include_router(oidc_identity_router(settings, current_user))
    app.include_router(phone_identity_router(settings, current_user))
    app.include_router(identity_security_router(settings, current_user))
    app.include_router(social_identity_router(settings, current_user))
    app.include_router(browser_sessions_router(settings, current_user))
    app.include_router(data_controls_router(settings, current_user))
    app.include_router(conversation_tools_router(settings, current_user))
    app.include_router(memory_router(settings, current_user))
    app.include_router(context_memory_router(settings, current_user))
    app.include_router(companion_router(settings, current_user))
    app.include_router(imports_router(settings, current_user))
    app.include_router(economy_router(settings, current_user))
    app.include_router(public_router(settings))
    if settings.admin_enabled:
        app.include_router(admin_router(settings))

    if settings.console_enabled:
        console_root = Path(settings.console_dist_path).expanduser().resolve()

        def console_page(name: str) -> FileResponse:
            page = console_root / name / "index.html"
            if not page.is_file():
                raise APIError(503, "console_not_built", "Build the development console on the server.")
            return FileResponse(page, media_type="text/html")

        @app.get("/test/", include_in_schema=False)
        async def test_console():
            return console_page("test")

        @app.get("/test/sw.js", include_in_schema=False)
        async def test_service_worker():
            path = console_root / "test" / "sw.js"
            if not path.is_file():
                raise APIError(503, "console_not_built", "Build the development console on the server.")
            return FileResponse(path, media_type="application/javascript",
                                headers={"Service-Worker-Allowed": "/test/"})

        @app.get("/test/manifest.webmanifest", include_in_schema=False)
        async def test_manifest():
            path = console_root / "test" / "manifest.webmanifest"
            if not path.is_file():
                raise APIError(503, "console_not_built", "Build the development console on the server.")
            return FileResponse(path, media_type="application/manifest+json")

        @app.get("/admin", include_in_schema=False)
        async def admin_redirect():
            # A relative fixed Location preserves HTTPS behind the local connector
            # without trusting X-Forwarded-* or publishing a request-supplied host.
            return RedirectResponse("/admin/", status_code=307)

        @app.get("/admin/", include_in_schema=False)
        async def admin_console():
            return console_page("admin")

        if (console_root / "assets").is_dir():
            app.mount(
                "/console-assets", StaticFiles(directory=console_root / "assets"),
                name="console-assets",
            )

    app.add_middleware(RequestBodyLimit)
    return app
