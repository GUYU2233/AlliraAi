"""Application factory. Start with one Uvicorn worker; never expose dev auth publicly."""

import asyncio
import secrets
from contextlib import asynccontextmanager
from typing import Annotated

import httpx
from fastapi import Depends, FastAPI, Header, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from starlette.exceptions import HTTPException

from .config import Settings
from .db import APIError, Database
from .providers import MockProvider, ModelProvider, OpenAICompatibleProvider
from .runtime import RunRuntime
from .schemas import (
    Conversation,
    ConversationInput,
    DevSessionInput,
    ErrorResponse,
    Message,
    Page,
    Run,
    RunInput,
    Session,
)


def create_app(settings: Settings | None = None, provider: ModelProvider | None = None) -> FastAPI:
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        db = Database(settings.database_path)
        try:
            db.recover_interrupted_runs()
            async with httpx.AsyncClient(timeout=httpx.Timeout(60, connect=10)) as client:
                selected_provider = provider
                if selected_provider is None:
                    selected_provider = (
                        MockProvider() if settings.provider == "mock" else OpenAICompatibleProvider(
                            settings.upstream_base_url, settings.upstream_model,
                            settings.upstream_api_key, client=client,
                        )
                    )
                app.state.db = db
                app.state.runtime = RunRuntime(db, selected_provider, settings)
                try:
                    yield
                finally:
                    await app.state.runtime.close()
        finally:
            db.close()

    errors = {
        status: {"model": ErrorResponse}
        for status in (400, 401, 404, 409, 413, 422, 500)
    }
    app = FastAPI(
        title="AlliraAi API", version="0.1.0", lifespan=lifespan,
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

    bearer = HTTPBearer(auto_error=False)

    async def current_user(
        request: Request,
        credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    ) -> str:
        if credentials is None or credentials.scheme.lower() != "bearer":
            raise APIError(401, "unauthorized", "A valid session is required.")
        return request.app.state.db.authenticate(credentials.credentials)

    User = Annotated[str, Depends(current_user)]

    @app.get("/healthz", tags=["operations"])
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    if settings.allow_dev_auth:
        @app.post("/api/v1/dev/session", response_model=Session, tags=["development"])
        async def dev_session(body: DevSessionInput, request: Request):
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

    @app.post(
        "/api/v1/conversations/{conversation_id}/runs", response_model=Run,
        status_code=202, tags=["chat"],
    )
    async def create_run(conversation_id: str, body: RunInput, request: Request, user: User):
        run, prompt = request.app.state.db.create_run(
            user, conversation_id, body, settings.max_prompt_characters,
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
            await request.app.state.runtime.cancel(run_id)
        return request.app.state.db.run(user, run_id)

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
                batch = db.events(run_id, cursor)
                for event in batch:
                    cursor = event["eventId"]
                    yield f"id: {cursor}\nevent: {event['type']}\ndata: {event['data']}\n\n"
                if len(batch) == 100:
                    continue
                if db.run_internal(run_id).status != "running":
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

    return app
