"""Account export/erasure: owner isolation, no secrets, rollback and closing-task guards."""

import hmac
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from allira import accounts, data_controls
from allira.admin_db import AdminRepository
from allira.app import create_app
from allira.config import Settings
from allira.conversation_tools import ConversationForkInput, ConversationToolsRepository
from allira.data_controls import AccountDataRepository
from allira.economy import CdkIssue, EnergyRepository
from allira.schemas import ConversationInput, RunInput

PASSWORD = "private-account-password-123"
ORIGIN = "http://127.0.0.1:8000"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(accounts, "_derive_password", lambda password, salt: hmac.digest(
        salt, password.get_secret_value().encode(), "sha256",
    ) * 2)
    settings = Settings(_env_file=None, database_path=str(tmp_path / "account-data.sqlite3"),
                        provider="mock", fast_upstream_model="", mock_chunk_delay_seconds=0,
                        allow_dev_auth=True, admin_enabled=True)
    with TestClient(create_app(settings), base_url=ORIGIN, client=("127.0.0.1", 10)) as client:
        yield client


def register(client, name):
    response = client.post("/api/v1/auth/register", json={
        "username": name, "password": PASSWORD, "acceptedTermsVersion": "2026-11-19", "isAdult": True,
    })
    assert response.status_code == 201
    account = response.json()
    return account, {"Authorization": "Bearer " + account["accessToken"]}


def populate(client, user):
    db = client.app.state.db
    chat = db.create_conversation(user, ConversationInput(title="private archive 你好"))
    run, _ = db.create_run(user, chat.id, RunInput(clientMessageId=uuid4(), content="original-private-input"), 120000)
    db.append_delta(run.id, "private-answer")
    db.finish(run.id, "completed")
    return chat, run


def erase(client, headers, *, password=PASSWORD, confirmation="DELETE MY ACCOUNT"):
    return client.post("/api/v1/account/erase", headers=headers, json={
        "currentPassword": password, "confirmation": confirmation,
    })


def test_account_archive_is_complete_owner_only_and_excludes_all_credentials(client):
    first, headers = register(client, "archive_owner")
    other, _ = register(client, "archive_other")
    chat, run = populate(client, first["user"]["id"])
    populate(client, other["user"]["id"])
    recovery = client.post("/api/v1/account/recovery", headers=headers,
                           json={"currentPassword": PASSWORD}).json()["codes"]
    response = client.get("/api/v1/account/export", headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert body["format"] == "allira.account.v1" and body["user"] == first["user"]
    assert len(body["conversations"]) == 1 and body["conversations"][0]["conversation"]["id"] == chat.id
    assert [message["content"] for message in body["conversations"][0]["messages"]] == ["original-private-input", "private-answer"]
    assert body["wallet"]["balance"] == 99 and body["usage"][0]["runId"] == run.id
    assert len(body["ledger"]) == 2 and body["warnings"]
    assert response.headers["cache-control"] == "no-store"
    for secret in [PASSWORD, first["accessToken"], *recovery, other["user"]["id"],
                   "passwordHash", "passwordSalt", "tokenHash", "codeHash", "cursorKey"]:
        assert secret not in response.text
    db = client.app.state.db
    assert not db.conn.in_transaction
    assert db.conn.execute("PRAGMA foreign_key_check").fetchall() == []


def test_empty_export_does_not_issue_welcome_energy(client):
    first, headers = register(client, "empty_archive")
    response = client.get("/api/v1/account/export", headers=headers)
    assert response.status_code == 200 and response.json()["wallet"] is None
    assert response.json()["ledger"] == [] and response.json()["usage"] == []
    assert client.app.state.db.conn.execute("SELECT COUNT(*) FROM energy_wallets WHERE userId = ?",
                                             (first["user"]["id"],)).fetchone()[0] == 0


@pytest.mark.parametrize("limit", ["MAX_ACCOUNT_EXPORT_BYTES", "MAX_ACCOUNT_EXPORT_RECORDS", "MAX_ACCOUNT_EXPORT_CONVERSATIONS"])
def test_export_limits_refuse_without_truncating_or_writing(client, monkeypatch, limit):
    first, headers = register(client, "archive_limits")
    populate(client, first["user"]["id"])
    before = client.app.state.db.conn.total_changes
    monkeypatch.setattr(data_controls, limit, 0)
    response = client.get("/api/v1/account/export", headers=headers)
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "account_export_too_large"
    assert "original-private-input" not in response.text
    assert client.app.state.db.conn.total_changes == before


def test_export_and_erase_require_registered_account(client):
    developer = client.post("/api/v1/dev/session", json={"userId": "developer"}).json()
    headers = {"Authorization": "Bearer " + developer["accessToken"]}
    assert client.get("/api/v1/account/export", headers=headers).status_code == 403
    assert erase(client, headers).status_code == 403
    assert client.get("/api/v1/account/export").status_code == 401


def test_erasure_removes_personal_data_sessions_and_forks_but_cdk_stays_consumed(client):
    first, headers = register(client, "erase_owner")
    user = first["user"]["id"]
    other, other_headers = register(client, "erase_other")
    chat, run = populate(client, user)
    populate(client, other["user"]["id"])
    db, settings = client.app.state.db, Settings(_env_file=None)
    db.create_session("extra-device", user, 300, kind="browser")
    codes = client.post("/api/v1/account/recovery", headers=headers,
                        json={"currentPassword": PASSWORD}).json()["codes"]
    repository = EnergyRepository(db, settings)
    cdk = repository.issue(CdkIssue(energy=7))[0]
    repository.redeem(user, cdk.code)
    tools = ConversationToolsRepository(db)
    tools.fork(user, chat.id, ConversationForkInput(clientForkId=uuid4()))
    AdminRepository(db).audit("messages.viewed", "conversation", chat.id)
    character = client.post("/api/v1/characters", headers=headers, json={
        "name": "private", "description": "private role", "instructions": "private notes",
    }).json()
    response = erase(client, headers)
    assert response.status_code == 200 and response.json() == {"ok": True}
    assert "Max-Age=0" in response.headers["set-cookie"]
    for table, column in (("accounts", "userId"), ("users", "id"), ("sessions", "userId"),
                          ("energy_wallets", "userId"), ("energy_ledger", "userId"),
                          ("run_usage", "userId"), ("run_rate_attempts", "userId"),
                          ("conversations", "ownerId"), ("characters", "ownerId"),
                          ("conversation_forks", "ownerId"), ("account_recovery_codes", "userId"),
                          ("account_recovery_state", "userId")):
        assert db.conn.execute(f"SELECT COUNT(*) FROM {table} WHERE {column} = ?", (user,)).fetchone()[0] == 0
    assert db.conn.execute("SELECT COUNT(*) FROM runs WHERE id = ?", (run.id,)).fetchone()[0] == 0
    assert db.conn.execute("SELECT COUNT(*) FROM admin_audit WHERE targetId = ?", (chat.id,)).fetchone()[0] == 0
    assert db.conn.execute("SELECT COUNT(*) FROM characters WHERE id = ?", (character["id"],)).fetchone()[0] == 0
    row = db.conn.execute("SELECT * FROM energy_cdks WHERE prefix = ?", (cdk.prefix,)).fetchone()
    assert row["redeemedBy"].startswith("erased_") and row["redeemedBy"] != user
    assert row["redeemedAt"] is not None
    assert client.post("/api/v1/energy/redeem", headers=other_headers, json={"code": cdk.code}).status_code == 409
    assert client.get("/api/v1/account", headers=headers).status_code == 401
    assert client.post("/api/v1/auth/recover", json={
        "username": first["user"]["username"], "recoveryCode": codes[0], "newPassword": "replacement-password-456",
    }).status_code == 401
    assert client.get("/api/v1/account", headers=other_headers).status_code == 200
    assert db.conn.execute("PRAGMA foreign_key_check").fetchall() == []


def test_erasure_removes_audit_links_for_previously_deleted_paid_conversations(client):
    first, headers = register(client, "erase_old_metadata")
    user = first["user"]["id"]
    chat, run = populate(client, user)
    db = client.app.state.db
    admin = AdminRepository(db)
    admin.audit("run.cancelled", "run", run.id)
    db.delete_conversation(user, chat.id, admin=True)
    assert db.conn.execute("SELECT COUNT(*) FROM admin_audit WHERE targetId IN (?, ?)",
                           (chat.id, run.id)).fetchone()[0] == 2
    assert erase(client, headers).status_code == 200
    assert db.conn.execute("SELECT COUNT(*) FROM admin_audit WHERE targetId IN (?, ?)",
                           (chat.id, run.id)).fetchone()[0] == 0


def test_erasure_removes_deleted_uncharged_companion_audit_but_keeps_other_owner(client):
    first, headers = register(client, "erase_empty_audit")
    other, other_headers = register(client, "keep_empty_audit")
    chats = []
    for auth in (headers, other_headers):
        response = client.post("/api/v1/companion/conversations", headers=auth,
                               json={"clientSetupId": str(uuid4())})
        assert response.status_code == 201
        chats.append(response.json()["conversation"]["id"])
    db = client.app.state.db
    admin = AdminRepository(db)
    for conversation_id in chats:
        assert admin.list_messages(conversation_id, None, 50)["items"] == []
        admin.delete_conversation(conversation_id)
    assert db.conn.execute("SELECT COUNT(*) FROM run_usage").fetchone()[0] == 0
    assert db.conn.execute("SELECT COUNT(*) FROM energy_wallets").fetchone()[0] == 0
    assert db.conn.execute("SELECT COUNT(*) FROM admin_audit WHERE ownerId = ?",
                           (first["user"]["id"],)).fetchone()[0] == 2
    public_audit = admin.list_audit(None, 50)["items"]
    assert all("ownerId" not in item for item in public_audit)
    other_before = [dict(row) for row in db.conn.execute(
        "SELECT * FROM admin_audit WHERE ownerId = ? ORDER BY id", (other["user"]["id"],),
    )]
    assert len(other_before) == 2
    assert erase(client, headers).status_code == 200
    assert db.conn.execute("SELECT COUNT(*) FROM admin_audit WHERE targetId = ?",
                           (chats[0],)).fetchone()[0] == 0
    assert [dict(row) for row in db.conn.execute(
        "SELECT * FROM admin_audit WHERE ownerId = ? ORDER BY id", (other["user"]["id"],),
    )] == other_before
    assert client.get("/api/v1/account", headers=other_headers).status_code == 200
    assert db.conn.execute("PRAGMA foreign_key_check").fetchall() == []


@pytest.mark.parametrize("known_user_owner", [False, True])
def test_erasure_audit_fallback_respects_type_and_existing_owner(client, known_user_owner):
    first, headers = register(client, "erase_type_collision")
    user = first["user"]["id"]
    db = client.app.state.db
    chat = db.create_conversation(user, ConversationInput())
    # A development user may legitimately have the same UUID as a conversation.
    other_user = chat.id
    other_token = "synthetic-other-device-session"
    db.create_session(other_token, other_user, 3600)
    admin = AdminRepository(db)
    with db.atomic():
        admin.audit("user.sessions_revoked", "user", other_user)
    if not known_user_owner:
        with db.atomic():
            db.conn.execute("UPDATE admin_audit SET ownerId = NULL WHERE targetType = 'user'")
    with db.atomic():
        db.conn.executemany(
            "INSERT INTO admin_audit (action, targetType, targetId, createdAt, ownerId) "
            "VALUES (?, ?, ?, ?, ?)",
            [
                ("known_other_owner", "conversation", chat.id, "2026-11-19T00:00:00Z", other_user),
                ("unknown_type", "other", chat.id, "2026-11-19T00:00:00Z", None),
                ("legacy_own_conversation", "conversation", chat.id, "2026-11-19T00:00:00Z", None),
                ("known_own_conversation", "conversation", chat.id, "2026-11-19T00:00:00Z", user),
            ],
        )
    before = [dict(row) for row in db.conn.execute(
        "SELECT * FROM admin_audit WHERE action IN "
        "('user.sessions_revoked', 'known_other_owner', 'unknown_type') ORDER BY id",
    )]
    assert len(before) == 3
    assert erase(client, headers).status_code == 200
    assert [dict(row) for row in db.conn.execute("SELECT * FROM admin_audit ORDER BY id")] == before
    assert db.authenticate(other_token) == other_user
    assert db.conn.execute("SELECT id FROM users WHERE id = ?", (other_user,)).fetchone()
    assert db.conn.execute("PRAGMA foreign_key_check").fetchall() == []
    assert not db.conn.in_transaction


def test_wrong_password_and_confirmation_never_erase(client):
    first, headers = register(client, "erase_confirm")
    assert erase(client, headers, password="wrong-password-456").status_code == 401
    assert erase(client, headers, confirmation="DELETE").status_code == 422
    assert client.get("/api/v1/account", headers=headers).json() == first["user"]


def test_active_and_deleted_conversation_cleanup_tasks_block_account_erasure(client):
    first, headers = register(client, "erase_busy")
    user = first["user"]["id"]
    db = client.app.state.db
    chat = db.create_conversation(user, ConversationInput())
    run, _ = db.create_run(user, chat.id, RunInput(clientMessageId=uuid4(), content="active"), 120000)
    assert erase(client, headers).status_code == 409
    assert client.get("/api/v1/account/export", headers=headers).status_code == 409
    db.finish(run.id, "cancelled")
    db.delete_conversation(user, chat.id)
    previous = accounts._Accounts(db).require(user)
    with pytest.raises(Exception) as error:
        AccountDataRepository(db, Settings(_env_file=None)).erase(user, previous, {run.id})
    assert error.value.code == "account_busy"
    assert client.get("/api/v1/account", headers=headers).status_code == 200
    assert erase(client, headers).status_code == 200


def test_erasure_failure_rolls_back_everything_including_cdk_tombstone(client):
    first, headers = register(client, "erase_rollback")
    user = first["user"]["id"]
    chat, _ = populate(client, user)
    db = client.app.state.db
    energy = EnergyRepository(db, Settings(_env_file=None))
    cdk = energy.issue(CdkIssue(energy=3))[0]
    energy.redeem(user, cdk.code)
    with db.atomic():
        db.conn.execute("CREATE TRIGGER erase_fault BEFORE DELETE ON accounts BEGIN SELECT RAISE(ABORT, 'synthetic erasure fault'); END;")
    with pytest.raises(Exception, match="synthetic erasure fault"):
        erase(client, headers)
    assert client.get("/api/v1/account", headers=headers).status_code == 200
    assert db.conversation(user, chat.id)
    assert db.conn.execute("SELECT redeemedBy FROM energy_cdks WHERE prefix = ?", (cdk.prefix,)).fetchone()[0] == user
    assert db.conn.execute("PRAGMA foreign_key_check").fetchall() == []


@pytest.mark.parametrize("auth_kind", ["bearer", "browser"])
@pytest.mark.parametrize("mutation", ["revoke", "change_password", "delete_account"])
def test_erasure_rechecks_credentials_after_password_worker(client, monkeypatch, auth_kind, mutation):
    first, headers = register(client, "erase_worker_race")
    user = first["user"]["id"]
    chat, _ = populate(client, user)
    token = first["accessToken"]
    if auth_kind == "browser":
        exchange = client.post("/api/v1/auth/browser/session", headers={
            **headers, "Origin": ORIGIN,
        }).json()
        headers = {"Origin": ORIGIN, "X-CSRF-Token": exchange["csrfToken"]}
        token = client.cookies.get("allira_user")
    db = client.app.state.db
    original = accounts._derive_password

    def changed_during_worker(password, salt):
        digest = original(password, salt)
        if mutation == "revoke":
            db.revoke_session(token)
        else:
            with db.atomic():
                if mutation == "change_password":
                    db.conn.execute("UPDATE accounts SET passwordHash = ? WHERE userId = ?", (b"x" * 64, user))
                else:
                    db.conn.execute("DELETE FROM accounts WHERE userId = ?", (user,))
        return digest

    monkeypatch.setattr(accounts, "_derive_password", changed_during_worker)
    assert erase(client, headers).status_code == 401
    assert db.conversation(user, chat.id)
    assert db.conn.execute("SELECT COUNT(*) FROM users WHERE id = ?", (user,)).fetchone()[0] == 1
    assert not db.conn.in_transaction


def test_browser_erasure_requires_csrf_and_clears_cookie(client):
    first, _ = register(client, "erase_browser")
    exchange = client.post("/api/v1/auth/browser/session", headers={
        "Authorization": "Bearer " + first["accessToken"], "Origin": ORIGIN,
    }).json()
    assert erase(client, {}, password=PASSWORD).status_code == 403
    response = erase(client, {"Origin": ORIGIN, "X-CSRF-Token": exchange["csrfToken"]})
    assert response.status_code == 200 and "Max-Age=0" in response.headers["set-cookie"]
    assert client.get("/api/v1/auth/browser/session").status_code == 401
