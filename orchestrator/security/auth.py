from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import StrEnum

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from orchestrator.config.settings import Settings
from orchestrator.persistence.models import ApiKey


class Role(StrEnum):
    ADMIN = "ADMIN"
    OPERATOR = "OPERATOR"
    READ_ONLY = "READ_ONLY"
    WORKER = "WORKER"


class Permission(StrEnum):
    NODES_READ = "nodes:read"
    NODES_OPERATE = "nodes:operate"  # drain / uncordon
    NODES_ADMIN = "nodes:admin"  # delete
    DEPLOYMENTS_READ = "deployments:read"
    DEPLOYMENTS_WRITE = "deployments:write"
    PODS_READ = "pods:read"
    PODS_WRITE = "pods:write"
    SERVICES_READ = "services:read"
    SERVICES_WRITE = "services:write"
    LOGS_READ = "logs:read"
    EVENTS_READ = "events:read"
    AGENT = "agent:node"  # register, heartbeat, assignments, status reports


P = Permission
_READ = {P.NODES_READ, P.DEPLOYMENTS_READ, P.PODS_READ, P.SERVICES_READ, P.LOGS_READ, P.EVENTS_READ}
ROLE_PERMISSIONS: dict[Role, frozenset[Permission]] = {
    Role.ADMIN: frozenset(Permission),
    Role.OPERATOR: frozenset(
        _READ | {P.NODES_OPERATE, P.DEPLOYMENTS_WRITE, P.PODS_WRITE, P.SERVICES_WRITE}
    ),
    Role.READ_ONLY: frozenset(_READ),
    Role.WORKER: frozenset({P.AGENT}),
}


@dataclass(frozen=True)
class Principal:
    name: str
    role: Role

    def can(self, permission: Permission) -> bool:
        return permission in ROLE_PERMISSIONS[self.role]


def hash_key(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


class AuthenticationProvider(ABC):
    @abstractmethod
    def authenticate(self, session: Session, token: str) -> Principal | None: ...


class ApiKeyAuthProvider(AuthenticationProvider):
    """Keys are stored only as SHA-256 hashes; the raw secrets live in the environment."""

    def authenticate(self, session: Session, token: str) -> Principal | None:
        row = session.scalars(select(ApiKey).where(ApiKey.key_hash == hash_key(token))).first()
        return Principal(row.name, Role(row.role)) if row else None


def bootstrap_api_keys(session: Session, settings: Settings) -> None:
    """Sync env-provided keys into the api_keys table (rotate by changing env and restarting)."""
    # Serialise concurrent replicas starting up (transaction-scoped; released on commit).
    session.execute(text("SELECT pg_advisory_xact_lock(727001)"))
    configured = {
        Role.ADMIN: settings.admin_api_key,
        Role.OPERATOR: settings.operator_api_key,
        Role.READ_ONLY: settings.read_only_api_key,
        Role.WORKER: settings.worker_api_key,
    }
    for role, secret in configured.items():
        name = f"bootstrap-{role.value.lower()}"
        row = session.scalars(select(ApiKey).where(ApiKey.name == name)).first()
        if secret is None:
            if row:
                session.delete(row)
            continue
        digest = hash_key(secret.get_secret_value())
        if row is None:
            session.add(ApiKey(name=name, role=role.value, key_hash=digest))
        else:
            row.key_hash, row.role = digest, role.value
    session.flush()
