"""Translate YAML manifests (apiVersion/kind/metadata/spec) into API request bodies."""

from __future__ import annotations

import re
from typing import Any

import yaml

API_VERSION = "orchestrator/v1"
_UNITS = {"Mi": 1, "Gi": 1024, "M": 1, "G": 1024}


class ManifestError(ValueError):
    pass


def parse_memory(value: Any) -> int:
    """Memory in MiB: 256, "256", "256Mi" or "1Gi"."""
    if isinstance(value, int):
        return value
    match = re.fullmatch(r"(\d+)\s*(Mi|Gi|M|G)?", str(value).strip())
    if not match:
        raise ManifestError(f"invalid memory value {value!r}")
    return int(match.group(1)) * _UNITS[match.group(2) or "Mi"]


def load_manifests(text: str) -> list[dict[str, Any]]:
    docs = [d for d in yaml.safe_load_all(text) if d]
    if not docs:
        raise ManifestError("manifest is empty")
    return docs


def _require(doc: dict[str, Any], *path: str) -> Any:
    node: Any = doc
    for key in path:
        if not isinstance(node, dict) or key not in node:
            raise ManifestError(f"missing field {'.'.join(path)}")
        node = node[key]
    return node


def _env(raw: Any) -> dict[str, str]:
    if isinstance(raw, dict):
        return {str(k): str(v) for k, v in raw.items()}
    return {str(item["name"]): str(item["value"]) for item in raw or []}


def deployment_body(doc: dict[str, Any]) -> dict[str, Any]:
    name = _require(doc, "metadata", "name")
    spec = _require(doc, "spec")
    tpl = _require(doc, "spec", "template")
    resources = tpl.get("resources", {})
    body: dict[str, Any] = {
        "name": name,
        "image": _require(doc, "spec", "template", "image"),
        "replicas": spec.get("replicas", 1),
        "ports": [
            {"container_port": p["containerPort"], "protocol": p.get("protocol", "TCP")}
            for p in tpl.get("ports", [])
        ],
        "environment": _env(tpl.get("env") or tpl.get("environment")),
        "args": tpl.get("args", []),
        "restart_policy": tpl.get("restartPolicy", "Always"),
        "labels": doc.get("metadata", {}).get("labels", {}),
        "node_selector": spec.get("nodeSelector", {}),
    }
    if "cpu" in resources:
        body["cpu_request"] = float(resources["cpu"])
    if "memory" in resources:
        body["memory_request"] = parse_memory(resources["memory"])
    if tpl.get("command"):
        body["command"] = tpl["command"]
    if hc := tpl.get("healthCheck"):
        body["health_check"] = {
            "type": hc.get("type", "http"),
            "path": hc.get("path", "/"),
            "port": hc["port"],
            **{
                k: hc[s]
                for k, s in (
                    ("interval", "interval"),
                    ("timeout", "timeout"),
                    ("failure_threshold", "failureThreshold"),
                )
                if s in hc
            },
        }
    return body


def service_body(doc: dict[str, Any]) -> dict[str, Any]:
    spec = _require(doc, "spec")
    return {
        "name": _require(doc, "metadata", "name"),
        "selector": _require(doc, "spec", "selector"),
        "port": spec["port"],
        "target_port": spec.get("targetPort", spec["port"]),
    }


def to_request(doc: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """Returns (api path, body) for one manifest document."""
    if doc.get("apiVersion") != API_VERSION:
        raise ManifestError(f"apiVersion must be {API_VERSION!r}")
    kind = doc.get("kind")
    if kind == "Deployment":
        return "/api/v1/deployments", deployment_body(doc)
    if kind == "Service":
        return "/api/v1/services", service_body(doc)
    raise ManifestError(f"unsupported kind {kind!r}")
