"""Version-one JSON DTOs; generated OpenAPI is the contract source."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


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


class Page[T](DTO):
    items: list[T]
    nextCursor: str | None


class ErrorDetail(DTO):
    code: str
    message: str


class ErrorResponse(DTO):
    error: ErrorDetail
