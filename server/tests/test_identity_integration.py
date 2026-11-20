"""Application-level identity, legacy migration, privacy and lifecycle regressions.

All accounts are synthetic. Delivery/providers are injected in process; no listener,
actual SMS, OAuth provider, serving database or model endpoint is contacted.
"""

import hmac
import secrets
import sqlite3
import time
from contextlib import closing
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from allira import accounts
from allira.accounts import AccountRegister, _Accounts
from allira.admin_db import AdminRepository, token_hash
from allira.app import create_app
from allira.backup_cli import create_backup, restore_backup
from allira.config import Settings
from allira.db import Database, now
from allira.identity_lifecycle import clear_transient_identity_state, revoke_identity_proofs
from allira.phone_identity import PhoneIdentityRepository
from allira.schemas import ConversationInput, RunInput
from allira.social_providers import TestSocialProvider

ORIGIN = "http://127.0.0.1:8000"
PHONE = "+8613800138000"
OTHER_PHONE = "+8613900139000"
PASSWORD = "synthetic-private-password-123"
NEW_PASSWORD = "synthetic-new-password-456"
KEY = "isolated-integration-key-" + "x" * 40
TERMS = {"acceptedTermsVersion": "2026-11-19", "isAdult": True}
TRANSIENT = ("sessions", "admin_sessions", "admin_login_codes", "account_recovery_codes",
             "phone_challenges", "social_browsers", "social_oauth_states", "social_pending")


def cheap_password(password, salt):
    return hmac.digest(salt, password.get_secret_value().encode(), "sha256") * 2


class FakeSender:
    def __init__(self):
        self.sent = []
        self.before_send = None

    async def send(self, phone, code, purpose):
        if self.before_send:
            self.before_send()
        self.sent.append((phone, code, purpose))


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(accounts, "_derive_password", cheap_password)
    settings = Settings(
        _env_file=None, database_path=str(tmp_path / "identity.sqlite3"), provider="mock",
        fast_upstream_model="", legacy_registration_enabled=True,
        phone_identity_hmac_key=KEY, allow_dev_auth=True, admin_enabled=True,
    )
    with TestClient(create_app(settings), base_url=ORIGIN, client=("127.0.0.1", 1234),
                    raise_server_exceptions=False) as active:
        active.app.state.sms_sender = FakeSender()
        active.app.state.social_providers = {
            name: TestSocialProvider(name, ORIGIN + "/api/v1/auth/social/" + name + "/callback")
            for name in ("wechat", "qq")
        }
        yield active


def error(response, status, code):
    assert response.status_code == status, response.text
    assert response.json()["error"]["code"] == code
    assert set(response.json()) == {"error"}


def auth(session):
    return {"Authorization": "Bearer " + session["accessToken"]}


def legacy(client, username="legacy_owner"):
    db = client.app.state.db
    salt = secrets.token_bytes(16)
    body = AccountRegister(username=username, password=PASSWORD, **TERMS)
    return _Accounts(db).register(body, salt, cheap_password(body.password, salt),
                                  secrets.token_urlsafe(32), 3600).model_dump()


def phone_account(client, phone=PHONE, *, password=False):
    db = client.app.state.db
    repo = PhoneIdentityRepository(db)
    salt = secrets.token_bytes(16) if password else None
    digest = cheap_password(accounts.AccountLogin(username="unused", password=PASSWORD).password,
                            salt) if password else None
    with db.atomic():
        account = repo.create_phone_account(phone, salt, digest)
        return _Accounts(db).login(account, secrets.token_urlsafe(32), 3600).model_dump()


def age_rate_rows(client):
    # Time-dependent delivery limits have their own tests. Advance only synthetic rate
    # timestamps here to permit distinct security purposes without a real wait.
    db = client.app.state.db
    with db.atomic():
        db.conn.execute("UPDATE phone_send_attempts SET createdAt = createdAt - 61")


def security_challenge(client, session, purpose):
    age_rate_rows(client)
    response = client.post("/api/v1/account/phone/security/challenges", headers=auth(session),
                           json={"purpose": purpose})
    assert response.status_code == 202, response.text
    body = response.json()
    code = client.app.state.sms_sender.sent[-1][1]
    assert code not in response.text and set(body) == {
        "challengeId", "maskedPhone", "expiresAt", "resendAfterSeconds",
    }
    return {"challengeId": body["challengeId"], "code": code}


def seed_linked_flows(db, user, phone=PHONE):
    """Create private, synthetic unfinished flows attributable to a linked account."""
    repo = PhoneIdentityRepository(db)
    age = time.time()
    suffix = uuid4().hex
    browser_hash, pending_hash = token_hash("browser-" + suffix), token_hash("pending-" + suffix)
    subject = "verified-subject-" + suffix
    with db.atomic():
        db.conn.execute("INSERT INTO social_identities VALUES(?,?,?,?,?)",
                        ("qq", "isolated-app", subject, user, now()))
        db.conn.execute("INSERT INTO social_browsers VALUES(?,?,?)",
                        (browser_hash, ORIGIN, age + 600))
        revision = db.conn.execute("SELECT revision FROM social_revocation_clock WHERE singleton=1").fetchone()[0]
        db.conn.execute(
            "INSERT INTO social_oauth_states (stateHash,browserHash,provider,clientId,redirectUri,"
            "status,expiresAt,flowRevision) VALUES(?,?,?,?,?,?,?,?)",
            (token_hash("state-" + suffix), browser_hash, "qq", "isolated-app",
             ORIGIN + "/api/v1/auth/social/qq/callback", "consumed", age + 300, revision),
        )
        db.conn.execute(
            "INSERT INTO social_pending (pendingHash,browserHash,provider,clientId,subject,"
            "displayName,expiresAt,flowRevision) VALUES(?,?,?,?,?,?,?,?)",
            (pending_hash, browser_hash, "qq", "isolated-app", subject,
             "synthetic profile", age + 600, revision),
        )
    challenge, code = repo.begin_challenge(phone, "social_bind", "social:" + pending_hash, "peer")
    repo.mark_issued(challenge.challengeId)
    return browser_hash, pending_hash, challenge.challengeId, code


def test_default_channels_disabled_fail_closed_and_legacy_login_still_works(tmp_path, monkeypatch):
    monkeypatch.setattr(accounts, "_derive_password", cheap_password)
    settings = Settings(_env_file=None, database_path=str(tmp_path / "disabled.sqlite3"),
                        provider="mock", legacy_registration_enabled=False)
    with TestClient(create_app(settings), base_url=ORIGIN, client=("127.0.0.1", 1)) as active:
        body = active.get("/api/v1/auth/options").json()
        assert body["phone"] == {"enabled": False, "simulated": False, "reason": "sms_unavailable",
                                  "countryCallingCodes": ["+86"]}
        assert all(not option["enabled"] for option in body["social"])
        error(active.post("/api/v1/auth/phone/challenges", json={"phone": PHONE, "purpose": "register"}),
              503, "sms_unavailable")
        error(active.post("/api/v1/auth/register", json={"username": "new_user", "password": PASSWORD,
                                                         **TERMS}), 403, "phone_registration_required")
        original = legacy(active)
        response = active.post("/api/v1/auth/login", json={"username": original["user"]["username"],
                                                           "password": PASSWORD})
        assert response.status_code == 200 and response.json()["user"] == original["user"]
        assert active.get("/api/v1/account/identity", headers=auth(original)).json()["requiresPhoneBinding"]
        assert active.app.state.db.conn.execute("SELECT COUNT(*) FROM phone_challenges").fetchone()[0] == 0


def test_phone_binding_preserves_immutable_legacy_owner_and_private_data(client):
    session = legacy(client)
    db, user = client.app.state.db, session["user"]["id"]
    chat = db.create_conversation(user, ConversationInput(title="old private conversation"))
    db.create_session("other-legacy-device", user, 3600)
    response = client.post("/api/v1/account/phone/challenges", headers=auth(session),
                           json={"phone": PHONE, "currentPassword": PASSWORD})
    assert response.status_code == 202
    code = client.app.state.sms_sender.sent[-1][1]
    bound = client.post("/api/v1/account/phone/bind", headers=auth(session), json={
        "challengeId": response.json()["challengeId"], "code": code, "currentPassword": PASSWORD,
    })
    assert bound.status_code == 200 and bound.json()["phone"] == PHONE
    assert bound.json()["primaryIdentifier"] == "phone"
    assert client.get("/api/v1/account", headers=auth(session)).json() == session["user"]
    assert client.get("/api/v1/conversations/" + chat.id, headers=auth(session)).status_code == 200
    error(client.get("/api/v1/session", headers={"Authorization": "Bearer other-legacy-device"}),
          401, "unauthorized")
    logged = client.post("/api/v1/auth/phone/password", json={"phone": PHONE, "password": PASSWORD})
    assert logged.status_code == 200 and logged.json()["user"]["id"] == user
    assert db.conn.execute("PRAGMA foreign_key_check").fetchall() == [] and not db.conn.in_transaction


def test_passwordless_account_sets_password_with_session_bound_otp_and_revokes_all(client):
    session = phone_account(client)
    db, user = client.app.state.db, session["user"]["id"]
    assert not client.get("/api/v1/account/identity", headers=auth(session)).json()["passwordEnabled"]
    error(client.post("/api/v1/account/password", headers=auth(session),
                      json={"currentPassword": PASSWORD, "newPassword": NEW_PASSWORD}),
          403, "password_not_enabled")
    proof = security_challenge(client, session, "set_password")
    other_token = secrets.token_urlsafe(32)
    db.create_session(other_token, user, 3600)
    error(client.post("/api/v1/account/phone/password", headers={"Authorization": "Bearer " + other_token},
                      json={**proof, "newPassword": NEW_PASSWORD}), 401, "invalid_phone_code")
    result = client.post("/api/v1/account/phone/password", headers=auth(session),
                         json={**proof, "newPassword": NEW_PASSWORD})
    assert result.status_code == 200 and result.json() == {"ok": True}
    assert db.conn.execute("SELECT passwordEnabled FROM accounts WHERE userId=?", (user,)).fetchone()[0] == 1
    assert db.conn.execute("SELECT COUNT(*) FROM sessions WHERE userId=?", (user,)).fetchone()[0] == 0
    assert db.conn.execute("SELECT COUNT(*) FROM phone_challenges").fetchone()[0] == 0
    logged = client.post("/api/v1/auth/phone/password", json={"phone": PHONE, "password": NEW_PASSWORD})
    assert logged.status_code == 200 and logged.json()["user"]["id"] == user


def test_security_code_cannot_cross_purpose_or_phone_account(client):
    session = phone_account(client)
    other = phone_account(client, OTHER_PHONE)
    proof = security_challenge(client, session, "set_password")
    error(client.post("/api/v1/account/phone/erase", headers=auth(session),
                      json={**proof, "confirmation": "DELETE MY ACCOUNT"}), 401, "invalid_phone_code")
    error(client.post("/api/v1/account/phone/password", headers=auth(other),
                      json={**proof, "newPassword": NEW_PASSWORD}), 401, "invalid_phone_code")
    assert client.post("/api/v1/account/phone/password", headers=auth(session),
                       json={**proof, "newPassword": NEW_PASSWORD}).status_code == 200
    assert client.app.state.db.conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0] == 2


@pytest.mark.parametrize("mutation", ["revoke", "password", "phone", "expiry", "replacement", "consumed"])
def test_set_password_rechecks_session_identity_and_proof_after_worker(client, monkeypatch, mutation):
    session = phone_account(client)
    db, user = client.app.state.db, session["user"]["id"]
    proof = security_challenge(client, session, "set_password")
    original = _Accounts(db).require(user)

    def derive(password, salt):
        with db.atomic():
            if mutation == "revoke":
                db.revoke_session(session["accessToken"])
            elif mutation == "password":
                db.conn.execute("UPDATE accounts SET passwordHash=? WHERE userId=?", (b"z" * 64, user))
            elif mutation == "phone":
                db.conn.execute("UPDATE phone_phones SET phone=? WHERE userId=?", (OTHER_PHONE, user))
            elif mutation == "expiry":
                db.conn.execute("UPDATE phone_challenges SET expiresAt=0 WHERE challengeId=?", (proof["challengeId"],))
            else:
                status = "invalid" if mutation == "replacement" else "consumed"
                db.conn.execute("UPDATE phone_challenges SET status=? WHERE challengeId=?",
                                (status, proof["challengeId"]))
        return cheap_password(password, salt)

    async def run_worker(operation):
        # Keep the controlled mutation on this isolated SQLite event-loop connection.
        return operation()

    class RequestStub:
        app = client.app

    tools = accounts._auth_tools(RequestStub())
    monkeypatch.setattr(tools.workers, "run", run_worker)
    monkeypatch.setattr(accounts, "_derive_password", derive)
    response = client.post("/api/v1/account/phone/password", headers=auth(session),
                           json={**proof, "newPassword": NEW_PASSWORD})
    assert response.status_code == 401
    current = _Accounts(db).require(user)
    assert current["passwordSalt"] == original["passwordSalt"] and current["passwordEnabled"] == 0
    if mutation != "password":
        assert current["passwordHash"] == original["passwordHash"]
    assert not db.conn.in_transaction


def test_security_challenge_is_invalidated_if_session_changes_during_delivery(client):
    session = phone_account(client)
    client.app.state.sms_sender.before_send = lambda: client.app.state.db.revoke_session(session["accessToken"])
    error(client.post("/api/v1/account/phone/security/challenges", headers=auth(session),
                      json={"purpose": "erase"}), 401, "unauthorized")
    assert client.app.state.db.conn.execute("SELECT status FROM phone_challenges").fetchone()[0] == "invalid"


@pytest.mark.parametrize("deleted_chat", [False, True])
def test_phone_erasure_busy_rolls_back_otp_until_physical_cleanup_finishes(client, monkeypatch, deleted_chat):
    session = phone_account(client)
    db, user = client.app.state.db, session["user"]["id"]
    chat = db.create_conversation(user, ConversationInput(title="synthetic cleanup"))
    run, _ = db.create_run(user, chat.id, RunInput(clientMessageId=uuid4(), content="private input"), 120000)
    db.finish(run.id, "cancelled")
    if deleted_chat:
        db.delete_conversation(user, chat.id)
    proof = security_challenge(client, session, "erase")
    monkeypatch.setattr(client.app.state.runtime, "in_flight_run_ids", lambda: {run.id})
    error(client.post("/api/v1/account/phone/erase", headers=auth(session),
                      json={**proof, "confirmation": "DELETE MY ACCOUNT"}), 409, "account_busy")
    assert db.conn.execute("SELECT status FROM phone_challenges WHERE challengeId=?",
                           (proof["challengeId"],)).fetchone()[0] == "issued"
    monkeypatch.setattr(client.app.state.runtime, "in_flight_run_ids", set)
    assert client.post("/api/v1/account/phone/erase", headers=auth(session),
                       json={**proof, "confirmation": "DELETE MY ACCOUNT"}).status_code == 200
    assert db.conn.execute("SELECT COUNT(*) FROM accounts WHERE userId=?", (user,)).fetchone()[0] == 0
    assert db.conn.execute("SELECT COUNT(*) FROM phone_phones").fetchone()[0] == 0
    assert db.conn.execute("PRAGMA foreign_key_check").fetchall() == [] and not db.conn.in_transaction


@pytest.mark.parametrize("operation", ["password", "logout_all", "admin_revoke", "erase"])
def test_credential_lifecycle_clears_only_linked_pending_and_preserves_rate_caps(client, operation):
    session = phone_account(client, password=True)
    db, user = client.app.state.db, session["user"]["id"]
    browser, pending, _, _ = seed_linked_flows(db, user)
    # An unlinked, independently verified flow is not silently attributed by nickname.
    other = token_hash("unlinked-browser")
    with db.atomic():
        db.conn.execute("INSERT INTO social_browsers VALUES(?,?,?)", (other, ORIGIN, 9999999999))
        db.conn.execute(
            "INSERT INTO social_pending (pendingHash,browserHash,provider,clientId,subject,"
            "displayName,expiresAt,flowRevision) VALUES(?,?,?,?,?,?,?,?)",
            (token_hash("unlinked-pending"), other, "qq", "other-app", "other-subject",
             "same name", 9999999999,
             db.conn.execute("SELECT revision FROM social_revocation_clock WHERE singleton=1").fetchone()[0]),
        )
    rate_before = db.conn.execute("SELECT COUNT(*) FROM phone_send_attempts").fetchone()[0]
    revision_before = db.conn.execute("SELECT revision FROM social_revocation_clock WHERE singleton=1").fetchone()[0]
    if operation == "password":
        result = client.post("/api/v1/account/password", headers=auth(session),
                             json={"currentPassword": PASSWORD, "newPassword": NEW_PASSWORD})
    elif operation == "logout_all":
        result = client.post("/api/v1/account/logout-all", headers=auth(session), json={})
    elif operation == "admin_revoke":
        assert AdminRepository(db).revoke_user_sessions(user) == 1
        result = client.get("/api/v1/auth/options")
    else:
        result = client.post("/api/v1/account/erase", headers=auth(session),
                             json={"currentPassword": PASSWORD, "confirmation": "DELETE MY ACCOUNT"})
    assert result.status_code == 200, result.text
    assert db.conn.execute("SELECT 1 FROM social_browsers WHERE browserHash=?", (browser,)).fetchone() is None
    assert db.conn.execute("SELECT 1 FROM social_pending WHERE pendingHash=?", (pending,)).fetchone() is None
    assert db.conn.execute("SELECT 1 FROM social_browsers WHERE browserHash=?", (other,)).fetchone() is not None
    assert db.conn.execute("SELECT COUNT(*) FROM phone_challenges").fetchone()[0] == 0
    revision = db.conn.execute("SELECT revision FROM social_revocation_clock WHERE singleton=1").fetchone()[0]
    assert revision == revision_before + 1
    if operation != "erase":
        assert db.conn.execute("SELECT revokedRevision FROM social_user_revocations WHERE userId=?",
                               (user,)).fetchone()[0] == revision
        assert db.conn.execute("SELECT COUNT(*) FROM phone_send_attempts").fetchone()[0] == rate_before
    else:
        assert db.conn.execute("SELECT COUNT(*) FROM social_identities WHERE userId=?", (user,)).fetchone()[0] == 0
    assert not db.conn.in_transaction


def test_lifecycle_requires_outer_transaction_and_rolls_back_with_account_write(client):
    session = phone_account(client, password=True)
    db, user = client.app.state.db, session["user"]["id"]
    seed_linked_flows(db, user)
    before = "\n".join(db.conn.iterdump())
    with pytest.raises(RuntimeError, match="outer account transaction"):
        revoke_identity_proofs(db, user)
    with pytest.raises(RuntimeError, match="synthetic rollback"), db.atomic():
        revoke_identity_proofs(db, user)
        raise RuntimeError("synthetic rollback")
    assert "\n".join(db.conn.iterdump()) == before and not db.conn.in_transaction


def test_own_identity_export_excludes_otp_provider_subjects_and_cross_account_data(client):
    session = phone_account(client)
    other = phone_account(client, OTHER_PHONE)
    db = client.app.state.db
    _, _, _, code = seed_linked_flows(db, session["user"]["id"])
    response = client.get("/api/v1/account/export", headers=auth(session))
    assert response.status_code == 200
    assert response.json()["identity"]["phone"] == PHONE
    assert response.json()["identity"]["social"][0]["provider"] == "qq"
    for forbidden in [code, other["user"]["id"], OTHER_PHONE, "passwordSalt", "passwordHash",
                      "codeHash", "pendingHash", "browserHash", "verified-subject-", KEY]:
        assert forbidden not in response.text
    assert response.headers["cache-control"] == "no-store"


def test_social_callback_is_only_pending_until_phone_binding_then_cookie_session(client):
    before = client.app.state.db.conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0]
    social = client.get("/api/v1/auth/social/session")
    assert social.status_code == 200
    headers = {"Origin": ORIGIN, "X-CSRF-Token": social.json()["csrfToken"]}
    start = client.post("/api/v1/auth/social/qq/start", headers=headers, json={})
    assert start.status_code == 200
    target = urlsplit(start.json()["authorizeUrl"])
    callback = client.get(target.path + "?" + target.query, follow_redirects=False)
    assert callback.status_code == 303 and callback.headers["location"] == "/test/"
    pending = client.get("/api/v1/auth/social/session").json()
    assert pending["pending"]["requiresPhoneBinding"]
    assert client.app.state.db.conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0] == before
    error(client.get("/api/v1/account"), 401, "unauthorized")
    error(client.post("/api/v1/auth/social/complete", headers=headers, json={}), 409, "phone_binding_required")
    request = client.post("/api/v1/auth/social/phone/challenges", headers=headers, json={"phone": PHONE})
    assert request.status_code == 202
    code = client.app.state.sms_sender.sent[-1][1]
    result = client.post("/api/v1/auth/social/bind-phone", headers=headers, json={
        "challengeId": request.json()["challengeId"], "code": code, "confirmBind": True, **TERMS,
    })
    assert result.status_code == 200
    session = result.json()
    identity = client.get("/api/v1/account/identity", headers=auth(session)).json()
    assert identity["phoneVerified"] and not identity["passwordEnabled"]
    exchanged = client.post("/api/v1/auth/browser/session", headers={**auth(session), "Origin": ORIGIN})
    assert exchanged.status_code == 200
    assert client.get("/api/v1/account/identity").json()["phone"] == PHONE
    error(client.get("/api/v1/account", headers=auth(session)), 401, "unauthorized")
    csrf = exchanged.json()["csrfToken"]
    error(client.post("/api/v1/account/phone/security/challenges", headers={"Origin": ORIGIN},
                      json={"purpose": "erase"}), 403, "csrf_forbidden")
    age_rate_rows(client)
    assert client.post("/api/v1/account/phone/security/challenges", headers={
        "Origin": ORIGIN, "X-CSRF-Token": csrf,
    }, json={"purpose": "erase"}).status_code == 202


def test_restore_revokes_transient_identity_proofs_but_keeps_verified_account_links(client, tmp_path):
    session = phone_account(client)
    db, user = client.app.state.db, session["user"]["id"]
    seed_linked_flows(db, user)
    before = dict(db.conn.execute("SELECT * FROM accounts WHERE userId=?", (user,)).fetchone())
    output = tmp_path / "backups"
    backup = create_backup(Path(db.settings.database_path), output)
    target = tmp_path / "offline-restored.sqlite3"
    restore_backup(output / backup["backupName"], target)
    with closing(sqlite3.connect(target)) as copy:
        for table in TRANSIENT:
            assert copy.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0] == 0
        assert copy.execute("SELECT phone,userId FROM phone_phones").fetchone() == (PHONE, user)
        assert copy.execute("SELECT userId FROM social_identities").fetchone()[0] == user
        assert copy.execute("SELECT passwordHash FROM accounts WHERE userId=?", (user,)).fetchone()[0] == before["passwordHash"]
    assert db.conn.execute("SELECT COUNT(*) FROM phone_challenges").fetchone()[0] == 1
    assert db.conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 1
    assert not db.conn.in_transaction


def test_restart_invalidates_transient_state_without_resetting_daily_sms_rate(client):
    session = phone_account(client)
    db, user = client.app.state.db, session["user"]["id"]
    seed_linked_flows(db, user)
    before = db.conn.execute("SELECT COUNT(*) FROM phone_send_attempts").fetchone()[0]
    clear_transient_identity_state(db)
    for table in ("phone_challenges", "social_browsers", "social_oauth_states", "social_pending"):
        assert db.conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0] == 0
    assert db.conn.execute("SELECT COUNT(*) FROM phone_send_attempts").fetchone()[0] == before
    assert db.conn.execute("SELECT COUNT(*) FROM phone_phones").fetchone()[0] == 1
    assert db.conn.execute("SELECT COUNT(*) FROM social_identities").fetchone()[0] == 1


def test_migration_preserves_old_accounts_passwords_sessions_and_chats(tmp_path):
    path = tmp_path / "old-schema5.sqlite3"
    settings = Settings(_env_file=None, database_path=str(path), provider="mock")
    original = Database(str(path), settings)
    salt = secrets.token_bytes(16)
    body = AccountRegister(username="existing_legacy", password=PASSWORD, **TERMS)
    digest = cheap_password(body.password, salt)
    legacy_session = _Accounts(original).register(body, salt, digest, "legacy-device", 3600)
    user = legacy_session.user.id
    chat = original.create_conversation(user, ConversationInput(title="preserved title"))
    original.close()
    with closing(sqlite3.connect(path)) as connection:
        connection.executescript("""
            DROP TABLE social_pending;
            DROP TABLE social_oauth_states;
            DROP TABLE social_browsers;
            DROP TABLE social_identities;
            DROP TABLE social_user_revocations;
            DROP TABLE social_revocation_clock;
            DROP TABLE phone_challenges;
            DROP TABLE phone_send_attempts;
            DROP TABLE phone_phones;
            ALTER TABLE accounts DROP COLUMN passwordEnabled;
            PRAGMA user_version=5;
        """)
        connection.commit()
    migrated = Database(str(path), settings)
    try:
        assert migrated.conn.execute("PRAGMA user_version").fetchone()[0] == 8
        assert migrated.authenticate("legacy-device") == user
        assert migrated.conversation(user, chat.id).title == "preserved title"
        preserved = _Accounts(migrated).require(user)
        assert preserved["passwordEnabled"] == 1
        assert preserved["passwordHash"] == digest and preserved["passwordSalt"] == salt
        assert preserved["username"] == "existing_legacy"
        assert "passwordEnabled" in {row["name"] for row in migrated.conn.execute("PRAGMA table_info(accounts)")}
        assert migrated.conn.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        migrated.close()
