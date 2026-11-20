"""Run lifecycle tests stay on one event loop and never call external services."""

import asyncio
from uuid import uuid4

import pytest

from allira.config import Settings
from allira.db import Database
from allira.providers import ProviderError
from allira.runtime import RunRuntime
from allira.schemas import ConversationInput, RunInput


class BlockingProvider:
    def __init__(self):
        self.started = asyncio.Event()
        self.closed = False

    async def stream(self, messages):
        try:
            yield "partial"
            self.started.set()
            await asyncio.Event().wait()
        finally:
            self.closed = True


def setup(provider, **settings):
    db = Database(":memory:")
    chat = db.create_conversation("alice", ConversationInput())
    run, prompt = db.create_run(
        "alice", chat.id, RunInput(clientMessageId=uuid4(), content="hello"), 120000,
    )
    runtime = RunRuntime(db, provider, Settings(**settings))
    runtime.start(run.id, prompt)
    return db, runtime, run


def test_explicit_cancel_preserves_partial_and_closes_provider():
    async def check():
        provider = BlockingProvider()
        db, runtime, run = setup(provider)
        try:
            await provider.started.wait()
            await runtime.cancel(run.id)
            assert provider.closed
            assert db.run_internal(run.id).status == "cancelled"
            message = db.message(run.assistantMessageId)
            assert message.status == "cancelled"
            assert message.content == "partial"
            assert db.events(run.id, 2)[0]["type"] == "run.cancelled"
            await runtime.cancel(run.id)
            assert db.max_event_id(run.id) == 3
        finally:
            await runtime.close()
            db.close()
    asyncio.run(check())


def test_cancel_snapshot_survives_concurrent_delete_during_provider_cleanup():
    class SlowClosingProvider:
        def __init__(self):
            self.started = asyncio.Event()
            self.closing = asyncio.Event()
            self.release = asyncio.Event()

        async def stream(self, messages):
            try:
                yield "partial"
                self.started.set()
                await asyncio.Event().wait()
            finally:
                self.closing.set()
                await self.release.wait()

    async def check():
        provider = SlowClosingProvider()
        db, runtime, run = setup(provider)
        try:
            await asyncio.wait_for(provider.started.wait(), timeout=1)
            cancel_task = asyncio.create_task(runtime.cancel(run.id))
            await asyncio.wait_for(provider.closing.wait(), timeout=1)
            db.delete_conversation("alice", run.conversationId)
            provider.release.set()
            snapshot = await asyncio.wait_for(cancel_task, timeout=1)
            assert snapshot.status == "cancelled"
            assert db.conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 0
            from allira.economy import EnergyRepository
            energy = EnergyRepository(db, runtime.settings)
            assert energy.wallet("alice").balance == 100
            assert energy.usage("alice", run.id).state == "refunded"
        finally:
            provider.release.set()
            await runtime.close()
            db.close()
    asyncio.run(check())


@pytest.mark.parametrize("error_type", [ProviderError, TimeoutError, RuntimeError])
@pytest.mark.parametrize("delete_content", [False, True])
@pytest.mark.parametrize("operation", ["cancel", "shutdown"])
def test_cleanup_errors_never_restart_terminal_fast_run(error_type, delete_content, operation):
    class ClosingErrorProvider:
        def __init__(self):
            self.started = asyncio.Event()
            self.closing = asyncio.Event()
            self.release = asyncio.Event()

        async def stream(self, messages):
            try:
                self.started.set()
                await asyncio.Event().wait()
                yield "unreachable"
            finally:
                self.closing.set()
                await self.release.wait()
                raise error_type("synthetic-close-error-must-not-escape")

    class EconomyProvider:
        calls = 0

        async def stream(self, messages):
            self.calls += 1
            yield "must-not-run-after-refund"

    async def check():
        from allira.economy import EnergyRepository

        settings = Settings(_env_file=None, provider="mock")
        db = Database(":memory:", settings)
        chat = db.create_conversation("alice", ConversationInput())
        run, prompt = db.create_run("alice", chat.id, RunInput(
            clientMessageId=uuid4(), content="hello", mode="fast"), 120000)
        fast, economy = ClosingErrorProvider(), EconomyProvider()
        runtime = RunRuntime(db, economy, settings, fast_provider=fast)
        runtime.start(run.id, prompt)
        try:
            await asyncio.wait_for(fast.started.wait(), timeout=1)
            action = runtime.cancel(run.id) if operation == "cancel" else runtime.close()
            action_task = asyncio.create_task(action)
            await asyncio.wait_for(fast.closing.wait(), timeout=1)
            if delete_content:
                db.delete_conversation("alice", chat.id)
            fast.release.set()
            snapshot = await asyncio.wait_for(action_task, timeout=1)
            if operation == "cancel":
                assert snapshot.status == "cancelled"
            if not delete_content:
                terminal = db.run_internal(run.id)
                assert terminal.status == ("cancelled" if operation == "cancel" else "failed")
                assert db.max_event_id(run.id) == 2
                assert db.message(run.assistantMessageId).content == ""
            energy = EnergyRepository(db, settings)
            assert economy.calls == 0
            assert energy.wallet("alice").balance == 100
            usage = energy.usage("alice", run.id)
            assert usage.state == "refunded"
            assert usage.actualMode == "fast"
            assert usage.outputCharacters == 0
            assert not db.conn.execute(
                "SELECT 1 FROM energy_ledger WHERE kind = 'run.route_refunded'"
            ).fetchone()
        finally:
            fast.release.set()
            await runtime.close()
            db.close()
    asyncio.run(check())


@pytest.mark.parametrize("error_type", [ProviderError, TimeoutError, RuntimeError])
def test_timeout_cleanup_error_never_starts_fallback_after_deadline(error_type):
    class TimeoutClosingProvider:
        async def stream(self, messages):
            try:
                await asyncio.Event().wait()
                yield "unreachable"
            finally:
                raise error_type("safe-synthetic-close-error")

    class EconomyProvider:
        calls = 0

        async def stream(self, messages):
            self.calls += 1
            yield "must-not-run-after-deadline"

    async def check():
        settings = Settings(_env_file=None, run_timeout_seconds=0.01)
        db = Database(":memory:", settings)
        chat = db.create_conversation("alice", ConversationInput())
        run, prompt = db.create_run("alice", chat.id, RunInput(
            clientMessageId=uuid4(), content="hello", mode="fast"), 120000)
        economy = EconomyProvider()
        runtime = RunRuntime(db, economy, settings, fast_provider=TimeoutClosingProvider())
        runtime.start(run.id, prompt)
        try:
            await asyncio.wait_for(asyncio.gather(*runtime.tasks.values()), timeout=1)
            assert economy.calls == 0
            assert db.run_internal(run.id).status == "failed"
            assert db.run_internal(run.id).errorCode == "upstream_timeout"
            from allira.economy import EnergyRepository
            assert EnergyRepository(db, settings).wallet("alice").balance == 100
        finally:
            await runtime.close()
            db.close()
    asyncio.run(check())


def test_provider_swallowing_deadline_cannot_complete_and_charge():
    class SwallowedDeadline:
        async def stream(self, messages):
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                return
            yield "unreachable"

    async def check():
        from allira.economy import EnergyRepository
        db, runtime, run = setup(SwallowedDeadline(), run_timeout_seconds=0.01)
        try:
            await asyncio.wait_for(asyncio.gather(*runtime.tasks.values()), timeout=1)
            assert db.run_internal(run.id).status == "failed"
            assert db.run_internal(run.id).errorCode == "upstream_timeout"
            assert EnergyRepository(db, runtime.settings).usage("alice", run.id).state == "refunded"
            assert EnergyRepository(db, runtime.settings).wallet("alice").balance == 100
        finally:
            await runtime.close()
            db.close()
    asyncio.run(check())


def test_shutdown_cleans_task_even_if_terminal_content_was_deleted():
    async def check():
        provider = BlockingProvider()
        db, runtime, run = setup(provider)
        try:
            await asyncio.wait_for(provider.started.wait(), timeout=1)
            db.finish(run.id, "cancelled")
            db.delete_conversation("alice", run.conversationId)
            await runtime.close()
            assert provider.closed
            assert not runtime.tasks
        finally:
            await runtime.close()
            db.close()
    asyncio.run(check())


def test_shutdown_records_failure_and_closes_provider():
    async def check():
        provider = BlockingProvider()
        db, runtime, run = setup(provider)
        try:
            await provider.started.wait()
            await runtime.close()
            assert provider.closed
            assert db.run_internal(run.id).status == "failed"
            assert db.run_internal(run.id).errorCode == "server_shutdown"
            assert db.message(run.assistantMessageId).content == "partial"
        finally:
            await runtime.close()
            db.close()
    asyncio.run(check())


def test_timeout_records_failure_and_closes_provider():
    async def check():
        provider = BlockingProvider()
        db, runtime, run = setup(provider, run_timeout_seconds=0.01)
        try:
            await asyncio.wait_for(asyncio.gather(*runtime.tasks.values()), timeout=1)
            assert provider.closed
            assert db.run_internal(run.id).errorCode == "upstream_timeout"
            assert db.run_internal(run.id).status == "failed"
        finally:
            await runtime.close()
            db.close()
    asyncio.run(check())


def test_provider_failure_is_safe_and_preserves_partial():
    class FailingProvider:
        async def stream(self, messages):
            yield "partial"
            raise ProviderError("a-secret-upstream-key")

    async def check():
        db, runtime, run = setup(FailingProvider())
        try:
            await asyncio.gather(*runtime.tasks.values())
            assert db.run_internal(run.id).errorCode == "upstream_error"
            assert "a-secret-upstream-key" not in str(db.events(run.id, 0))
            assert db.message(run.assistantMessageId).content == "partial"
        finally:
            await runtime.close()
            db.close()
    asyncio.run(check())


def test_output_limit_closes_provider_before_oversized_delta():
    class LargeProvider:
        def __init__(self):
            self.closed = False

        async def stream(self, messages):
            try:
                yield "abc"
                yield "def"
            finally:
                self.closed = True

    async def check():
        provider = LargeProvider()
        db, runtime, run = setup(provider, max_output_characters=5)
        try:
            await asyncio.gather(*runtime.tasks.values())
            assert db.run_internal(run.id).errorCode == "output_too_large"
            assert db.message(run.assistantMessageId).content == "abc"
            assert provider.closed
        finally:
            await runtime.close()
            db.close()
    asyncio.run(check())


def test_cancel_before_generator_starts_is_terminal():
    async def check():
        provider = BlockingProvider()
        db, runtime, run = setup(provider)
        try:
            await runtime.cancel(run.id)
            assert db.run_internal(run.id).status == "cancelled"
            assert db.max_event_id(run.id) == 2
            assert not provider.started.is_set()
        finally:
            await runtime.close()
            db.close()
    asyncio.run(check())
