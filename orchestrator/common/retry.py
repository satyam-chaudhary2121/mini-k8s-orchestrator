"""Exponential backoff with jitter. Permanent failures are never retried."""

from __future__ import annotations

import logging
import random
import time
from collections.abc import Callable

log = logging.getLogger(__name__)


def backoff_delays(base: float, factor: float, maximum: float, attempts: int) -> list[float]:
    """Deterministic delay sequence, e.g. base=1 -> 1, 2, 4, 8 (capped at maximum)."""
    return [min(maximum, base * factor**i) for i in range(attempts)]


def retry[T](
    func: Callable[[], T],
    *,
    retry_on: tuple[type[BaseException], ...],
    attempts: int = 4,
    base: float = 1.0,
    maximum: float = 30.0,
    jitter: float = 0.1,
    sleep: Callable[[float], None] = time.sleep,
    operation: str = "operation",
) -> T:
    """Call ``func``; on ``retry_on`` errors wait 1s, 2s, 4s... then re-raise the last error.

    Exceptions not listed in ``retry_on`` are treated as permanent and propagate immediately.
    """
    delays = backoff_delays(base, 2.0, maximum, max(attempts - 1, 0))
    for attempt in range(attempts):
        try:
            return func()
        except retry_on as exc:
            if attempt == attempts - 1:
                raise
            delay = delays[attempt] * (1 + random.uniform(0, jitter))  # noqa: S311
            log.warning(
                "transient failure, retrying",
                extra={
                    "operation": operation,
                    "attempt": attempt + 1,
                    "retry_in": round(delay, 2),
                    "error": str(exc),
                },
            )
            sleep(delay)
    raise AssertionError("unreachable")  # pragma: no cover
