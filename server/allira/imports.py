"""Local, bounded history parsing and atomic, user-scoped import commits.

Only genuine adjacent user/assistant pairs become completed runs. Unpaired and
unsupported records remain in the explicitly retrievable original source; no
counterpart is invented and no import invokes a model, tool or external request.
"""

import hashlib
import json
import re
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from pydantic import Field, field_validator

from .config import Settings
from .db import APIError, Database, identifier, now
from .memory import MemoryRepository, MemoryUpdate, atomic, initialize_memory
from .schemas import DTO, Conversation

MAX_SOURCE_BYTES = 1024 * 1024
MAX_MESSAGES = 1000
MAX_MESSAGE_CHARACTERS = 20000
ImportFormat = Literal["json", "markdown", "text"]


class ImportInput(DTO):
    format: ImportFormat
    content: str


class ImportMessage(DTO):
    role: Literal["user", "assistant"]
    content: str


class ImportPreview(DTO):
    messages: list[ImportMessage]
    warnings: list[str]
    suggestedTitle: str
    sourceHash: str


class ImportCommitInput(ImportInput):
    clientImportId: UUID
    title: str | None = Field(default=None, min_length=1, max_length=200)
    characterId: str | None = Field(default=None, min_length=1, max_length=100)
    coreMemory: str = Field(default="", max_length=20000)
    relationship: str = Field(default="", max_length=20000)

    @field_validator("title")
    @classmethod
    def nonblank_title(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("title must not be blank")
        # Preserve submitted title too: idempotency compares exact original parameters.
        return value


class ImportCommitResult(DTO):
    conversation: Conversation
    importedMessages: int
    archivedMessages: int
    replayed: bool


class ImportSource(DTO):
    format: ImportFormat
    content: str
    archivedMessages: int


@dataclass
class ParsedImport:
    preview: ImportPreview
    pairs: list[tuple[ImportMessage, ImportMessage]]
    unpaired: list[tuple[int, ImportMessage]]

    @property
    def archived_messages(self) -> int:
        return len(self.unpaired)


def initialize_imports(db: Database) -> None:
    initialize_memory(db)
    db.conn.executescript("""
        CREATE TABLE IF NOT EXISTS history_imports (
            ownerId TEXT NOT NULL, clientImportId TEXT NOT NULL, requestHash TEXT NOT NULL,
            conversationId TEXT NOT NULL UNIQUE REFERENCES conversations(id) ON DELETE CASCADE,
            format TEXT NOT NULL, content TEXT NOT NULL, sourceHash TEXT NOT NULL,
            importedMessages INTEGER NOT NULL, archivedMessages INTEGER NOT NULL,
            createdAt TEXT NOT NULL, PRIMARY KEY(ownerId, clientImportId)
        );
        CREATE TABLE IF NOT EXISTS imports_unpaired (
            conversationId TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
            sourceIndex INTEGER NOT NULL, role TEXT NOT NULL, content TEXT NOT NULL,
            PRIMARY KEY(conversationId, sourceIndex)
        );
    """)


def _invalid(message: str = "The import structure is not supported.") -> APIError:
    return APIError(400, "invalid_import", message)


def _warning(warnings: list[str], message: str) -> None:
    if message not in warnings:
        warnings.append(message)


def _message(role: str, content: Any, warnings: list[str]) -> ImportMessage | None:
    supported_role = role in ("user", "assistant")
    if not supported_role:
        _warning(warnings, "System, developer, tool and unknown-role records are archived only, "
                 "never model system instructions or generation context.")
    if isinstance(content, dict):
        content = content.get("parts")
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, dict) and part.get("type") in ("text", "input_text", "output_text"):
                if not isinstance(part.get("text"), str):
                    raise _invalid("Text parts must contain strings.")
                parts.append(part["text"])
            else:
                _warning(warnings, "Non-text content is retained in the source archive only.")
                return None
        content = "".join(parts)
    if not isinstance(content, str):
        _warning(warnings, "Missing or non-text messages are retained in the source archive only.")
        return None
    if len(content) > MAX_MESSAGE_CHARACTERS:
        raise APIError(413, "import_message_too_large", "An imported message exceeds 20000 characters.")
    try:
        content.encode("utf-8")
    except UnicodeEncodeError:
        raise _invalid("Imported messages must contain valid Unicode text.") from None
    if not supported_role:
        return None
    return ImportMessage(role=role, content=content)


def _mapping_messages(data: dict[str, Any], warnings: list[str]) -> list[Any]:
    """Follow one ChatGPT export branch, never combine unrelated tree branches."""
    mapping = data["mapping"]
    if not isinstance(mapping, dict):
        raise _invalid("The conversation mapping is invalid.")
    if len(mapping) > MAX_MESSAGES + 1:
        raise APIError(413, "too_many_import_messages", "Imports support at most 1000 messages.")
    current = data.get("current_node")
    if current is None:
        leaves = [key for key, node in mapping.items()
                  if isinstance(node, dict) and not node.get("children")]
        if len(leaves) != 1:
            raise _invalid("A branched export requires a current_node selection.")
        current = leaves[0]
    records = []
    seen = set()
    while current is not None:
        if not isinstance(current, str) or current in seen or current not in mapping:
            raise _invalid("The conversation mapping has a broken or cyclic parent chain.")
        seen.add(current)
        node = mapping[current]
        if not isinstance(node, dict):
            raise _invalid()
        if node.get("message") is not None:
            records.append(node["message"])
        current = node.get("parent")
    if len(seen) != len(mapping):
        _warning(warnings, "Only the selected export branch is imported; other branches remain "
                 "available in the original source archive.")
    return list(reversed(records))


def _allira_pairs(rows: list[Any], conversation_id: str, warnings: list[str]) -> set[int]:
    """Select pair starts before parsing/filtering so archived rows remain barriers."""
    def occurrences(field: str, valid: Callable[[Any], bool]) -> Counter:
        return Counter(row[field] for row in rows
                       if isinstance(row, dict) and valid(row.get(field)))

    message_ids = occurrences("id", lambda value: isinstance(value, str) and bool(value))
    sequences = occurrences("sequence", lambda value: type(value) is int and value > 0)
    run_ids = occurrences("runId", lambda value: isinstance(value, str) and bool(value))

    def valid_message(row: Any) -> bool:
        return (
            isinstance(row, dict) and row.get("status") == "completed"
            and row.get("conversationId") == conversation_id
            and isinstance(row.get("content"), str)
            and isinstance(row.get("id"), str) and bool(row["id"])
            and message_ids[row["id"]] == 1
            and type(row.get("sequence")) is int and row["sequence"] > 0
            and sequences[row["sequence"]] == 1
            and isinstance(row.get("runId"), str) and bool(row["runId"])
            and run_ids[row["runId"]] == 2
        )

    starts = set()
    previous_sequence = 0
    for index in range(len(rows) - 1):
        user, assistant = rows[index:index + 2]
        if (valid_message(user) and valid_message(assistant)
                and user.get("role") == "user" and assistant.get("role") == "assistant"
                and user["runId"] == assistant["runId"]
                and assistant["sequence"] == user["sequence"] + 1
                and user["sequence"] > previous_sequence):
            starts.add(index)
            previous_sequence = assistant["sequence"]
    _warning(warnings, "Allira archive migration imports only original adjacent completed user/assistant pairs "
             "with matching conversation, run and sequence metadata. Failed, cancelled, generating and "
             "malformed/unpaired records are archived only; "
             "roles, instructions, memory, identifiers, credentials and usage are not restored.")
    return starts


def _json_records(
    content: str, warnings: list[str],
) -> tuple[list[ImportMessage | None], str | None, set[int] | None]:
    try:
        data = json.loads(content)
    except (ValueError, RecursionError):
        raise _invalid("The supplied JSON is invalid.") from None
    # Common official exports wrap a single conversation in an array.
    if isinstance(data, list) and len(data) == 1 and isinstance(data[0], dict) and (
        "messages" in data[0] or "mapping" in data[0]
    ):
        data = data[0]
    allira_archive = False
    conversation = None
    if isinstance(data, dict):
        declared_format = data.get("format")
        if isinstance(declared_format, str) and declared_format.startswith("allira.conversation."):
            if declared_format != "allira.conversation.v1":
                raise _invalid("This Allira conversation archive version is not supported.")
            allira_archive = True
            conversation = data.get("conversation")
            if (not isinstance(conversation, dict) or not isinstance(conversation.get("id"), str)
                    or not conversation["id"] or not isinstance(data.get("messages"), list)
                    or "mapping" in data):
                raise _invalid("The Allira conversation archive metadata is invalid.")
    title = (conversation.get("title") if allira_archive
             else data.get("title") if isinstance(data, dict) else None)
    if not isinstance(title, str) or not title.strip():
        title = None
    elif title is not None:
        try:
            title.encode("utf-8")
        except UnicodeEncodeError:
            raise _invalid("Imported titles must contain valid Unicode text.") from None
    if isinstance(data, dict) and "mapping" in data:
        rows = _mapping_messages(data, warnings)
    elif isinstance(data, dict) and isinstance(data.get("messages"), list):
        rows = data["messages"]
    elif isinstance(data, list):
        rows = data
    else:
        _warning(warnings, "Roles could not be identified; the original text is a single user "
                 "message, archived only unless paired with a genuine assistant record.")
        return [_message("user", content, warnings)], title, None
    if len(rows) > MAX_MESSAGES:
        raise APIError(413, "too_many_import_messages", "Imports support at most 1000 messages.")
    allowed_pairs = _allira_pairs(rows, conversation["id"], warnings) if allira_archive else None
    records = []
    for row in rows:
        if not isinstance(row, dict):
            raise _invalid("Each JSON message must be an object.")
        author = row.get("author")
        role = row.get("role")
        if role is None and isinstance(author, dict):
            role = author.get("role")
        records.append(_message(role, row.get("content"), warnings))
    return records, title, allowed_pairs


ROLE_NAMES = {
    "user": "user", "human": "user", "用户": "user",
    "assistant": "assistant", "ai": "assistant", "助手": "assistant",
    "system": "system", "系统": "system", "developer": "developer", "tool": "tool",
}
ROLE_PATTERN = "|".join(ROLE_NAMES)
TEXT_ROLE = re.compile(rf"^[ \t]*({ROLE_PATTERN})[ \t]*[:：]", re.IGNORECASE)
MARKDOWN_ROLE = re.compile(
    rf"^[ \t]*#{{1,6}}[ \t]+(?:\*\*)?({ROLE_PATTERN})(?:\*\*)?[ \t]*[:：]?[ \t]*(?:\r?\n)?$",
    re.IGNORECASE,
)


def _text_records(content: str, markdown: bool, warnings: list[str]) -> list[ImportMessage | None]:
    records: list[ImportMessage | None] = []
    current_role: str | None = None
    pieces: list[str] = []
    identified = False
    fence: tuple[str, int] | None = None

    def opening_fence(line: str) -> tuple[str, int] | None:
        # CommonMark fences have at most three leading spaces. A tab or four
        # spaces is literal/indented code, not a new speaker or fence delimiter.
        match = re.fullmatch(r" {0,3}(`{3,}|~{3,})(.*)", line.rstrip("\r\n"))
        if match is None or (match[1][0] == "`" and "`" in match[2]):
            return None
        return match[1][0], len(match[1])

    def flush() -> None:
        if current_role is not None:
            if len(records) >= MAX_MESSAGES:
                raise APIError(413, "too_many_import_messages", "Imports support at most 1000 messages.")
            records.append(_message(current_role, "".join(pieces), warnings))

    for line in content.splitlines(keepends=True):
        if fence is not None:
            pieces.append(line)
            # A shorter or differently typed delimiter cannot close this fence;
            # closing lines may contain only spaces/tabs after the marker run.
            if re.fullmatch(r" {0,3}" + re.escape(fence[0]) + "{" + str(fence[1])
                            + r",}[ \t]*", line.rstrip("\r\n")):
                fence = None
            continue
        starts = opening_fence(line)
        if starts is not None:
            pieces.append(line)
            fence = starts
            continue
        # Indented code cannot introduce role labels either. Keep source bytes,
        # including CRLF and code fences, inside their original speaker record.
        indentation = re.match(r"[ \t]*", line)[0]
        indented = len(indentation.expandtabs(4)) >= 4
        heading = MARKDOWN_ROLE.match(line) if markdown and not indented else None
        label = TEXT_ROLE.match(line) if heading is None and not indented else None
        match = heading or label
        if match:
            if not identified and pieces:
                # Preamble is not discarded or merged into a different speaker.
                current_role = "user"
                flush()
                _warning(warnings, "Text before the first role marker is an unpaired user record.")
            elif identified:
                flush()
            identified = True
            current_role = ROLE_NAMES[match.group(1).casefold()]
            pieces = [] if heading else [line[match.end():]]
            if label:
                fence = opening_fence(line[match.end():])
        else:
            pieces.append(line)
    if fence is not None:
        _warning(warnings, "An unclosed code fence is retained as literal content; role markers inside it are ignored.")
    if not identified:
        _warning(warnings, "Roles could not be identified; the original text is a single user "
                 "message, archived only unless paired with a genuine assistant record.")
        return [_message("user", content, warnings)]
    flush()
    return records


def parse_import(body: ImportInput) -> ParsedImport:
    try:
        encoded = body.content.encode("utf-8")
    except UnicodeEncodeError:
        raise _invalid("The import must contain valid Unicode text.") from None
    if len(encoded) > MAX_SOURCE_BYTES:
        raise APIError(413, "import_too_large", "Imports support at most 1 MiB of UTF-8 source.")
    if not body.content.strip():
        raise _invalid("The import is empty.")
    warnings: list[str] = []
    title = None
    allowed_pairs = None
    if body.format == "json":
        records, title, allowed_pairs = _json_records(body.content, warnings)
    else:
        records = _text_records(body.content, body.format == "markdown", warnings)
    messages = [record for record in records if record is not None]
    pairs = []
    paired_indices: set[int] = set()
    index = 0
    while index < len(records):
        current = records[index]
        following = records[index + 1] if index + 1 < len(records) else None
        if current is not None and following is not None and (
            current.role == "user" and following.role == "assistant"
            and (allowed_pairs is None or index in allowed_pairs)
        ):
            pairs.append((current, following))
            paired_indices.update((index, index + 1))
            index += 2
        else:
            index += 1
    archived = len(messages) - 2 * len(pairs)
    if archived:
        _warning(warnings, f"{archived} unpaired user/assistant message(s) will be archived only, "
                 "not included in the chat timeline or generation context; no missing reply is invented.")
    if not pairs:
        _warning(warnings, "No complete user/assistant pairs were found; commit creates an empty "
                 "chat timeline with the complete original source archive.")
    if title is None:
        first = next((message.content for message in messages if message.role == "user"), "导入的对话")
        title = next((line.strip() for line in first.splitlines() if line.strip()), "导入的对话")
    return ParsedImport(
        preview=ImportPreview(messages=messages, warnings=warnings, suggestedTitle=title[:200],
                              sourceHash=hashlib.sha256(encoded).hexdigest()),
        pairs=pairs, unpaired=[(index, record) for index, record in enumerate(records)
                               if record is not None and index not in paired_indices],
    )


class ImportRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def preview(self, body: ImportInput) -> ImportPreview:
        return parse_import(body).preview

    def source(self, user: str, conversation_id: str) -> ImportSource:
        self.db.conversation(user, conversation_id)
        row = self.db.conn.execute(
            "SELECT format, content, archivedMessages FROM history_imports "
            "WHERE ownerId = ? AND conversationId = ?", (user, conversation_id),
        ).fetchone()
        if row is None:
            raise APIError(404, "not_found", "Import source not found.")
        return ImportSource(**dict(row))

    def _insert_pair(
        self, conversation_id: str, pair: tuple[ImportMessage, ImportMessage], sequence: int,
        stamp: str,
    ) -> None:
        run_id, user_id, assistant_id = identifier(), identifier(), identifier()
        self.db.conn.execute(
            "INSERT INTO runs (id, conversationId, clientMessageId, userMessageId, "
            "assistantMessageId, status, errorCode, createdAt, updatedAt) "
            "VALUES (?, ?, ?, ?, ?, 'completed', NULL, ?, ?)",
            (run_id, conversation_id, identifier(), user_id, assistant_id, stamp, stamp),
        )
        for offset, message, message_id in ((1, pair[0], user_id), (2, pair[1], assistant_id)):
            self.db.conn.execute(
                "INSERT INTO messages "
                "(id, conversationId, runId, sequence, role, content, status, createdAt) "
                "VALUES (?, ?, ?, ?, ?, ?, 'completed', ?)",
                (message_id, conversation_id, run_id, sequence + offset,
                 message.role, message.content, stamp),
            )
        self.db._event(run_id, "run.completed", {
            "run": self.db.run_internal(run_id).model_dump(),
            "message": self.db.message(assistant_id).model_dump(),
        })

    def commit(self, user: str, body: ImportCommitInput) -> ImportCommitResult:
        # Reject an oversized source before hashing it; replay conflicts still
        # precede parsing so changed malformed JSON cannot reuse a committed ID.
        try:
            source_size = len(body.content.encode("utf-8"))
        except UnicodeEncodeError:
            raise _invalid("The import must contain valid Unicode text.") from None
        if source_size > MAX_SOURCE_BYTES:
            raise APIError(413, "import_too_large", "Imports support at most 1 MiB of UTF-8 source.")
        # Optional JSON string parameters must also be valid SQLite/UTF-8 text.
        try:
            for value in (body.title, body.characterId, body.coreMemory, body.relationship):
                if value is not None:
                    value.encode("utf-8")
        except UnicodeEncodeError:
            raise _invalid("Import parameters must contain valid Unicode text.") from None
        # exclude_unset also distinguishes omitted parameters from explicit defaults.
        request_hash = hashlib.sha256(json.dumps(
            body.model_dump(mode="json", exclude_unset=True), ensure_ascii=False,
            sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")).hexdigest()
        with atomic(self.db):
            previous = self.db.conn.execute(
                "SELECT * FROM history_imports WHERE ownerId = ? AND clientImportId = ?",
                (user, str(body.clientImportId)),
            ).fetchone()
            if previous is not None:
                if previous["requestHash"] != request_hash:
                    raise APIError(409, "idempotency_conflict", "Import ID has different source or parameters.")
                return ImportCommitResult(
                    conversation=self.db.conversation(user, previous["conversationId"]),
                    importedMessages=previous["importedMessages"],
                    archivedMessages=previous["archivedMessages"], replayed=True,
                )
            parsed = parse_import(body)
            memory_repo = MemoryRepository(self.db)
            if body.characterId is not None:
                memory_repo.character(user, body.characterId)
            stamp, conversation_id = now(), identifier()
            self.db.conn.execute("INSERT OR IGNORE INTO users VALUES (?, ?)", (user, stamp))
            self.db.conn.execute(
                "INSERT INTO conversations "
                "(id, ownerId, title, instructions, createdAt, updatedAt) VALUES (?, ?, ?, NULL, ?, ?)",
                (conversation_id, user, body.title if body.title is not None else parsed.preview.suggestedTitle,
                 stamp, stamp),
            )
            memory_repo.update_memory(user, conversation_id, MemoryUpdate(
                characterId=body.characterId, coreMemory=body.coreMemory, relationship=body.relationship,
            ))
            for index, pair in enumerate(parsed.pairs):
                self._insert_pair(conversation_id, pair, index * 2, stamp)
            for index, message in parsed.unpaired:
                self.db.conn.execute(
                    "INSERT INTO imports_unpaired (conversationId, sourceIndex, role, content) "
                    "VALUES (?, ?, ?, ?)", (conversation_id, index, message.role, message.content),
                )
            self.db.conn.execute(
                "INSERT INTO history_imports (ownerId, clientImportId, requestHash, conversationId, "
                "format, content, sourceHash, importedMessages, archivedMessages, createdAt) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (user, str(body.clientImportId), request_hash, conversation_id,
                 body.format, body.content, parsed.preview.sourceHash, len(parsed.pairs) * 2,
                 parsed.archived_messages, stamp),
            )
            return ImportCommitResult(
                conversation=self.db.conversation(user, conversation_id),
                importedMessages=len(parsed.pairs) * 2,
                archivedMessages=parsed.archived_messages, replayed=False,
            )


def imports_router(settings: Settings, current_user: Callable[..., str]) -> APIRouter:
    router = APIRouter(prefix="/api/v1", tags=["imports"])
    User = Annotated[str, Depends(current_user)]

    def repo(request: Request) -> ImportRepository:
        return ImportRepository(request.app.state.db)

    @router.post("/imports/preview", response_model=ImportPreview)
    async def preview(body: ImportInput, request: Request, user: User):
        return repo(request).preview(body)

    @router.post("/imports/commit", response_model=ImportCommitResult, status_code=201)
    async def commit(body: ImportCommitInput, request: Request, user: User):
        return repo(request).commit(user, body)

    @router.get("/conversations/{conversation_id}/import-source", response_model=ImportSource)
    async def import_source(conversation_id: str, request: Request, user: User):
        return repo(request).source(user, conversation_id)

    return router
