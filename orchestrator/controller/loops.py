from __future__ import annotations

from orchestrator.config.settings import Settings
from orchestrator.controller import deployments, endpoints, nodes, pods
from orchestrator.controller.supervisor import Loop
from orchestrator.persistence.db import Database
from orchestrator.scheduler.service import schedule_pending
from orchestrator.scheduler.strategies import get_strategy


def build_loops(db: Database, settings: Settings) -> list[Loop]:
    strategy = get_strategy(settings.scheduler_strategy)
    interval = settings.reconciliation_interval
    return [
        Loop("node-monitor", interval, lambda: nodes.reconcile_nodes(db, settings)),
        Loop(
            "deployment-controller",
            interval,
            lambda: deployments.reconcile_deployments(db, settings),
        ),
        Loop("scheduler", interval, lambda: schedule_pending(db, strategy)),
        Loop("pod-controller", interval, lambda: pods.reconcile_pods(db, settings)),
        Loop("endpoint-controller", interval, lambda: endpoints.reconcile_endpoints(db, settings)),
    ]
