"""Lifecycle-managed background loops with retry/backoff and graceful shutdown."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import random
from collections.abc import Callable
from dataclasses import dataclass

from orchestrator import metrics

log = logging.getLogger("orchestrator.supervisor")


@dataclass(frozen=True)
class Loop:
    name: str
    interval: float
    run_once: Callable[[], object]


def failure_delay(interval: float, failures: int, maximum: float) -> float:
    """interval * 2^failures, capped. 0 failures -> plain interval."""
    return min(maximum, interval * 2**failures) if failures else interval


class Supervisor:
    """Runs each controller loop as an asyncio task. The (blocking, DB-bound) ``run_once`` executes
    in a worker thread; failures back off exponentially with jitter and never kill the loop."""

    def __init__(self, loops: list[Loop], *, max_backoff: float = 30.0) -> None:
        self._loops = loops
        self._max_backoff = max_backoff
        self._stop = asyncio.Event()
        self._tasks: list[asyncio.Task[None]] = []

    def start(self) -> None:
        self._stop.clear()
        self._tasks = [asyncio.create_task(self._run(loop), name=loop.name) for loop in self._loops]

    async def stop(self, timeout: float = 10.0) -> None:
        self._stop.set()
        _, pending = (
            await asyncio.wait(self._tasks, timeout=timeout) if self._tasks else (set(), set())
        )
        for task in pending:
            task.cancel()
        for task in pending:
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async def _run(self, loop: Loop) -> None:
        failures = 0
        while not self._stop.is_set():
            try:
                await asyncio.to_thread(loop.run_once)
                metrics.RECONCILIATIONS.labels(loop.name).inc()
                failures = 0
            except asyncio.CancelledError:
                raise
            except Exception:
                failures += 1
                metrics.RECONCILIATION_FAILURES.labels(loop.name).inc()
                log.exception(
                    "reconciliation failed",
                    extra={"controller": loop.name, "consecutive_failures": failures},
                )
            delay = failure_delay(loop.interval, failures, self._max_backoff)
            delay *= 1 + random.uniform(0, 0.1) if failures else 1  # noqa: S311
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._stop.wait(), timeout=delay)
