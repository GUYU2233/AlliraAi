"""Isolated administrator identity search and owner-bound device control regressions.

Only in-process TestClient, SQLite memory/temp databases and synthetic identities are
used. These tests never open a listening API or contact Logto or a model provider.
"""

import base64
import hashlib
import hmac
import json
import secrets
import sqlite3
import time
from uuid import uuid4

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from allira.admin import COOKIE_NAME, admin_router
from allira.admin_db import AdminRepository, token_hash
from allira.config import Settings
from allira.conversation_tools import ConversationToolsRepository
from allira.db import APIError, Database, now
from allira.oidc_identity import OidcIdentityRepository, _VerifiedIdentity
from allira.schemas import ConversationInput, RunInput

ORIGIN = "http://127.0.0.1:8000"
ISSUER = "http://127.0.0.1:3001/oidc"
OTHER_ISSUER = "http://127.0.0.1:3002/oidc"
PHONE = "+8613800138000"
USER_FIELDS = {
    "userId", "conversationCount", "runCount", "activeSessions", "createdAt",
    "accountType", "username", "displayName", "identityStatus", "maskedPhone",
    "phoneVerified", "passwordEnabled", "walletBalance",
}
DEVICE_FIELDS = {"id", "createdAt", "lastSeenAt", "expiresAt", "label", "type"}


def settings_for(path=":memory:", **changes):
    return Settings(
        _env_file=None, database_path=path, provider="mock", admin_enabled=True,
        allow_dev_auth=True, auth_backend="oidc", oidc_issuer_url=ISSUER,
        oidc_client_id="synthetic-admin-control-client",
        oidc_client_secret="synthetic-private-client-secret-never-returned",
        oidc_redirect_uri=ORIGIN + "/api/v1/auth/oidc/callback", **changes,
    )


def make_app(settings):
    app = FastAPI()
    app.state.db = Database(settings.database_path, settings)

    def error(status, code, message):
        return JSONResponse({"error": {"code": code, "message": message}}, status_code=status)

    @app.exception_handler(APIError)
    async def api_error(request, exc):
        return error(exc.status, exc.code, exc.message)

    @app.exception_handler(RequestValidationError)
    async def invalid(request, exc):
        return error(422, "validation_error", "Request parameters are invalid.")

    @app.exception_handler(HTTPException)
    async def http_error(request, exc):
        return error(exc.status_code, "http_error", "Request could not be processed.")

    @app.exception_handler(Exception)
    async def unexpected(request, exc):
        return error(500, "internal_error", "An internal error occurred.")

    @app.middleware("http")
    async def no_store(request: Request, call_next):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        return response

    app.include_router(admin_router(settings))
    return app


@pytest.fixture
def client():
    app = make_app(settings_for())
    with TestClient(app, base_url=ORIGIN, client=("127.0.0.1", 23456),
                    raise_server_exceptions=False) as client:
        yield client
    app.state.db.close()


def login(client):
    code = secrets.token_urlsafe(32)
    AdminRepository(client.app.state.db).issue_login(code, 300)
    response = client.post("/api/v1/admin/session", json={"code": code}, headers={"Origin": ORIGIN})
    assert response.status_code == 200, response.text
    return {"Origin": ORIGIN, "X-CSRF-Token": response.json()["csrfToken"]}


def account(db, username, *, display=None, phone=None, issuer=None, password=True):
    user = "acct_" + uuid4().hex
    stamp = now()
    with db.atomic():
        db.conn.execute("INSERT INTO users VALUES (?, ?)", (user, stamp))
        db.conn.execute(
            "INSERT INTO accounts (userId, username, displayName, passwordSalt, passwordHash, "
            "createdAt, termsVersion, isAdult, passwordEnabled) VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?)",
            (user, username, display or username, b"synthetic-salt!!", b"h" * 64, stamp,
             "2026-11-19", int(password)),
        )
        if phone is not None:
            db.conn.execute("INSERT INTO phone_phones VALUES (?, ?, ?)", (phone, user, stamp))
        if issuer is not None:
            db.conn.execute("INSERT INTO oidc_identities VALUES (?, ?, ?, ?)",
                            (issuer, "synthetic-subject-" + username, user, stamp))
    return user


def developer(db, user="developer_one"):
    with db.atomic():
        db.conn.execute("INSERT INTO users VALUES (?, ?)", (user, now()))
    return user


def device(db, user, *, kind="bearer", ttl=300, label="SDK", session_id=None):
    raw = secrets.token_urlsafe(32)
    db.create_session(raw, user, ttl, kind=kind, label=label)
    row = db.conn.execute("SELECT sessionId FROM sessions WHERE tokenHash = ?", (token_hash(raw),)).fetchone()
    if session_id is not None:
        with db.atomic():
            db.conn.execute("UPDATE sessions SET sessionId = ? WHERE tokenHash = ?", (session_id, token_hash(raw)))
    return session_id or row["sessionId"], raw


def rows(client, **params):
    response = client.get("/api/v1/admin/users", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def snapshot(db):
    tables = [row[0] for row in db.conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name",
    )]
    return {name: [tuple(row) for row in db.conn.execute(f'SELECT * FROM "{name}" ORDER BY rowid')]
            for name in tables}


def flow(db, user, phase="issued", *, purpose="login", session_token=None):
    repo = OidcIdentityRepository(db)
    browser = hashlib.sha256(secrets.token_bytes(32)).hexdigest()
    with db.atomic():
        db.conn.execute("INSERT INTO oidc_browsers VALUES (?, ?)", (browser, time.time() + 600))
    state, started = repo.begin(
        browser, db.settings, purpose=purpose, user=user if purpose == "erase" else None,
        session_hash=token_hash(session_token) if session_token is not None else None,
    )
    if phase != "issued":
        started = repo.exchanging(browser, state, db.settings)
    identity = db.conn.execute("SELECT issuer, subject FROM oidc_identities WHERE userId = ?", (user,)).fetchone()
    verified = _VerifiedIdentity(identity["issuer"], identity["subject"], PHONE, "Synthetic", time.time())
    if phase == "pending":
        repo.finish(started, verified, db.settings)
    return repo, browser, state, started, verified


def test_users_identity_summary_counts_and_no_credentials_or_mutations(client):
    db = client.app.state.db
    linked = account(db, "alpha_oidc", display="Alpha OIDC", phone=PHONE, issuer=ISSUER, password=False)
    legacy = account(db, "alpha_legacy", display="Alpha Legacy")
    foreign = account(db, "foreign_issuer", issuer=OTHER_ISSUER, password=False)
    dev = developer(db)
    device(db, linked)
    device(db, linked, kind="browser")
    device(db, legacy)
    device(db, dev)
    device(db, linked, ttl=-1)
    conversation = db.create_conversation(linked, ConversationInput())
    run, _ = db.create_run(linked, conversation.id, RunInput(clientMessageId=uuid4(), content="synthetic"), 120000)
    db.finish(run.id, "completed")
    login(client)
    before = snapshot(db)
    page = rows(client)
    after = snapshot(db)
    assert after == before and not db.conn.in_transaction
    assert page["nextCursor"] is None
    result = {item["userId"]: item for item in page["items"]}
    assert set(result) == {linked, legacy, foreign, dev}
    assert all(set(item) == USER_FIELDS for item in result.values())
    assert result[linked] == {
        "userId": linked, "conversationCount": 1, "runCount": 1, "activeSessions": 2,
        "createdAt": db.conn.execute("SELECT createdAt FROM users WHERE id = ?", (linked,)).fetchone()[0],
        "accountType": "registered", "username": "alpha_oidc", "displayName": "Alpha OIDC",
        "identityStatus": "oidc", "maskedPhone": "+86 138****8000", "phoneVerified": True,
        "passwordEnabled": False, "walletBalance": 99,
    }
    assert result[legacy]["identityStatus"] == "legacy" and result[legacy]["passwordEnabled"] is True
    assert result[foreign]["identityStatus"] == "legacy"
    assert result[foreign]["walletBalance"] is None
    assert result[dev]["accountType"] == result[dev]["identityStatus"] == "developer"
    assert result[dev]["username"] is result[dev]["displayName"] is result[dev]["maskedPhone"] is None
    assert result[dev]["phoneVerified"] is result[dev]["passwordEnabled"] is False
    text = json.dumps(page)
    for secret in (PHONE, ISSUER, OTHER_ISSUER, "synthetic-subject", "tokenHash", "passwordHash", "passwordSalt"):
        assert secret not in text
    assert client.get("/api/v1/admin/users").headers["cache-control"] == "no-store"


@pytest.mark.parametrize("identity,expected", [("all", 4), ("oidc", 1), ("legacy", 2), ("developer", 1)])
def test_identity_filters_use_only_current_issuer(client, identity, expected):
    db = client.app.state.db
    account(db, "one", issuer=ISSUER)
    account(db, "two", issuer=OTHER_ISSUER)
    account(db, "three")
    developer(db)
    login(client)
    page = rows(client, identity=identity)
    assert len(page["items"]) == expected
    if identity != "all":
        assert {item["identityStatus"] for item in page["items"]} == {identity}


@pytest.mark.parametrize("query,expected", [
    ("  Alpha  ", {"alpha_name"}), ("ALPHA", {"alpha_name"}),
    ("友好", {"friendly_name"}), ("100%", {"literal_name"}),
    ("x_y", {"literal_name"}), ("\\path", {"literal_name"}),
    ("%", {"literal_name"}), ("_", {"alpha_name", "friendly_name", "literal_name"}),
    ("not found", set()), (PHONE, set()), ("13800138000", set()),
    ("synthetic-subject", set()), ("' OR 1=1 --", set()),
])
def test_search_is_literal_only_public_identity_columns(client, query, expected):
    db = client.app.state.db
    account(db, "alpha_name", display="Alpha")
    account(db, "friendly_name", display="友好称呼")
    account(db, "literal_name", display="100% x_y \\path", phone=PHONE, issuer=ISSUER)
    login(client)
    assert {item["username"] for item in rows(client, q=query)["items"]} == expected


def test_search_matches_user_id_and_whitespace_becomes_legacy_unfiltered_cursor(client):
    db = client.app.state.db
    developer(db, "a-developer")
    developer(db, "b-developer")
    login(client)
    assert rows(client, q="a-developer")["items"][0]["userId"] == "a-developer"
    page = rows(client, q="   ", limit=1)
    assert page["nextCursor"] == "a-developer"
    assert rows(client, cursor=page["nextCursor"], limit=1)["items"][0]["userId"] == "b-developer"


@pytest.mark.parametrize("query", ["x" * 101, "\U0001f600" * 101, "\x00", "\nname", "name\t", "\u200b", "\u202e"])
def test_query_bounds_and_control_characters_are_generic_validation(client, query):
    login(client)
    before = snapshot(client.app.state.db)
    response = client.get("/api/v1/admin/users", params={"q": query})
    assert response.status_code == 422
    assert response.json() == {"error": {"code": "validation_error", "message": "Request parameters are invalid."}}
    assert snapshot(client.app.state.db) == before


def test_query_exact_unicode_bounds_and_surrogate_rejected_by_repository(client):
    login(client)
    assert rows(client, q="\U0001f600" * 100)["items"] == []
    assert rows(client, q=" " + "x" * 100 + " ")["items"] == []
    with pytest.raises(APIError) as error:
        AdminRepository(client.app.state.db).list_users(None, 50, "\ud800")
    assert error.value.status == 422


@pytest.mark.parametrize("params", [
    {"identity": "registered"}, {"identity": "OIDC"}, {"limit": 0}, {"limit": 101},
    {"cursor": "a" * 2049},
])
def test_list_query_parameter_bounds(client, params):
    login(client)
    assert client.get("/api/v1/admin/users", params=params).status_code == 422


def test_filtered_cursor_binds_query_filter_issuer_and_is_tamper_proof(client):
    db = client.app.state.db
    developer(db, "search-a")
    developer(db, "search-b")
    developer(db, "search-c")
    login(client)
    first = rows(client, q=" search ", identity="developer", limit=1)
    cursor = first["nextCursor"]
    assert cursor and cursor != "search-a"
    assert rows(client, q="search", identity="developer", limit=2, cursor=cursor)["items"][0]["userId"] == "search-b"
    for params in (
        {"q": "search-b", "identity": "developer", "cursor": cursor},
        {"q": "search", "identity": "all", "cursor": cursor},
        {"q": "search", "identity": "developer", "cursor": "search-a"},
        {"q": "search", "identity": "developer", "cursor": cursor[:-2] + "xx"},
        {"q": "search", "identity": "developer", "cursor": "not.a.cursor"},
        {"cursor": cursor},
    ):
        response = client.get("/api/v1/admin/users", params=params)
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "invalid_cursor"
    old_issuer = db.settings.oidc_issuer_url
    db.settings.oidc_issuer_url = OTHER_ISSUER
    assert client.get("/api/v1/admin/users", params={"q": "search", "identity": "developer", "cursor": cursor}).status_code == 400
    db.settings.oidc_issuer_url = old_issuer


def test_filtered_keyset_survives_anchor_deletion_and_restart(tmp_path):
    path = str(tmp_path / "admin-control.sqlite3")
    db = Database(path, settings_for(path))
    developer(db, "anchor-a")
    developer(db, "anchor-b")
    repository = AdminRepository(db)
    cursor = repository.list_users(None, 1, "anchor", "developer")["nextCursor"]
    with db.atomic():
        db.conn.execute("DELETE FROM users WHERE id = 'anchor-a'")
    db.close()
    db = Database(path, settings_for(path))
    assert AdminRepository(db).list_users(cursor, 1, "anchor", "developer")["items"][0]["userId"] == "anchor-b"
    assert not db.conn.in_transaction
    db.close()


def test_search_cursor_is_domain_separated_from_conversation_cursor(client):
    db = client.app.state.db
    owner = developer(db, "search-owner")
    conversation = db.create_conversation(owner, ConversationInput())
    tools = ConversationToolsRepository(db)
    foreign = tools._encode_cursor(owner, "search", conversation)
    login(client)
    assert client.get("/api/v1/admin/users", params={"q": "search", "cursor": foreign}).status_code == 400
    key = bytes.fromhex(db.conn.execute("SELECT value FROM conversation_tool_state WHERE name = 'cursor_hmac_key'").fetchone()[0])
    payload = json.dumps({"v": 1, "q": hashlib.sha256(b"search").hexdigest(), "f": "all",
                          "s": hashlib.sha256(ISSUER.encode()).hexdigest(), "i": owner},
                         sort_keys=True, separators=(",", ":")).encode()
    bare_signature = hmac.digest(key, payload, "sha256")
    unsigned_domain = base64.urlsafe_b64encode(payload).rstrip(b"=").decode() + "." + base64.urlsafe_b64encode(bare_signature).rstrip(b"=").decode()
    assert client.get("/api/v1/admin/users", params={"q": "search", "cursor": unsigned_domain}).status_code == 400


def test_user_and_device_empty_lists_and_missing_owner(client):
    login(client)
    assert rows(client) == {"items": [], "nextCursor": None}
    user = developer(client.app.state.db)
    assert client.get(f"/api/v1/admin/users/{user}/sessions").json() == {"items": [], "nextCursor": None}
    assert client.get("/api/v1/admin/users/missing/sessions").status_code == 404
    assert client.post(f"/api/v1/admin/users/missing/sessions/{uuid4()}/revoke", headers=login(client)).status_code == 404


def test_device_session_metadata_expiry_and_stable_owner_pagination(client):
    db = client.app.state.db
    owner = account(db, "devices", issuer=ISSUER)
    other = account(db, "foreign_devices", issuer=ISSUER)
    first = "00000000-0000-4000-8000-000000000001"
    second = "00000000-0000-4000-8000-000000000002"
    third = "00000000-0000-4000-8000-000000000003"
    device(db, owner, kind="browser", label="\n Browser\x00 ", session_id=second)
    device(db, owner, label="SDK", session_id=first)
    device(db, owner, label="Z" * 100, session_id=third)
    foreign, _ = device(db, other)
    expired, _ = device(db, owner, ttl=-1)
    login(client)
    before = snapshot(db)
    endpoint = f"/api/v1/admin/users/{owner}/sessions"
    page = client.get(endpoint, params={"limit": 1}).json()
    assert len(page["items"]) == 1 and page["nextCursor"] == first
    assert set(page["items"][0]) == DEVICE_FIELDS
    assert page["items"][0]["id"] == first and page["items"][0]["type"] == "bearer"
    assert page["items"][0]["expiresAt"].endswith("Z")
    tail = client.get(endpoint, params={"cursor": first, "limit": 2}).json()
    assert [item["id"] for item in tail["items"]] == [second, third]
    assert tail["items"][0]["label"] == "Browser" and len(tail["items"][1]["label"]) == 80
    assert tail["nextCursor"] is None
    assert snapshot(db) == before
    for cursor in (foreign, expired, str(uuid4()), "garbage", ""):
        assert client.get(endpoint, params={"cursor": cursor}).status_code == 400
    for params in ({"limit": 0}, {"limit": 101}, {"cursor": "x" * 101}):
        assert client.get(endpoint, params=params).status_code == 422
    text = json.dumps(page) + json.dumps(tail)
    assert "current" not in text and "tokenHash" not in text and "userId" not in text


@pytest.mark.parametrize("kind", ["bearer", "browser"])
def test_single_device_revocation_keeps_other_sessions_and_only_owner_audit(client, kind):
    db = client.app.state.db
    owner = account(db, "revoke_one", phone=PHONE, issuer=ISSUER)
    other = account(db, "untouched", issuer=ISSUER)
    selected, token = device(db, owner, kind=kind)
    _, retained = device(db, owner, kind="browser" if kind == "bearer" else "bearer")
    _, foreign_token = device(db, other)
    headers = login(client)
    response = client.post(f"/api/v1/admin/users/{owner}/sessions/{selected}/revoke", headers=headers)
    assert response.status_code == 200 and response.json() == {"ok": True, "revoked": 1}
    assert db.conn.execute("SELECT 1 FROM sessions WHERE tokenHash = ?", (token_hash(token),)).fetchone() is None
    with pytest.raises(APIError):
        db.authenticate(token, kind=kind)
    assert db.authenticate(retained, kind="browser" if kind == "bearer" else "bearer") == owner
    assert db.authenticate(foreign_token) == other
    audit = db.conn.execute("SELECT * FROM admin_audit WHERE action = 'user.session_revoked'").fetchone()
    assert audit["targetType"] == "user" and audit["targetId"] == audit["ownerId"] == owner
    assert selected not in str(tuple(audit)) and PHONE not in str(tuple(audit))
    assert db.conn.execute("SELECT revokedRevision FROM oidc_user_revocations WHERE userId = ?", (owner,)).fetchone()[0] == 1
    assert db.conn.execute("SELECT 1 FROM oidc_user_revocations WHERE userId = ?", (other,)).fetchone() is None
    assert client.get("/api/v1/admin/session").status_code == 200
    assert not db.conn.in_transaction


@pytest.mark.parametrize("target", ["unknown", "foreign", "expired", "already_revoked"])
def test_noop_single_revocation_has_no_revision_audit_or_other_changes(client, target):
    db = client.app.state.db
    owner = account(db, "noop_owner", issuer=ISSUER)
    other = account(db, "noop_other", issuer=ISSUER)
    device(db, owner)
    foreign, _ = device(db, other)
    expired, _ = device(db, owner, ttl=-1)
    target_id = {"unknown": str(uuid4()), "foreign": foreign, "expired": expired,
                 "already_revoked": str(uuid4())}[target]
    headers = login(client)
    before = snapshot(db)
    response = client.post(f"/api/v1/admin/users/{owner}/sessions/{target_id}/revoke", headers=headers)
    assert response.json() == {"ok": True, "revoked": 0}
    assert snapshot(db) == before and not db.conn.in_transaction


def test_repeated_revoke_is_zero_and_does_not_tick_again(client):
    db = client.app.state.db
    owner = account(db, "repeat_owner", issuer=ISSUER)
    selected, _ = device(db, owner)
    headers = login(client)
    endpoint = f"/api/v1/admin/users/{owner}/sessions/{selected}/revoke"
    assert client.post(endpoint, headers=headers).json()["revoked"] == 1
    before = snapshot(db)
    assert client.post(endpoint, headers=headers).json() == {"ok": True, "revoked": 0}
    assert snapshot(db) == before


@pytest.mark.parametrize("phase", ["issued", "exchanging", "pending"])
def test_single_device_revocation_blocks_old_oidc_inflight_and_preserves_other_owner(client, phase):
    db = client.app.state.db
    owner = account(db, "inflight_owner", phone=PHONE, issuer=ISSUER)
    other = account(db, "inflight_other", issuer=ISSUER)
    selected, _ = device(db, owner)
    _, retained = device(db, owner)
    own_repo, own_browser, own_state, own_started, own_verified = flow(db, owner, phase)
    other_repo, other_browser, other_state, other_started, other_verified = flow(db, other, phase)
    headers = login(client)
    response = client.post(f"/api/v1/admin/users/{owner}/sessions/{selected}/revoke", headers=headers)
    assert response.json()["revoked"] == 1
    with pytest.raises(APIError) as error:
        if phase == "pending":
            own_repo.pending(own_browser)
        else:
            if phase == "issued":
                own_started = own_repo.exchanging(own_browser, own_state, db.settings)
            own_repo.finish(own_started, own_verified, db.settings)
    assert error.value.status == 401
    if phase != "pending":
        if phase == "issued":
            other_started = other_repo.exchanging(other_browser, other_state, db.settings)
        other_repo.finish(other_started, other_verified, db.settings)
    assert other_repo.pending(other_browser)["subject"] == other_verified.subject
    assert db.authenticate(retained) == owner
    # A genuinely fresh post-revocation flow is still usable.
    _, fresh_browser, _, _, _ = flow(db, owner, "pending")
    assert own_repo.pending(fresh_browser)["subject"] == own_verified.subject
    assert db.conn.execute("PRAGMA foreign_key_check").fetchall() == []


@pytest.mark.parametrize("phase", ["issued", "exchanging", "pending"])
def test_revoking_different_device_invalidates_old_fresh_erase_proof(client, phase):
    db = client.app.state.db
    owner = account(db, "erase_owner", phone=PHONE, issuer=ISSUER)
    selected, _ = device(db, owner)
    _, browser_token = device(db, owner, kind="browser")
    repo, browser, state, previous, verified = flow(db, owner, phase, purpose="erase", session_token=browser_token)
    headers = login(client)
    assert client.post(f"/api/v1/admin/users/{owner}/sessions/{selected}/revoke", headers=headers).json()["revoked"] == 1
    assert db.authenticate(browser_token, kind="browser") == owner
    with pytest.raises(APIError):
        if phase == "pending":
            repo.pending(browser)
        else:
            if phase == "issued":
                previous = repo.exchanging(browser, state, db.settings)
            repo.finish(previous, verified, db.settings)
    assert db.conn.execute("SELECT 1 FROM accounts WHERE userId = ?", (owner,)).fetchone()


@pytest.mark.parametrize("failure", ["proofs", "delete", "audit"])
def test_single_revocation_rolls_back_all_proofs_sessions_and_audit_on_failure(client, monkeypatch, failure):
    db = client.app.state.db
    owner = account(db, "rollback_owner", phone=PHONE, issuer=ISSUER)
    selected, token = device(db, owner)
    flow(db, owner, "pending")
    repository = AdminRepository(db)
    if failure == "proofs":
        from allira import identity_lifecycle
        original = identity_lifecycle.revoke_identity_proofs

        def fail_proofs(database, user):
            original(database, user)
            raise RuntimeError("synthetic failure after proof mutation")

        monkeypatch.setattr(identity_lifecycle, "revoke_identity_proofs", fail_proofs)
    elif failure == "delete":
        db.conn.executescript("CREATE TRIGGER reject_device_delete BEFORE DELETE ON sessions BEGIN SELECT RAISE(ABORT, 'synthetic delete fault'); END;")
    else:
        def fail_audit(*args):
            raise RuntimeError("synthetic audit failure")

        monkeypatch.setattr(repository, "audit", fail_audit)
    before = snapshot(db)
    with pytest.raises((RuntimeError, sqlite3.IntegrityError)):
        repository.revoke_user_session(owner, selected)
    assert snapshot(db) == before
    assert db.authenticate(token) == owner
    assert not db.conn.in_transaction and db.conn.execute("PRAGMA foreign_key_check").fetchall() == []


def test_revoke_all_existing_semantics_are_retained(client):
    db = client.app.state.db
    owner = account(db, "all_owner", issuer=ISSUER)
    other = account(db, "all_other", issuer=ISSUER)
    device(db, owner)
    device(db, owner, kind="browser")
    _, foreign_token = device(db, other)
    device(db, owner, ttl=-1)
    headers = login(client)
    assert client.post(f"/api/v1/admin/users/{owner}/revoke-sessions", headers=headers).json() == {"ok": True, "revoked": 3}
    assert db.authenticate(foreign_token) == other
    assert db.conn.execute("SELECT action FROM admin_audit WHERE action = 'user.sessions_revoked'").fetchone()


@pytest.mark.parametrize("credentials", ["developer", "ordinary_bearer", "ordinary_browser", "oidc_pending", "m2m"])
def test_ordinary_identity_and_management_like_bearers_are_never_admin(client, credentials):
    db = client.app.state.db
    owner = account(db, "not_admin", phone=PHONE, issuer=ISSUER)
    selected, raw = device(db, owner)
    headers = {}
    if credentials == "developer":
        dev = developer(db, "admin")
        _, raw = device(db, dev)
        headers["Authorization"] = "Bearer " + raw
    elif credentials == "ordinary_bearer":
        headers["Authorization"] = "Bearer " + raw
    elif credentials == "ordinary_browser":
        _, raw = device(db, owner, kind="browser")
        client.cookies.set("allira_browser", raw)
    elif credentials == "oidc_pending":
        _, browser, _, _, _ = flow(db, owner, "pending")
        client.cookies.set("allira_oidc", browser)
    else:
        headers["Authorization"] = "Bearer synthetic-logto-management-m2m-access-token"
    before = snapshot(db)
    for path in ("/api/v1/admin/users", f"/api/v1/admin/users/{owner}/sessions"):
        assert client.get(path, headers=headers).status_code == 401
    assert client.post(f"/api/v1/admin/users/{owner}/sessions/{selected}/revoke", headers={**headers, "Origin": ORIGIN}).status_code == 401
    assert snapshot(db) == before


@pytest.mark.parametrize("bad", [
    {}, {"Origin": ORIGIN}, {"Origin": ORIGIN, "X-CSRF-Token": "wrong"},
    {"Origin": "http://attacker.invalid"}, {"Sec-Fetch-Site": "cross-site"},
    {"Host": "attacker.invalid"}, {"Host": "localhost.attacker.invalid:8000"},
])
def test_new_mutations_retain_independent_csrf_origin_and_host_boundaries(client, bad):
    db = client.app.state.db
    owner = account(db, "protected_owner", issuer=ISSUER)
    selected, _ = device(db, owner)
    headers = login(client)
    submitted = {**bad}
    if "Origin" in bad and bad["Origin"] != ORIGIN:
        submitted["X-CSRF-Token"] = headers["X-CSRF-Token"]
    before = snapshot(db)
    assert client.post(f"/api/v1/admin/users/{owner}/sessions/{selected}/revoke", headers=submitted).status_code == 403
    assert snapshot(db) == before


def test_nonloopback_peer_forwarding_cannot_read_or_revoke(tmp_path):
    settings = settings_for(str(tmp_path / "nonloopback.sqlite3"))
    app = make_app(settings)
    db = app.state.db
    owner = account(db, "peer_owner", issuer=ISSUER)
    selected, _ = device(db, owner)
    raw = secrets.token_urlsafe(32)
    repository = AdminRepository(db)
    repository.issue_login("x" * 32, 300)
    repository.exchange_login("x" * 32, raw, 300)
    with TestClient(app, base_url=ORIGIN, client=("10.0.0.2", 1234)) as client:
        client.cookies.set(COOKIE_NAME, raw)
        headers = {"X-Forwarded-For": "127.0.0.1", "Origin": ORIGIN}
        before = snapshot(db)
        assert client.get("/api/v1/admin/users", headers=headers).status_code == 403
        assert client.get(f"/api/v1/admin/users/{owner}/sessions", headers=headers).status_code == 403
        assert client.post(f"/api/v1/admin/users/{owner}/sessions/{selected}/revoke", headers=headers).status_code == 403
        assert snapshot(db) == before
    db.close()


@pytest.mark.parametrize("session_id", ["missing", "not-a-uuid", "1" * 200])
def test_session_path_uuid_validation_is_generic_and_has_no_mutation(client, session_id):
    db = client.app.state.db
    owner = developer(db)
    headers = login(client)
    before = snapshot(db)
    response = client.post(f"/api/v1/admin/users/{owner}/sessions/{session_id}/revoke", headers=headers)
    assert response.status_code == 422 and response.json()["error"]["code"] == "validation_error"
    assert snapshot(db) == before


def test_uppercase_uuid_path_selects_same_device(client):
    db = client.app.state.db
    owner = developer(db)
    selected, _ = device(db, owner)
    headers = login(client)
    response = client.post(f"/api/v1/admin/users/{owner}/sessions/{selected.upper()}/revoke", headers=headers)
    assert response.json() == {"ok": True, "revoked": 1}


def test_openapi_declares_only_bounded_identity_and_device_dtos(client):
    document = client.app.openapi()
    fields = document["components"]["schemas"]
    assert set(fields["AdminUser"]["properties"]) == USER_FIELDS
    assert set(fields["AdminDeviceSession"]["properties"]) == DEVICE_FIELDS
    parameters = document["paths"]["/api/v1/admin/users"]["get"]["parameters"]
    identity = next(item for item in parameters if item["name"] == "identity")
    assert set(identity["schema"]["enum"]) == {"all", "oidc", "legacy", "developer"}
    endpoint = document["paths"]["/api/v1/admin/users/{user_id}/sessions/{session_id}/revoke"]["post"]
    session = next(item for item in endpoint["parameters"] if item["name"] == "session_id")
    assert session["schema"]["format"] == "uuid"
