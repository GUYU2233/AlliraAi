"""Credits and route accounting use isolated SQLite and deterministic providers only."""

import asyncio
import hashlib
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from allira.app import create_app
from allira.config import Settings
from allira.db import APIError, Database
from allira.economy import MAX_BALANCE, CdkIssue, EnergyRepository
from allira.providers import MockProvider, ProviderError
from allira.runtime import RunRuntime
from allira.schemas import ConversationInput, RunInput


@pytest.fixture
def setup():
    settings = Settings(_env_file=None, database_path=":memory:")
    db = Database(":memory:", settings)
    energy = EnergyRepository(db, settings)
    chat = db.create_conversation("alice", ConversationInput())
    yield db, energy, chat, settings
    db.close()


def start(db, chat, mode="economy", **kwargs):
    body = RunInput(clientMessageId=uuid4(), content="hello", mode=mode)
    run, prompt = db.create_run("alice", chat.id, body, 120000, **kwargs)
    return body, run, prompt


def test_welcome_is_once_and_integer_ledger_has_owner_pagination(setup):
    db, energy, _, _ = setup
    assert energy.wallet("alice").balance == 100
    assert energy.wallet("alice").balance == 100
    assert energy.wallet("bob").balance == 100
    first = energy.ledger("alice", None, 1)
    assert first["items"][0]["delta"] == 100
    assert first["items"][0]["kind"] == "welcome"
    assert isinstance(first["items"][0]["id"], str)
    bob_cursor = energy.ledger("bob", None, 1)["items"][0]["id"]
    for cursor in [bob_cursor, "9" * 100, "-1", "１２", "missing"]:
        with pytest.raises(APIError) as caught:
            energy.ledger("alice", cursor, 50)
        assert caught.value.code == "invalid_cursor"
    assert not db.conn.in_transaction


def test_idempotent_replay_does_not_charge_or_rate_limit_and_mode_is_strict(setup):
    db, energy, chat, settings = setup
    settings.run_rate_per_minute = 1
    body, run, _ = start(db, chat, "fast")
    assert energy.wallet("alice").balance == 98
    replay, prompt = db.create_run("alice", chat.id, body, 1)
    assert replay == run and prompt is None
    with pytest.raises(APIError) as caught:
        db.create_run("alice", chat.id, body.model_copy(update={"mode": "economy"}), 120000)
    assert caught.value.code == "idempotency_conflict"
    db.finish(run.id, "completed")
    replay, _ = db.create_run("alice", chat.id, body, 1)
    assert replay.status == "completed"
    with pytest.raises(APIError) as caught:
        start(db, chat)
    assert caught.value.code == "rate_limited"
    assert energy.wallet("alice").balance == 98
    assert db.conn.execute("SELECT COUNT(*) FROM run_rate_attempts").fetchone()[0] == 1


@pytest.mark.parametrize("terminal", ["completed", "failed", "cancelled"])
def test_terminal_settles_or_refunds_once_with_canonical_event(setup, terminal):
    db, energy, chat, _ = setup
    _, run, _ = start(db, chat, "fast")
    assert energy.usage("alice", run.id).state == "reserved"
    db.append_delta(run.id, "partial")
    result = db.finish(run.id, terminal)
    assert db.finish(run.id, "completed") == result
    assert energy.wallet("alice").balance == (98 if terminal == "completed" else 100)
    assert energy.usage("alice", run.id).state == ("settled" if terminal == "completed" else "refunded")
    assert db.max_event_id(run.id) == 3
    assert len([x for x in energy.ledger("alice", None, 100)["items"] if x["kind"] == "run.refunded"]) == (terminal != "completed")


def test_event_failure_rolls_back_wallet_summary_rate_and_usage(setup, monkeypatch):
    db, energy, chat, _ = setup
    balance = energy.wallet("alice").balance
    def fail(*args):
        raise RuntimeError("simulated event failure")
    monkeypatch.setattr(db, "_event", fail)
    with pytest.raises(RuntimeError):
        start(db, chat)
    assert energy.wallet("alice").balance == balance
    for table in ["runs", "messages", "run_usage", "run_rate_attempts"]:
        assert db.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
    assert not db.conn.in_transaction


def test_terminal_event_failure_also_rolls_back_refund(setup, monkeypatch):
    db, energy, chat, _ = setup
    _, run, _ = start(db, chat)
    def fail(*args):
        raise RuntimeError("simulated terminal failure")
    monkeypatch.setattr(db, "_event", fail)
    with pytest.raises(RuntimeError):
        db.finish(run.id, "cancelled")
    assert db.run_internal(run.id).status == "running"
    assert energy.wallet("alice").balance == 99
    assert energy.usage("alice", run.id).state == "reserved"
    monkeypatch.undo()
    db.finish(run.id, "cancelled")
    assert energy.wallet("alice").balance == 100


def test_insufficient_energy_and_context_error_leave_no_generation(setup):
    db, energy, chat, settings = setup
    settings.initial_energy = 0
    with pytest.raises(APIError) as caught:
        start(db, chat)
    assert caught.value.status == 402
    for table in ["energy_wallets", "energy_ledger", "run_usage", "runs"]:
        assert db.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
    settings.initial_energy = 100
    with pytest.raises(APIError) as caught:
        db.create_run("alice", chat.id, RunInput(clientMessageId=uuid4(), content="hello"), 1)
    assert caught.value.code == "context_too_large"
    assert energy.wallet("alice").balance == 100


def test_restart_refunds_reserved_usage_and_delete_keeps_noncontent_accounting(tmp_path):
    path = str(tmp_path / "accounting.sqlite3")
    db = Database(path)
    chat = db.create_conversation("alice", ConversationInput())
    _, run, _ = start(db, chat)
    db.close()
    db = Database(path)
    energy = EnergyRepository(db, db.settings)
    try:
        db.recover_interrupted_runs()
        db.recover_interrupted_runs()
        assert energy.wallet("alice").balance == 100
        assert energy.usage("alice", run.id).state == "refunded"
        db.delete_conversation("alice", chat.id)
        assert energy.usage("alice", run.id).cost == 1
        assert "hello" not in str(dict(db.conn.execute("SELECT * FROM run_usage").fetchone()))
        assert energy.overview().refundedEnergy == 1
        with pytest.raises(APIError):
            energy.usage("bob", run.id)
    finally:
        db.close()


def test_cdk_hash_only_single_use_idempotency_disable_and_expiry(setup):
    db, energy, _, _ = setup
    issued = energy.issue(CdkIssue(energy=12, count=3))
    listed = energy.list_cdks(None, 1)
    assert len(listed["items"]) == 1 and listed["nextCursor"]
    code = issued[0].code
    assert code not in str([dict(x) for x in db.conn.execute("SELECT * FROM energy_cdks")])
    assert hashlib.sha256(code.encode()).hexdigest() in str([dict(x) for x in db.conn.execute("SELECT * FROM energy_cdks")])
    assert energy.redeem("alice", code).balance == 112
    assert energy.redeem("alice", code).balance == 112
    with pytest.raises(APIError) as caught:
        energy.redeem("bob", code)
    assert caught.value.code == "cdk_used"
    energy.disable_cdk(issued[1].prefix)
    with pytest.raises(APIError):
        energy.redeem("alice", issued[1].code)
    with db.atomic():
        db.conn.execute("UPDATE energy_cdks SET expiresAt = 0 WHERE prefix = ?", (issued[2].prefix,))
    with pytest.raises(APIError):
        energy.redeem("alice", issued[2].code)
    assert code not in str([dict(x) for x in db.conn.execute("SELECT * FROM admin_audit")])


def test_refund_headroom_prevents_cdk_from_breaking_pending_cancel(setup):
    db, energy, chat, _ = setup
    energy.wallet("alice")
    with db.atomic():
        db.conn.execute("UPDATE energy_wallets SET balance = ? WHERE userId = 'alice'", (MAX_BALANCE,))
    _, run, _ = start(db, chat)
    code = energy.issue(CdkIssue(energy=1))[0].code
    with pytest.raises(APIError) as caught:
        energy.redeem("alice", code)
    assert caught.value.code == "balance_limit"
    db.finish(run.id, "cancelled")
    assert energy.wallet("alice").balance == MAX_BALANCE
    row = db.conn.execute("SELECT redeemedBy FROM energy_cdks").fetchone()
    assert row[0] is None


class FailedRoute:
    def __init__(self, partial=False):
        self.partial = partial
        self.calls = 0
    async def stream(self, messages):
        self.calls += 1
        if self.partial:
            yield "fast partial"
        raise ProviderError("never expose a secret")


@pytest.mark.parametrize("configured,partial,fallback", [(False, False, True), (True, False, True), (True, True, True), (True, False, False), (False, False, False)])
def test_route_fallback_only_before_delta_and_refunds_price_difference(setup, configured, partial, fallback):
    db, energy, chat, settings = setup
    settings.fallback_to_economy = fallback
    _, run, prompt = start(db, chat, "fast")
    fast = FailedRoute(partial) if configured else None
    async def check():
        runtime = RunRuntime(db, MockProvider(), settings, fast)
        runtime.start(run.id, prompt)
        await asyncio.gather(*runtime.tasks.values())
        await runtime.close()
    asyncio.run(check())
    success = fallback and not partial
    final = db.run_internal(run.id)
    usage = energy.usage("alice", run.id)
    assert final.status == ("completed" if success else "failed")
    assert energy.wallet("alice").balance == (99 if success else 100)
    assert usage.actualMode == ("economy" if success else "fast")
    assert usage.cost == (1 if success else 2)
    assert usage.state == ("settled" if success else "refunded")
    assert usage.durationMs is not None
    assert db.message(run.assistantMessageId).content == ("Mock response." if success else "fast partial" if partial else "")
    assert "never expose" not in str(db.events(run.id, 0))
    expected_refund = 1 if success else 2
    assert energy.overview().refundedEnergy == expected_refund
    refund_deltas = [entry["delta"] for entry in energy.ledger("alice", None, 100)["items"]
                     if entry["kind"] in {"run.refunded", "run.route_refunded"}]
    assert sum(refund_deltas) == expected_refund
    if fast:
        assert fast.calls == 1


def test_failed_economy_after_fast_fallback_reports_both_refunds(setup):
    db, energy, chat, settings = setup
    _, run, prompt = start(db, chat, "fast")
    async def check():
        runtime = RunRuntime(db, FailedRoute(), settings, FailedRoute())
        runtime.start(run.id, prompt)
        await asyncio.gather(*runtime.tasks.values())
        await runtime.close()
    asyncio.run(check())
    assert db.run_internal(run.id).status == "failed"
    assert energy.wallet("alice").balance == 100
    usage = energy.usage("alice", run.id)
    assert usage.cost == 1 and usage.state == "refunded" and usage.actualMode == "economy"
    refunds = [entry["delta"] for entry in energy.ledger("alice", None, 100)["items"]
               if entry["kind"] in {"run.refunded", "run.route_refunded"}]
    assert sum(refunds) == energy.overview().refundedEnergy == 2
    db.delete_conversation("alice", chat.id)
    assert energy.overview().refundedEnergy == 2


def test_api_routes_terms_cdk_csrf_and_owner_scope(tmp_path):
    settings = Settings(_env_file=None, database_path=str(tmp_path / "api.sqlite3"), allow_dev_auth=True, admin_enabled=True)
    with TestClient(create_app(settings), base_url="http://127.0.0.1:8000", client=("127.0.0.1", 45000)) as client:
        token = client.post("/api/v1/dev/session", json={"userId": "alice"}).json()["accessToken"]
        auth = {"Authorization": f"Bearer {token}"}
        assert client.get("/api/v1/terms").json()["adultOnly"] is True
        assert len(client.get("/api/v1/models").json()["items"]) == 2
        assert client.get("/api/v1/energy", headers=auth).json()["balance"] == 100
        assert client.post("/api/v1/admin/cdks", json={"energy": 10}, headers=auth).status_code == 401
        from allira.admin_db import AdminRepository
        repo = AdminRepository(client.app.state.db)
        repo.issue_login("x" * 43, 300)
        login = client.post("/api/v1/admin/session", json={"code": "x" * 43}, headers={"Origin": "http://127.0.0.1:8000"}).json()
        csrf = {"Origin": "http://127.0.0.1:8000", "X-CSRF-Token": login["csrfToken"]}
        assert client.post("/api/v1/admin/cdks", json={"energy": 10}).status_code == 403
        result = client.post("/api/v1/admin/cdks", json={"energy": 10}, headers=csrf)
        assert result.status_code == 200
        item = result.json()["items"][0]
        assert client.post("/api/v1/energy/redeem", json={"code": item["code"]}, headers=auth).json()["balance"] == 110
        assert "code" not in client.get("/api/v1/admin/cdks").json()["items"][0]
        assert client.get("/api/v1/admin/usage").json()["measurement"] == "characters_not_tokens"
        assert client.post(f"/api/v1/admin/cdks/{item['prefix']}/disable", headers=csrf).status_code == 200
