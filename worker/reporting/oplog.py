from __future__ import annotations

import logging
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

log = logging.getLogger("worker.ops")


@contextmanager
def operation(
    name: str,
    *,
    node_id: str | None = None,
    pod_id: str | None = None,
    container_id: str | None = None,
) -> Iterator[dict[str, Any]]:
    """One structured log line per operation: ids, operation, result, duration."""
    ctx: dict[str, Any] = {"container_id": container_id}
    start = time.perf_counter()
    result = "ok"
    try:
        yield ctx
    except Exception as exc:
        result = f"error: {type(exc).__name__}"
        raise
    finally:
        log.info(
            name,
            extra={
                "node_id": node_id,
                "pod_id": pod_id,
                "container_id": ctx["container_id"],
                "operation": name,
                "result": result,
                "duration_ms": round((time.perf_counter() - start) * 1000, 2),
            },
        )
