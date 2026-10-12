import asyncio

import pytest

from orchestrator.common.retry import backoff_delays, retry
from orchestrator.controller.supervisor import Loop, Supervisor, failure_delay


class Transient(Exception):
    pass


def test_backoff_sequence_is_1_2_4_8():
    assert backoff_delays(1, 2, 60, 4) == [1, 2, 4, 8]
    assert backoff_delays(1, 2, 5, 5) == [1, 2, 4, 5, 5]


def test_retry_succeeds_after_transient_failures():
    calls, sleeps = [], []

    def flaky():
        calls.append(1)
        if len(calls) < 3:
            raise Transient
        return "ok"

    assert retry(flaky, retry_on=(Transient,), attempts=4, sleep=sleeps.append, jitter=0) == "ok"
    assert sleeps == [1, 2]


def test_retry_gives_up_after_max_attempts():
    calls = []

    def always():
        calls.append(1)
        raise Transient

    with pytest.raises(Transient):
        retry(always, retry_on=(Transient,), attempts=3, sleep=lambda s: None)
    assert len(calls) == 3


def test_permanent_errors_are_not_retried():
    calls = []

    def permanent():
        calls.append(1)
        raise ValueError

    with pytest.raises(ValueError):
        retry(permanent, retry_on=(Transient,), attempts=5, sleep=lambda s: None)
    assert calls == [1]


def test_failure_delay_exponential_and_capped():
    assert failure_delay(2, 0, 30) == 2
    assert [failure_delay(2, n, 30) for n in (1, 2, 3, 10)] == [4, 8, 16, 30]


async def test_supervisor_survives_failures_and_stops_gracefully():
    state = {"runs": 0}

    def flaky():
        state["runs"] += 1
        if state["runs"] <= 2:
            raise RuntimeError("database down")

    sup = Supervisor([Loop("t", 0.01, flaky)], max_backoff=0.05)
    sup.start()
    await asyncio.sleep(0.4)
    await sup.stop(timeout=2)
    assert state["runs"] > 3  # kept running after the two failures
    runs_after_stop = state["runs"]
    await asyncio.sleep(0.1)
    assert state["runs"] == runs_after_stop  # truly stopped
