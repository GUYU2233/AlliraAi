"""Version-one JSON DTOs; generated OpenAPI is the contract source."""

import unicodedata
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, field_validator, model_validator


class DTO(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DevSessionInput(DTO):
    userId: str = Field(min_length=1, max_length=100, pattern=r"^[a-zA-Z0-9_-]+$")


class Session(DTO):
    accessToken: str
    tokenType: Literal["Bearer"] = "Bearer"


class ConversationInput(DTO):
    title: str = Field(default="新对话", min_length=1, max_length=200)
    instructions: str | None = Field(default=None, max_length=20000)

    @field_validator("title")
    @classmethod
    def valid_title(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("title must not be blank")
        return value.strip()


class ConversationUpdate(DTO):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    instructions: str | None = Field(default=None, max_length=20000)

    @field_validator("title")
    @classmethod
    def valid_title(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            raise ValueError("title must not be null or blank")
        return value.strip()

    @model_validator(mode="after")
    def require_change(self) -> "ConversationUpdate":
        if not self.model_fields_set:
            raise ValueError("at least one field is required")
        return self


class Conversation(DTO):
    id: str
    title: str
    instructions: str | None
    createdAt: str
    updatedAt: str


MessageStatus = Literal["generating", "completed", "failed", "cancelled"]
RunStatus = Literal["running", "completed", "failed", "cancelled"]


class Message(DTO):
    id: str
    conversationId: str
    runId: str
    sequence: int
    role: Literal["user", "assistant"]
    content: str
    status: MessageStatus
    createdAt: str


class RunInput(DTO):
    clientMessageId: UUID
    content: str = Field(min_length=1, max_length=20000)
    mode: Literal["economy", "fast"] = "economy"

    @field_validator("content")
    @classmethod
    def nonblank_content(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("content must not be blank")
        return value


class Run(DTO):
    id: str
    conversationId: str
    clientMessageId: str
    userMessageId: str
    assistantMessageId: str
    status: RunStatus
    errorCode: str | None
    createdAt: str
    updatedAt: str


class ConversationState(DTO):
    conversation: Conversation
    activeRun: Run | None
    lastSequence: int


class OK(DTO):
    ok: Literal[True] = True


class CurrentSession(DTO):
    userId: str


class ServerInfo(DTO):
    environment: Literal["development", "production"]
    provider: Literal["mock", "openai"]
    allowDevAuth: bool
    consoleEnabled: bool
    adminEnabled: bool


class AdminLogin(DTO):
    code: str = Field(min_length=32, max_length=200)


class AdminAccessIdentity(DTO):
    email: str


class AdminSession(DTO):
    authenticated: Literal[True] = True
    csrfToken: str
    expiresAt: str
    authentication: Literal["cloudflare-access"] | None = None
    identity: AdminAccessIdentity | None = None
    logoutUrl: Literal["/cdn-cgi/access/logout"] | None = None


AdminIdentityFilter = Literal["all", "oidc", "legacy", "developer"]


def normalize_admin_user_query(value: str) -> str:
    # Reject hidden/control characters before trimming; never normalize a search
    # into a different identity or reflect the rejected query in an API error.
    if any(unicodedata.category(character).startswith("C") for character in value):
        raise ValueError("user query contains invalid characters")
    value = value.strip()
    try:
        encoded = value.encode("utf-8")
    except UnicodeEncodeError:
        raise ValueError("user query must contain valid Unicode text") from None
    if len(value) > 100 or len(encoded) > 400:
        raise ValueError("user query exceeds the supported length")
    return value


AdminUserQuery = Annotated[str, AfterValidator(normalize_admin_user_query)]


class AdminUser(DTO):
    userId: str
    conversationCount: int
    runCount: int
    activeSessions: int
    createdAt: str
    accountType: Literal["registered", "developer"]
    username: str | None
    displayName: str | None
    identityStatus: Literal["oidc", "legacy", "developer"]
    maskedPhone: str | None
    phoneVerified: bool
    passwordEnabled: bool
    walletBalance: int | None


class AdminDeviceSession(DTO):
    id: str
    createdAt: str
    lastSeenAt: str
    expiresAt: str
    label: str
    type: Literal["bearer", "browser"]


class AdminConversation(Conversation):
    ownerId: str
    messageCount: int


class AdminRun(Run):
    ownerId: str


class RunCounts(DTO):
    running: int
    completed: int
    failed: int
    cancelled: int


class AdminOverview(DTO):
    users: int
    conversations: int
    messages: int
    runs: RunCounts
    activeSessions: int
    environment: Literal["development", "production"]
    provider: Literal["mock", "openai"]
    maxConcurrentRuns: int


class AdminAudit(DTO):
    id: str
    action: str
    targetType: str
    targetId: str | None
    createdAt: str


class RevokedSessions(OK):
    revoked: int


class Page[T](DTO):
    items: list[T]
    nextCursor: str | None


class ErrorDetail(DTO):
    code: str
    message: str


class ErrorResponse(DTO):
    error: ErrorDetail
