"""Run lifecycle tests stay on one event loop and never call external services."""

import asyncio
from uuid import uuid4

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
