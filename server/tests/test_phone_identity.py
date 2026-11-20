"""Isolated phone/OTP identity tests: fake delivery, Mock settings, no API listener."""

import asyncio
import hashlib
import hmac
import json
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Annotated
from uuid import UUID

import pytest
from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.testclient import TestClient
from pydantic import SecretStr

from allira import accounts, phone_identity
from allira.accounts import AccountRegister, _Accounts
from allira.browser_sessions import authenticate_browser
from allira.config import Settings
from allira.db import APIError, Database
from allira.phone_identity import (
    PhoneIdentityRepository,
    initialize_phone_identity,
    normalize_phone,
    phone_identity_router,
)
from allira.schemas import ConversationInput
from allira.sms import DisabledSmsSender, SmsDeliveryError

PHONE = "13800138000"
OTHER_PHONE = "13900139000"
PASSWORD = "correct-phone-password-123"
ORIGIN = "http://127.0.0.1:8000"
HMAC_KEY = "isolated-phone-hmac-key-" + "x" * 40
REAL_DERIVE_PASSWORD = accounts._derive_password


class FakeSender:
    def __init__(self):
        self.sent = []
        self.fail = False
        self.before_send = None

    async def send(self, phone, code, purpose):
        if self.before_send is not None:
            self.before_send()
        if self.fail:
            raise SmsDeliveryError()
        self.sent.append((phone, code, purpose))


class BlockingSender(FakeSender):
    def __init__(self):
        super().__init__()
        self.started, self.release = threading.Event(), threading.Event()

    async def send(self, phone, code, purpose):
        self.started.set()
        await asyncio.to_thread(self.release.wait, 5)
        await super().send(phone, code, purpose)


def settings_for(path=":memory:", **overrides):
    # Independent of root's evolving Settings model; every DB option is still real Mock.
    baseline = Settings(_env_file=None, database_path=path, provider="mock").model_dump()
    return SimpleNamespace(**{
        **baseline, "registration_enabled": True, "legacy_registration_enabled": False,
        "session_ttl_seconds": 300, "phone_identity_hmac_key": HMAC_KEY,
        "sms_provider": "gateway", "phone_code_ttl_seconds": 300, "phone_resend_seconds": 60,
        **overrides,
    })


def make_app(*, sender=None, settings=None):
    settings = settings or settings_for()
    sender = sender if sender is not None else FakeSender()

    @asynccontextmanager
    async def lifespan(app):
        db = Database(settings.database_path, settings)
        initialize_phone_identity(db)
        app.state.db, app.state.settings, app.state.sms_sender = db, settings, sender
        try:
            yield
        finally:
            db.close()

    app = FastAPI(lifespan=lifespan)

    @app.exception_handler(APIError)
    async def api_error(request, exc):
        return JSONResponse({"error": {"code": exc.code, "message": exc.message}}, status_code=exc.status)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        return JSONResponse({"error": {"code": "validation_error", "message": "Invalid parameters."}},
                            status_code=422)

    @app.exception_handler(Exception)
    async def unexpected_error(request, exc):
        return JSONResponse({"error": {"code": "internal_error", "message": "Internal error."}},
                            status_code=500)

    bearer = HTTPBearer(auto_error=False)

    async def current_user(
        request: Request,
        credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    ):
        if "authorization" not in request.headers:
            return authenticate_browser(request)
        if credentials is None or credentials.scheme.lower() != "bearer":
            raise APIError(401, "unauthorized", "A valid session is required.")
        request.state.session_token, request.state.auth_kind = credentials.credentials, "bearer"
        return request.app.state.db.authenticate(credentials.credentials, kind="bearer")

    app.include_router(phone_identity_router(settings, current_user))
    return app


@pytest.fixture(autouse=True)
def fast_passwords(monkeypatch):
    # Production scrypt is verified in one separate test; all flow tests keep the same
    # salt+digest contract with a deterministic cheap primitive to isolate races/faults.
    monkeypatch.setattr(accounts, "_derive_password", lambda password, salt: hashlib.sha512(
        salt + password.get_secret_value().encode(),
    ).digest())


@pytest.fixture
def client():
    with TestClient(make_app(), base_url=ORIGIN, client=("127.0.0.1", 1234),
                    raise_server_exceptions=False) as client:
        yield client


def error(response, status, code):
    assert response.status_code == status, response.text
    assert response.json()["error"]["code"] == code
    assert set(response.json()) == {"error"}


def auth(session):
    return {"Authorization": "Bearer " + session["accessToken"]}


def challenge(client, phone=PHONE, purpose="register", **options):
    response = client.post("/api/v1/auth/phone/challenges", json={"phone": phone, "purpose": purpose},
                           **options)
    assert response.status_code == 202, response.text
    return response.json(), client.app.state.sms_sender.sent[-1][1]


def registration(challenge, code, **overrides):
    return {"challengeId": challenge["challengeId"], "code": code,
            "acceptedTermsVersion": "2026-11-19", "isAdult": True, **overrides}


def register(client, phone=PHONE, **overrides):
    proof, code = challenge(client, phone)
    response = client.post("/api/v1/auth/phone/register", json=registration(proof, code, **overrides))
    assert response.status_code == 201, response.text
    return response.json()


def legacy(client, username="legacy_user"):
    db = client.app.state.db
    salt = b"x" * 16
    digest = accounts._derive_password(SecretStr(PASSWORD), salt)
    return _Accounts(db).register(AccountRegister(
        username=username, password=PASSWORD, acceptedTermsVersion="2026-11-19", isAdult=True,
    ), salt, digest, "legacy-token-" + username, 300).model_dump()


def repository(client):
    return PhoneIdentityRepository(client.app.state.db, client.app.state.settings)


@pytest.mark.parametrize("value", [PHONE, "+86" + PHONE, "19912345678", "+8613012345678"])
def test_normalize_only_mainland_ascii(value):
    expected = value if value.startswith("+") else "+86" + value
    assert normalize_phone(value) == expected


@pytest.mark.parametrize("value", [
    "", " 13800138000", "13800138000 ", "138 0013 8000", "008613800138000",
    "+1" + PHONE, "+86 13800138000", "12800138000", "10800138000", "23800138000",
    "1380013800", "138001380000", "１３８００１３８０００", "١٣٨٠٠١٣٨٠٠٠", PHONE + "x123", None, 13800138000,
])
def test_normalize_rejects_ambiguous_unicode_and_other_countries(value):
    with pytest.raises(ValueError):
        normalize_phone(value)


def test_challenge_wire_and_no_plaintext_code_or_peer_in_database(client, caplog):
    proof, code = challenge(client)
    assert set(proof) == {"challengeId", "maskedPhone", "expiresAt", "resendAfterSeconds"}
    assert str(UUID(proof["challengeId"])) == proof["challengeId"]
    assert proof["resendAfterSeconds"] == 60 and PHONE not in proof["maskedPhone"]
    assert re.fullmatch(r"[0-9]{6}", code)
    row = client.app.state.db.conn.execute("SELECT * FROM phone_challenges").fetchone()
    assert row["status"] == "issued" and row["attempts"] == 0
    assert row["expiresAt"] - row["createdAt"] == 300
    assert row["codeHash"] != hashlib.sha256(code.encode()).hexdigest()
    dumped = json.dumps(dict(row))
    assert '"code"' not in dumped and row["codeHash"] != code
    rate = dict(client.app.state.db.conn.execute("SELECT * FROM phone_send_attempts").fetchone())
    assert rate["phoneKey"] != PHONE and rate["peerKey"] != "127.0.0.1"
    assert PHONE not in caplog.text and code not in caplog.text


def test_passwordless_register_and_otp_login_keep_legacy_dto(client, monkeypatch):
    current = [1000000.0]
    monkeypatch.setattr(phone_identity, "time", lambda: current[0])
    session = register(client)
    assert set(session) == {"accessToken", "tokenType", "user"}
    user = session["user"]
    assert user["id"].startswith("acct_") and len(user["id"]) == 37
    assert re.fullmatch(r"p_[a-f0-9]{24}", user["username"])
    assert user["displayName"] == "新朋友" and PHONE not in json.dumps(user)
    assert set(user) == {"id", "username", "displayName", "createdAt", "termsVersion", "isAdult"}
    identity = client.get("/api/v1/account/identity", headers=auth(session)).json()
    assert identity == {
        "primaryIdentifier": "phone", "phone": "+86" + PHONE, "maskedPhone": "+86 138****8000",
        "phoneVerified": True, "requiresPhoneBinding": False, "passwordEnabled": False, "social": [],
    }
    row = _Accounts(client.app.state.db).require(user["id"])
    assert len(row["passwordSalt"]) == 16 and len(row["passwordHash"]) == 64 and not row["passwordEnabled"]
    current[0] += 61
    proof, code = challenge(client, purpose="login")
    logged_in = client.post("/api/v1/auth/phone/login", json={"challengeId": proof["challengeId"], "code": code})
    assert logged_in.status_code == 200 and logged_in.json()["user"] == user
    error(client.post("/api/v1/auth/phone/login", json={"challengeId": proof["challengeId"], "code": code}),
          401, "invalid_phone_code")
    error(client.post("/api/v1/auth/phone/password", json={"phone": PHONE, "password": PASSWORD}),
          401, "invalid_credentials")


def test_password_register_uses_salt_and_supports_phone_password_login(client):
    session = register(client, password=PASSWORD, displayName="  小友  ")
    row = _Accounts(client.app.state.db).require(session["user"]["id"])
    assert row["passwordEnabled"] == 1
    assert row["passwordHash"] == accounts._derive_password(SecretStr(PASSWORD), row["passwordSalt"])
    assert session["user"]["displayName"] == "小友"
    response = client.post("/api/v1/auth/phone/password", json={"phone": "+86" + PHONE, "password": PASSWORD})
    assert response.status_code == 200 and response.json()["user"] == session["user"]
    assert response.json()["accessToken"] != session["accessToken"]
    error(client.post("/api/v1/auth/phone/password", json={"phone": PHONE, "password": "wrong-password"}),
          401, "invalid_credentials")


def test_unknown_phone_password_still_hashes_and_compares(client, monkeypatch):
    calls = []
    original_hash, original_compare = accounts._derive_password, hmac.compare_digest
    monkeypatch.setattr(accounts, "_derive_password", lambda password, salt: (
        calls.append(("hash", salt)) or original_hash(password, salt)))
    monkeypatch.setattr(phone_identity.hmac, "compare_digest", lambda left, right: (
        calls.append(("compare", len(left))) or original_compare(left, right)))
    error(client.post("/api/v1/auth/phone/password", json={"phone": PHONE, "password": PASSWORD}),
          401, "invalid_credentials")
    assert [kind for kind, _ in calls] == ["hash", "compare"]


def test_legacy_identity_no_phone_and_social_optional(client):
    session = legacy(client)
    identity = client.get("/api/v1/account/identity", headers=auth(session))
    assert identity.status_code == 200
    assert identity.json() == {"primaryIdentifier": "legacy_username", "phone": None, "maskedPhone": None,
                               "phoneVerified": False, "requiresPhoneBinding": True,
                               "passwordEnabled": True, "social": []}
    db = client.app.state.db
    with db.atomic():
        db.conn.execute("CREATE TABLE IF NOT EXISTS social_identities (provider TEXT, clientId TEXT, "
                        "subject TEXT, userId TEXT REFERENCES accounts(userId), linkedAt TEXT)")
        db.conn.execute("INSERT INTO social_identities (provider, clientId, subject, userId, linkedAt) "
                        "VALUES ('qq','test-client','private-subject',?,'2026-11-19T00:00:00Z')",
                        (session["user"]["id"],))
    data = client.get("/api/v1/account/identity", headers=auth(session)).json()
    assert data["social"] == [{"provider": "qq", "linkedAt": "2026-11-19T00:00:00Z"}]
    assert "private-subject" not in json.dumps(data) and "test-client" not in json.dumps(data)


def test_wrong_attempts_are_committed_and_five_lock_correct_code(client):
    challenge_data, code = challenge(client)
    wrong = "000000" if code != "000000" else "999999"
    for number in range(1, 6):
        error(client.post("/api/v1/auth/phone/register", json=registration(challenge_data, wrong)),
              401, "invalid_phone_code")
        row = client.app.state.db.conn.execute("SELECT * FROM phone_challenges").fetchone()
        assert row["attempts"] == number
        assert not client.app.state.db.conn.in_transaction
    error(client.post("/api/v1/auth/phone/register", json=registration(challenge_data, code)),
          401, "invalid_phone_code")
    assert client.app.state.db.conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0] == 0


def test_expiry_resend_and_purpose_subject_binding(client, monkeypatch):
    current = [1000000.0]
    monkeypatch.setattr(phone_identity, "time", lambda: current[0])
    first, code = challenge(client)
    repo = repository(client)
    with pytest.raises(APIError):
        repo.inspect_challenge(first["challengeId"], code, purpose="login", subject="public")
    with pytest.raises(APIError):
        repo.inspect_challenge(first["challengeId"], code, purpose="register", subject="someone-else")
    error(client.post("/api/v1/auth/phone/challenges", json={"phone": "+86" + PHONE, "purpose": "login"}),
          429, "rate_limited")
    current[0] += 60
    second, new_code = challenge(client)
    with pytest.raises(APIError):
        repo.inspect_challenge(first["challengeId"], code, purpose="register", subject="public")
    assert repo.inspect_challenge(second["challengeId"], new_code, purpose="register", subject="public").phone == "+86" + PHONE
    current[0] += 300
    with pytest.raises(APIError):
        repo.inspect_challenge(second["challengeId"], new_code, purpose="register", subject="public")
    assert client.app.state.db.conn.execute("SELECT COUNT(*) FROM phone_challenges").fetchone()[0] == 0


def test_sending_cannot_verify_and_failure_is_invalid_with_no_leaks(client):
    repo, sender = repository(client), client.app.state.sms_sender
    seen = []

    def before_send():
        row = client.app.state.db.conn.execute("SELECT * FROM phone_challenges").fetchone()
        assert row["status"] == "sending" and not client.app.state.db.conn.in_transaction
        seen.append(row["challengeId"])
        with pytest.raises(APIError):
            repo.inspect_challenge(row["challengeId"], "000000", purpose="register", subject="public")

    sender.before_send, sender.fail = before_send, True
    response = client.post("/api/v1/auth/phone/challenges", json={"phone": PHONE, "purpose": "register"})
    error(response, 503, "sms_unavailable")
    row = client.app.state.db.conn.execute("SELECT * FROM phone_challenges").fetchone()
    assert row["status"] == "invalid" and len(seen) == 1
    assert PHONE not in response.text and not sender.sent


def test_default_disabled_and_missing_key_do_not_send():
    for sender, settings in [
        (DisabledSmsSender(), settings_for()), (FakeSender(), settings_for(phone_identity_hmac_key="")),
    ]:
        with TestClient(make_app(sender=sender, settings=settings), base_url=ORIGIN,
                        raise_server_exceptions=False) as client:
            error(client.post("/api/v1/auth/phone/challenges", json={"phone": PHONE, "purpose": "login"}),
                  503, "sms_unavailable")
            assert client.app.state.db.conn.execute("SELECT COUNT(*) FROM phone_challenges").fetchone()[0] == 0
            if isinstance(sender, FakeSender):
                assert not sender.sent


def test_public_origin_policy_does_not_trust_forwarded_headers(client):
    for headers in [{"Origin": "https://evil.example"}, {"Sec-Fetch-Site": "cross-site"},
                    {"Origin": "https://evil.example", "X-Forwarded-Host": "evil.example",
                     "X-Forwarded-Proto": "https"}]:
        error(client.post("/api/v1/auth/phone/challenges", json={"phone": PHONE, "purpose": "login"},
                          headers=headers), 403, "origin_forbidden")
    response = client.post("/api/v1/auth/phone/challenges", json={"phone": PHONE, "purpose": "login"},
                           headers={"Origin": ORIGIN})
    assert response.status_code == 202 and len(client.app.state.sms_sender.sent) == 1


@pytest.mark.parametrize("changes", [
    {"isAdult": False}, {"isAdult": 1}, {"acceptedTermsVersion": "old"}, {"isAdult": "true"},
    {"password": "short"}, {"password": "x" * 129}, {"password": "😀" * 65},
    {"displayName": "bad\u202e"}, {"code": "１２３４５６"}, {"code": "1234567"},
    {"debugCode": "123456"}, {"password": "\ud800" * 10},
])
def test_register_strict_terms_adult_password_and_input_bounds(client, changes):
    body = {**registration({"challengeId": str(UUID(int=1))}, "123456"), **changes}
    response = client.post("/api/v1/auth/phone/register", content=json.dumps(body, ensure_ascii=True),
                           headers={"Content-Type": "application/json"})
    error(response, 422, "validation_error")
    assert "123456" not in response.text and "debugCode" not in response.text


def test_registration_disabled_blocks_both_issue_and_register_but_not_login(client):
    client.app.state.settings.registration_enabled = False
    error(client.post("/api/v1/auth/phone/challenges", json={"phone": PHONE, "purpose": "register"}),
          403, "registration_disabled")
    error(client.post("/api/v1/auth/phone/register", json=registration({"challengeId": str(UUID(int=1))}, "123456")),
          403, "registration_disabled")
    assert not client.app.state.sms_sender.sent
    assert client.post("/api/v1/auth/phone/challenges", json={"phone": PHONE, "purpose": "login"}).status_code == 202


def test_binding_preserves_identity_data_and_revokes_only_other_sessions(client):
    session, db = legacy(client), client.app.state.db
    user = session["user"]["id"]
    conversation = db.create_conversation(user, ConversationInput(title="Keep my history"))
    db.create_session("other-device", user, 300)
    db.create_session("browser-device", user, 300, kind="browser")
    response = client.post("/api/v1/account/phone/challenges", headers=auth(session),
                           json={"phone": PHONE, "currentPassword": PASSWORD})
    assert response.status_code == 202, response.text
    code = client.app.state.sms_sender.sent[-1][1]
    bound = client.post("/api/v1/account/phone/bind", headers=auth(session), json={
        "challengeId": response.json()["challengeId"], "code": code, "currentPassword": PASSWORD,
    })
    assert bound.status_code == 200 and bound.json()["phone"] == "+86" + PHONE
    assert db.authenticate(session["accessToken"]) == user
    for token, kind in [("other-device", "bearer"), ("browser-device", "browser")]:
        with pytest.raises(APIError):
            db.authenticate(token, kind=kind)
    assert _Accounts(db).require(user)["username"] == "legacy_user"
    assert db.conversation(user, conversation.id).title == "Keep my history"
    assert db.conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0] == 1


def test_binding_proof_belongs_to_exact_user_and_session(client):
    first, second = legacy(client, "first_user"), legacy(client, "second_user")
    db = client.app.state.db
    db.create_session("same-user-other-session", first["user"]["id"], 300)
    response = client.post("/api/v1/account/phone/challenges", headers=auth(first),
                           json={"phone": PHONE, "currentPassword": PASSWORD})
    assert response.status_code == 202
    body = {"challengeId": response.json()["challengeId"], "code": client.app.state.sms_sender.sent[-1][1],
            "currentPassword": PASSWORD}
    for headers in [auth(second), {"Authorization": "Bearer same-user-other-session"}]:
        error(client.post("/api/v1/account/phone/bind", headers=headers, json=body), 401, "invalid_phone_code")
    assert client.post("/api/v1/account/phone/bind", headers=auth(first), json=body).status_code == 200


def test_repository_binding_conflicts_never_merges_or_changes_phone(client):
    first, second = legacy(client, "first_user"), legacy(client, "second_user")
    db, repo = client.app.state.db, repository(client)
    with db.atomic():
        repo.bind_phone(first["user"]["id"], PHONE)
        repo.bind_phone(first["user"]["id"], "+86" + PHONE)
    with pytest.raises(APIError) as occupied, db.atomic():
        repo.bind_phone(second["user"]["id"], PHONE)
    assert occupied.value.code == "phone_in_use"
    with pytest.raises(APIError) as already, db.atomic():
        repo.bind_phone(first["user"]["id"], OTHER_PHONE)
    assert already.value.code == "phone_already_bound"
    assert repo.account_for_phone(PHONE)["userId"] == first["user"]["id"]
    assert repo.account_for_phone(OTHER_PHONE) is None
    assert db.conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0] == 2


@pytest.mark.parametrize("failure", ["phone", "session"])
def test_registration_fault_rolls_back_all_users_phone_and_proof(client, monkeypatch, failure):
    challenge_data, code = challenge(client)
    db = client.app.state.db
    if failure == "phone":
        db.conn.execute("CREATE TRIGGER fail_phone BEFORE INSERT ON phone_phones "
                        "BEGIN SELECT RAISE(ABORT, 'synthetic_failure'); END")
    else:
        original = db.create_session

        def fail_session(*args, **kwargs):
            original(*args, **kwargs)
            raise RuntimeError("synthetic_failure")

        monkeypatch.setattr(db, "create_session", fail_session)
    error(client.post("/api/v1/auth/phone/register", json=registration(challenge_data, code)),
          500, "internal_error")
    for table in ["accounts", "phone_phones", "users", "sessions"]:
        assert db.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
    assert db.conn.execute("SELECT status FROM phone_challenges").fetchone()[0] == "issued"
    assert not db.conn.in_transaction


def test_bind_failure_rolls_back_phone_and_revocations(client):
    session, db = legacy(client), client.app.state.db
    db.create_session("other-device", session["user"]["id"], 300)
    response = client.post("/api/v1/account/phone/challenges", headers=auth(session),
                           json={"phone": PHONE, "currentPassword": PASSWORD})
    db.conn.execute("CREATE TRIGGER fail_revoke BEFORE DELETE ON sessions "
                    "BEGIN SELECT RAISE(ABORT, 'synthetic_failure'); END")
    error(client.post("/api/v1/account/phone/bind", headers=auth(session), json={
        "challengeId": response.json()["challengeId"], "code": client.app.state.sms_sender.sent[-1][1],
        "currentPassword": PASSWORD,
    }), 500, "internal_error")
    assert repository(client).account_for_phone(PHONE) is None
    assert db.authenticate("other-device") == session["user"]["id"]
    assert db.conn.execute("SELECT status FROM phone_challenges").fetchone()[0] == "issued"
    assert not db.conn.in_transaction


def test_persistent_phone_rate_hour_day_and_restart(tmp_path, monkeypatch):
    current = [1000000.0]
    monkeypatch.setattr(phone_identity, "time", lambda: current[0])
    path = str(tmp_path / "isolated.sqlite3")
    settings = settings_for(path)
    db = Database(path, settings)
    initialize_phone_identity(db)
    repo = PhoneIdentityRepository(db)
    for _ in range(5):
        repo.begin_challenge(PHONE, "login", "public", "peer")
        current[0] += 60
    with pytest.raises(APIError) as limited:
        repo.begin_challenge(PHONE, "login", "public", "new-peer")
    assert limited.value.status == 429
    db.close()
    db = Database(path, settings)
    initialize_phone_identity(db)
    repo = PhoneIdentityRepository(db)
    with pytest.raises(APIError):
        repo.begin_challenge(PHONE, "login", "public", "new-peer")
    current[0] += 3600
    for _ in range(5):
        repo.begin_challenge(PHONE, "login", "public", "another-peer")
        current[0] += 60
    current[0] += 3600
    with pytest.raises(APIError) as daily:
        repo.begin_challenge(PHONE, "login", "public", "last-peer")
    assert daily.value.status == 429
    current[0] += 86400
    repo.begin_challenge(PHONE, "login", "public", "last-peer")
    assert db.conn.execute("SELECT COUNT(*) FROM phone_send_attempts").fetchone()[0] == 1
    db.close()


def test_peer_and_global_rates_are_bounded_before_sender(client, monkeypatch):
    monkeypatch.setattr(phone_identity, "time", lambda: 1000000.0)
    repo, db = repository(client), client.app.state.db
    for number in range(100):
        peer = "peer-" + str(number // 25)
        repo.begin_challenge(f"138{number:08d}", "login", "public", peer)
    with pytest.raises(APIError):
        repo.begin_challenge("13900000000", "login", "public", "fresh-peer")
    assert db.conn.execute("SELECT COUNT(*) FROM phone_send_attempts").fetchone()[0] == 100
    assert db.conn.execute("SELECT COUNT(*) FROM phone_challenges").fetchone()[0] <= 100
    assert not client.app.state.sms_sender.sent


def test_peer_rate_ignores_phone_rotation(client, monkeypatch):
    monkeypatch.setattr(phone_identity, "time", lambda: 1000000.0)
    repo = repository(client)
    for number in range(30):
        repo.begin_challenge(f"138{number:08d}", "login", "public", "same-peer")
    with pytest.raises(APIError) as limited:
        repo.begin_challenge("13900000000", "login", "public", "same-peer")
    assert limited.value.status == 429
    repo.begin_challenge("13900000000", "login", "public", "another-peer")


def test_x_forwarded_for_does_not_bypass_real_peer_public_attempts(client):
    for number in range(10):
        error(client.post("/api/v1/auth/phone/password", json={"phone": PHONE, "password": PASSWORD},
                          headers={"X-Forwarded-For": f"198.51.100.{number}"}), 401, "invalid_credentials")
    error(client.post("/api/v1/auth/phone/password", json={"phone": PHONE, "password": PASSWORD},
                      headers={"X-Forwarded-For": "different"}), 429, "rate_limited")


def test_proof_conditional_single_use_and_outer_transaction_rollback(client):
    challenge_data, code = challenge(client)
    repo, db = repository(client), client.app.state.db
    proof = repo.inspect_challenge(challenge_data["challengeId"], code, purpose="register", subject="public")
    assert code not in repr(proof)
    with pytest.raises(RuntimeError, match="outer transaction"):
        repo.consume_challenge(proof)
    with pytest.raises(RuntimeError, match="synthetic"), db.atomic():
        repo.consume_challenge(proof)
        raise RuntimeError("synthetic")
    with db.atomic():
        repo.consume_challenge(proof)
    with pytest.raises(APIError), db.atomic():
        repo.consume_challenge(proof)


def test_initializer_idempotent_preserves_existing_data_and_fk_cascades(client):
    session, db = legacy(client), client.app.state.db
    version = db.conn.execute("PRAGMA user_version").fetchone()[0]
    with db.atomic():
        repository(client).bind_phone(session["user"]["id"], PHONE)
    initial = list(db.conn.execute("SELECT * FROM phone_phones").fetchone())
    initialize_phone_identity(db)
    assert list(db.conn.execute("SELECT * FROM phone_phones").fetchone()) == initial
    assert db.conn.execute("PRAGMA user_version").fetchone()[0] == version
    with db.atomic():
        db.conn.execute("DELETE FROM accounts WHERE userId = ?", (session["user"]["id"],))
    assert db.conn.execute("SELECT COUNT(*) FROM phone_phones").fetchone()[0] == 0
    assert db.conn.execute("PRAGMA foreign_key_check").fetchall() == []


def test_openapi_has_secret_write_only_inputs_and_no_code_in_challenge(client):
    schema = client.get("/openapi.json").json()
    components = schema["components"]["schemas"]
    assert "code" not in components["PhoneChallenge"]["properties"]
    assert components["PhoneRegisterInput"]["properties"]["code"]["writeOnly"] is True
    assert components["PhonePasswordInput"]["properties"]["password"]["writeOnly"] is True
    assert "password" not in components["PhoneRegisterInput"]["required"]
    assert set(schema["paths"]) == {
        "/api/v1/auth/phone/challenges", "/api/v1/auth/phone/register", "/api/v1/auth/phone/login",
        "/api/v1/auth/phone/password", "/api/v1/account/identity", "/api/v1/account/phone/challenges",
        "/api/v1/account/phone/bind",
    }


def test_binding_rechecks_revoked_session_after_password_worker(client, monkeypatch):
    session = legacy(client)
    started, release = threading.Event(), threading.Event()
    original = accounts._derive_password

    def blocked(password, salt):
        started.set()
        assert release.wait(5)
        return original(password, salt)

    monkeypatch.setattr(accounts, "_derive_password", blocked)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(client.post, "/api/v1/account/phone/challenges", headers=auth(session),
                             json={"phone": PHONE, "currentPassword": PASSWORD})
        assert started.wait(5)
        with client.app.state.db.atomic():
            client.app.state.db.conn.execute("DELETE FROM sessions WHERE userId = ?",
                                            (session["user"]["id"],))
        release.set()
        error(future.result(5), 401, "unauthorized")
    assert not client.app.state.sms_sender.sent


def test_binding_rechecks_revocation_during_sms_delivery():
    sender = BlockingSender()
    with TestClient(make_app(sender=sender), base_url=ORIGIN, client=("127.0.0.1", 1234),
                    raise_server_exceptions=False) as client:
        session = legacy(client)
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(client.post, "/api/v1/account/phone/challenges", headers=auth(session),
                                 json={"phone": PHONE, "currentPassword": PASSWORD})
            assert sender.started.wait(5)
            with client.app.state.db.atomic():
                client.app.state.db.conn.execute("DELETE FROM sessions WHERE userId = ?",
                                                (session["user"]["id"],))
            sender.release.set()
            error(future.result(5), 401, "unauthorized")
        assert client.app.state.db.conn.execute("SELECT status FROM phone_challenges").fetchone()[0] == "invalid"


def test_password_login_rechecks_password_snapshot_after_hash(client, monkeypatch):
    session = register(client, password=PASSWORD)
    started, release = threading.Event(), threading.Event()
    original = accounts._derive_password

    def blocked(password, salt):
        started.set()
        assert release.wait(5)
        return original(password, salt)

    monkeypatch.setattr(accounts, "_derive_password", blocked)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(client.post, "/api/v1/auth/phone/password", json={"phone": PHONE, "password": PASSWORD})
        assert started.wait(5)
        with client.app.state.db.atomic():
            client.app.state.db.conn.execute("UPDATE accounts SET passwordHash = ? WHERE userId = ?",
                                            (b"z" * 64, session["user"]["id"]))
        release.set()
        error(future.result(5), 401, "invalid_credentials")


def test_phone_registration_uses_real_scrypt_when_password_enabled(client, monkeypatch):
    monkeypatch.setattr(accounts, "_derive_password", REAL_DERIVE_PASSWORD)
    session = register(client, password=PASSWORD)
    row = _Accounts(client.app.state.db).require(session["user"]["id"])
    assert row["passwordHash"] == hashlib.scrypt(
        PASSWORD.encode(), salt=row["passwordSalt"], n=32768, r=8, p=3,
        dklen=64, maxmem=64 * 1024 * 1024,
    )
    assert row["passwordHash"] != hashlib.sha256(PASSWORD.encode()).digest()


def test_purge_verifications_owner_exact_prefix_and_transaction_rollback(client):
    first, second = legacy(client, "first_user"), legacy(client, "second_user")
    repo, db = repository(client), client.app.state.db
    own, other = first["user"]["id"], second["user"]["id"]
    challenge_one, _ = repo.begin_challenge(PHONE, "bind", "bind:" + own + ":hash", "peer-one")
    challenge_two, _ = repo.begin_challenge(OTHER_PHONE, "bind", "bind:" + other + ":hash", "peer-two")
    with db.atomic():
        repo.purge_user_verifications(own)
    assert db.conn.execute("SELECT 1 FROM phone_challenges WHERE challengeId = ?",
                           (challenge_one.challengeId,)).fetchone() is None
    assert db.conn.execute("SELECT 1 FROM phone_challenges WHERE challengeId = ?",
                           (challenge_two.challengeId,)).fetchone() is not None
    with db.atomic():
        repo.bind_phone(other, OTHER_PHONE)
    with pytest.raises(RuntimeError, match="rollback"), db.atomic():
        repo.purge_user_verifications(other)
        raise RuntimeError("rollback")
    assert db.conn.execute("SELECT 1 FROM phone_challenges WHERE challengeId = ?",
                           (challenge_two.challengeId,)).fetchone() is not None
    with db.atomic():
        repo.purge_user_verifications(other)
    assert db.conn.execute("SELECT COUNT(*) FROM phone_challenges").fetchone()[0] == 0
    assert db.conn.execute("SELECT COUNT(*) FROM phone_send_attempts").fetchone()[0] == 1


def test_issue_cancellation_invalidates_sending_challenge_and_never_holds_transaction():
    async def exercise():
        settings = settings_for()
        db = Database(":memory:", settings)
        initialize_phone_identity(db)
        started = asyncio.Event()

        class CancellableSender:
            async def send(self, phone, code, purpose):
                assert not db.conn.in_transaction
                started.set()
                await asyncio.Event().wait()

        app = FastAPI()
        app.state.db, app.state.settings, app.state.sms_sender = db, settings, CancellableSender()
        request = Request({"type": "http", "method": "POST", "path": "/", "scheme": "http",
                           "headers": [(b"host", b"127.0.0.1:8000")], "app": app,
                           "server": ("127.0.0.1", 8000), "client": ("127.0.0.1", 1234),
                           "query_string": b""})
        task = asyncio.create_task(phone_identity.issue_phone_challenge(request, PHONE, "login", "public"))
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert db.conn.execute("SELECT status FROM phone_challenges").fetchone()[0] == "invalid"
        assert not db.conn.in_transaction
        db.close()

    asyncio.run(exercise())


def test_two_parallel_registrations_consume_proof_once(client):
    data, code = challenge(client)
    body = registration(data, code)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: client.post("/api/v1/auth/phone/register", json=body), range(2)))
    assert sorted(response.status_code for response in results) == [201, 401]
    db = client.app.state.db
    assert db.conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0] == 1
    assert db.conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 1
    assert db.conn.execute("SELECT status FROM phone_challenges").fetchone()[0] == "consumed"
    assert not db.conn.in_transaction


def test_purge_without_hmac_key_still_removes_plaintext_only_for_owner(client):
    session = legacy(client)
    repo, db = repository(client), client.app.state.db
    user = session["user"]["id"]
    own, _ = repo.begin_challenge(PHONE, "erase", "security:erase:" + user + ":hash", "peer")
    other, _ = repo.begin_challenge(OTHER_PHONE, "login", "public", "other-peer")
    with db.atomic():
        repo.bind_phone(user, PHONE)
    client.app.state.settings.phone_identity_hmac_key = ""
    with db.atomic():
        repo.purge_user_verifications(user)
    assert db.conn.execute("SELECT 1 FROM phone_challenges WHERE challengeId = ?", (own.challengeId,)).fetchone() is None
    assert db.conn.execute("SELECT 1 FROM phone_challenges WHERE challengeId = ?", (other.challengeId,)).fetchone() is not None
    assert db.conn.execute("SELECT COUNT(*) FROM phone_send_attempts").fetchone()[0] == 2


def test_social_binding_helpers_create_passwordless_in_same_transaction(client):
    repo, db = repository(client), client.app.state.db
    challenge_data, code = repo.begin_challenge(PHONE, "social_bind", "social:pending-hash", "peer")
    repo.mark_issued(challenge_data.challengeId)
    proof = repo.inspect_challenge(challenge_data.challengeId, code, purpose="social_bind", subject="social:pending-hash")
    with db.atomic():
        repo.consume_challenge(proof)
        row = repo.create_phone_account(proof.phone, None, None)
        assert not row["passwordEnabled"]
        assert repo.account_for_phone(PHONE)["userId"] == row["userId"]
    assert repo.identity(row["userId"]).primaryIdentifier == "phone"
