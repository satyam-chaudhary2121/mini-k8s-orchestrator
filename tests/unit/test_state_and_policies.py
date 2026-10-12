from types import SimpleNamespace

import pytest

from orchestrator.config.settings import Settings
from orchestrator.controller.deployments import pick_victims
from orchestrator.controller.pods import is_restartable, restart_delay
from orchestrator.persistence.models import PodStatus
from orchestrator.services.pods import POD_TRANSITIONS, can_transition

S = PodStatus


def test_every_state_is_defined_and_terminated_is_terminal():
    assert set(POD_TRANSITIONS) == set(PodStatus)
    assert POD_TRANSITIONS[S.TERMINATED] == frozenset()


@pytest.mark.parametrize(
    "a,b",
    [
        (S.PENDING, S.SCHEDULED),
        (S.SCHEDULED, S.CREATING),
        (S.CREATING, S.RUNNING),
        (S.RUNNING, S.UNHEALTHY),
        (S.UNHEALTHY, S.RUNNING),
        (S.RUNNING, S.FAILED),
        (S.FAILED, S.SCHEDULED),
        (S.TERMINATING, S.TERMINATED),
    ],
)
def test_allowed_transitions(a, b):
    assert can_transition(a, b)


@pytest.mark.parametrize(
    "a,b",
    [
        (S.PENDING, S.RUNNING),
        (S.TERMINATED, S.RUNNING),
        (S.RUNNING, S.PENDING),
        (S.FAILED, S.RUNNING),
        (S.TERMINATING, S.RUNNING),
        (S.CREATING, S.UNHEALTHY),
    ],
)
def test_forbidden_transitions(a, b):
    assert not can_transition(a, b)


def test_every_live_state_can_reach_terminated():
    def reachable(start):
        seen, stack = set(), [start]
        while stack:
            cur = stack.pop()
            for nxt in POD_TRANSITIONS[cur]:
                if nxt not in seen:
                    seen.add(nxt)
                    stack.append(nxt)
        return seen

    for state in PodStatus:
        if state is not S.TERMINATED:
            assert S.TERMINATED in reachable(state), state


def test_restart_policy_rules():
    assert is_restartable("Always", 0, False)
    assert is_restartable("OnFailure", 1, False) and not is_restartable("OnFailure", 0, False)
    assert not is_restartable("Never", 1, False)
    assert not is_restartable("Always", 1, True)  # permanent failures never restart


def test_restart_backoff_doubles_and_caps():
    assert [restart_delay(n, 2, 300) for n in range(4)] == [2, 4, 8, 16]
    assert restart_delay(20, 2, 300) == 300


def test_scale_down_victim_order():
    from datetime import UTC, datetime, timedelta

    now = datetime.now(UTC)

    def pod(name, status, age):
        return SimpleNamespace(
            name=name, status=status.value, created_at=now - timedelta(minutes=age)
        )

    pods = [
        pod("run-old", S.RUNNING, 30),
        pod("run-new", S.RUNNING, 1),
        pod("failed", S.FAILED, 5),
        pod("pending", S.PENDING, 2),
    ]
    assert [p.name for p in pick_victims(pods, 3)] == ["failed", "pending", "run-new"]


def test_settings_require_database_and_admin_key(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("ADMIN_API_KEY", raising=False)
    with pytest.raises(Exception, match="database_url|admin_api_key"):
        Settings(_env_file=None)
