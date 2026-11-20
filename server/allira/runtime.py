"""Single-process run supervision, independent from SSE subscribers."""

import asyncio
import time
from contextlib import aclosing, suppress

from .config import Settings
from .db import Database
from .economy import EnergyRepository
from .providers import ModelProvider, PromptMessage, ProviderError
from .schemas import Run


class RunRuntime:
    def __init__(self, db: Database, provider: ModelProvider, settings: Settings,
                 fast_provider: ModelProvider | None = None) -> None:
        self.db, self.provider, self.settings = db, provider, settings
        self.fast_provider = fast_provider
        self.tasks: dict[str, asyncio.Task[None]] = {}

    def start(self, run_id: str, prompt: list[PromptMessage]) -> None:
        task = asyncio.create_task(self._generate(run_id, prompt), name=f"allira-run-{run_id}")
        self.tasks[run_id] = task
        task.add_done_callback(lambda done: self.tasks.pop(run_id, None))

    def in_flight_run_ids(self) -> set[str]:
        # Terminal/refunded tasks still occupy a physical slot until upstream cleanup ends.
        return {run_id for run_id, task in self.tasks.items() if not task.done()}

    def _running(self, run_id: str) -> bool:
        row = self.db.conn.execute("SELECT status FROM runs WHERE id = ?", (run_id,)).fetchone()
        return bool(row and row["status"] == "running")

    def _can_generate(self, run_id: str) -> bool:
        task = asyncio.current_task()
        if task and task.cancelling():
            # Restore cancellation swallowed/replaced by upstream aclose(), including timeout.
            # The enclosing asyncio.timeout must see it to persist upstream_timeout.
            raise asyncio.CancelledError
        return self._running(run_id)

    def _finish_running(self, run_id: str, status: str, error_code: str | None = None) -> None:
        # Terminal content may be deleted while asynchronous upstream cleanup is pending.
        if self._running(run_id):
            self.db.finish(run_id, status, error_code)

    async def _generate(self, run_id: str, prompt: list[PromptMessage]) -> None:
        energy = EnergyRepository(self.db, self.settings)
        started, output_characters = time.monotonic(), 0
        try:
            if not self._can_generate(run_id):
                return
            usage = self.db.conn.execute("SELECT requestedMode FROM run_usage WHERE runId = ?", (run_id,)).fetchone()
            mode = usage["requestedMode"] if usage else "economy"
            selected = self.fast_provider if mode == "fast" else self.provider
            if selected is None:
                if not self.settings.fallback_to_economy:
                    raise ProviderError("The requested route is not configured.")
                mode, selected = "economy", self.provider
            energy.select_route(run_id, mode)
            async with asyncio.timeout(self.settings.run_timeout_seconds):
                while True:
                    if not self._can_generate(run_id):
                        return
                    try:
                        async with aclosing(selected.stream(prompt)) as stream:
                            async for delta in stream:
                                if self.settings.mock_chunk_delay_seconds:
                                    await asyncio.sleep(self.settings.mock_chunk_delay_seconds)
                                if not self._can_generate(run_id):
                                    return
                                if not isinstance(delta, str):
                                    raise ProviderError("Provider delta must be text.")
                                if output_characters + len(delta) > self.settings.max_output_characters:
                                    self.db.finish(run_id, "failed", "output_too_large")
                                    return
                                output_characters += len(delta)
                                if delta:
                                    energy.metrics(run_id, output=output_characters,
                                                   first_token_ms=int((time.monotonic() - started) * 1000))
                                    self.db.append_delta(run_id, delta)
                                # Even deterministic providers must yield for explicit cancellation.
                                await asyncio.sleep(0)
                        if not self._can_generate(run_id):
                            return  # A provider may swallow cancellation and return ordinary EOF.
                        break
                    except Exception as exc:
                        # aclose() can replace CancelledError with any cleanup exception.
                        # Restore cancellation before timeout exits or a new route can start.
                        if not self._can_generate(run_id):
                            return
                        if not isinstance(exc, (ProviderError, TimeoutError)):
                            raise
                        # Never mix responses after a visible delta. No retry of the same paid route.
                        if mode != "fast" or output_characters or not self.settings.fallback_to_economy:
                            raise
                        mode, selected = "economy", self.provider
                        energy.select_route(run_id, mode)
            self._finish_running(run_id, "completed")
        except asyncio.CancelledError:
            self._finish_running(run_id, "cancelled")
            raise
        except TimeoutError:
            self._finish_running(run_id, "failed", "upstream_timeout")
        except ProviderError:
            self._finish_running(run_id, "failed", "upstream_error")
        except Exception:  # noqa: BLE001 -- isolate provider failures at the background-task boundary
            self._finish_running(run_id, "failed", "internal_error")
        finally:
            # Usage survives deletion of chat content and contains no prompt/response text.
            energy.metrics(run_id, output=min(output_characters, self.settings.max_output_characters),
                           duration_ms=int((time.monotonic() - started) * 1000))

    async def cancel(self, run_id: str) -> Run:
        result = self.db.finish(run_id, "cancelled")
        task = self.tasks.get(run_id)
        if task:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        return result

    async def close(self) -> None:
        tasks = list(self.tasks.items())
        for run_id, task in tasks:
            # A cancelled/failed run can be deleted while upstream close is still awaiting.
            row = self.db.conn.execute("SELECT status FROM runs WHERE id = ?", (run_id,)).fetchone()
            if row and row["status"] == "running":
                self.db.finish(run_id, "failed", "server_shutdown")
            task.cancel()
        if tasks:
            await asyncio.gather(*(task for _, task in tasks), return_exceptions=True)
