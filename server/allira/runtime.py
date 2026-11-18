"""Single-process run supervision, independent from SSE subscribers."""

import asyncio
from contextlib import aclosing, suppress

from .config import Settings
from .db import Database
from .providers import ModelProvider, PromptMessage, ProviderError


class RunRuntime:
    def __init__(self, db: Database, provider: ModelProvider, settings: Settings) -> None:
        self.db, self.provider, self.settings = db, provider, settings
        self.tasks: dict[str, asyncio.Task[None]] = {}

    def start(self, run_id: str, prompt: list[PromptMessage]) -> None:
        task = asyncio.create_task(self._generate(run_id, prompt), name=f"allira-run-{run_id}")
        self.tasks[run_id] = task
        task.add_done_callback(lambda done: self.tasks.pop(run_id, None))

    async def _generate(self, run_id: str, prompt: list[PromptMessage]) -> None:
        try:
            async with asyncio.timeout(self.settings.run_timeout_seconds):
                async with aclosing(self.provider.stream(prompt)) as stream:
                    output_characters = 0
                    async for delta in stream:
                        if not isinstance(delta, str):
                            raise ProviderError("Provider delta must be text.")
                        output_characters += len(delta)
                        if output_characters > self.settings.max_output_characters:
                            self.db.finish(run_id, "failed", "output_too_large")
                            return
                        if delta:
                            self.db.append_delta(run_id, delta)
                        # Even deterministic/in-memory providers must yield for cancellation.
                        await asyncio.sleep(0)
            self.db.finish(run_id, "completed")
        except asyncio.CancelledError:
            # Explicit cancel/shutdown persist their chosen terminal state first.
            self.db.finish(run_id, "cancelled")
            raise
        except TimeoutError:
            self.db.finish(run_id, "failed", "upstream_timeout")
        except ProviderError:
            self.db.finish(run_id, "failed", "upstream_error")
        except Exception:  # noqa: BLE001 -- isolate provider failures at the background-task boundary
            # Never expose upstream exception strings, keys, request URLs or prompts.
            self.db.finish(run_id, "failed", "internal_error")

    async def cancel(self, run_id: str) -> None:
        self.db.finish(run_id, "cancelled")
        task = self.tasks.get(run_id)
        if task:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

    async def close(self) -> None:
        tasks = list(self.tasks.items())
        for run_id, task in tasks:
            self.db.finish(run_id, "failed", "server_shutdown")
            task.cancel()
        if tasks:
            await asyncio.gather(*(task for _, task in tasks), return_exceptions=True)
