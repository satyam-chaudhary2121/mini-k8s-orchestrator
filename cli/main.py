from __future__ import annotations

import uuid
from pathlib import Path
from typing import Annotated, Any

import typer

from cli.client import ApiClient, CliError
from cli.manifest import ManifestError, load_manifests, to_request

app = typer.Typer(help="Mini Kubernetes-inspired orchestrator CLI", no_args_is_help=True)
nodes_app = typer.Typer(help="Worker nodes", no_args_is_help=True)
deployments_app = typer.Typer(help="Deployments", no_args_is_help=True)
pods_app = typer.Typer(help="Pods", no_args_is_help=True)
services_app = typer.Typer(help="Services", no_args_is_help=True)
for name, sub in (
    ("nodes", nodes_app),
    ("deployments", deployments_app),
    ("pods", pods_app),
    ("services", services_app),
):
    app.add_typer(sub, name=name)


@app.callback()
def main(
    ctx: typer.Context,
    url: Annotated[str, typer.Option(envvar="ORCHESTRATOR_URL")] = "http://localhost:8000",
    api_key: Annotated[str | None, typer.Option(envvar="ORCHESTRATOR_API_KEY")] = None,
) -> None:
    ctx.obj = {"url": url, "api_key": api_key}


def _api(ctx: typer.Context) -> ApiClient:
    return ApiClient(ctx.obj["url"], ctx.obj["api_key"])


def table(rows: list[list[Any]], headers: list[str]) -> None:
    cells = [headers, *[["-" if c is None else str(c) for c in r] for r in rows]]
    widths = [max(len(r[i]) for r in cells) for i in range(len(headers))]
    for row in cells:
        typer.echo("  ".join(c.ljust(w) for c, w in zip(row, widths, strict=True)).rstrip())


def _run(func: Any) -> Any:
    try:
        return func()
    except (CliError, ManifestError, OSError) as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from exc


# ---- nodes
@nodes_app.command("list")
def nodes_list(ctx: typer.Context) -> None:
    page = _run(lambda: _api(ctx).request("GET", "/api/v1/nodes"))
    table(
        [
            [
                n["hostname"],
                n["status"],
                f"{n['cpu_allocated']:.2f}/{n['cpu_capacity']:g}",
                f"{n['memory_allocated_mb']}/{n['memory_capacity_mb']}",
                n["pod_count"],
                n["last_heartbeat_at"],
            ]
            for n in page["items"]
        ],
        ["NAME", "STATUS", "CPU", "MEM(MiB)", "PODS", "LAST HEARTBEAT"],
    )


@nodes_app.command("drain")
def nodes_drain(ctx: typer.Context, name: str) -> None:
    node = _run(lambda: _api(ctx).request("POST", f"/api/v1/nodes/{name}/drain"))
    typer.echo(f"node/{node['hostname']} draining")


@nodes_app.command("uncordon")
def nodes_uncordon(ctx: typer.Context, name: str) -> None:
    node = _run(lambda: _api(ctx).request("POST", f"/api/v1/nodes/{name}/uncordon"))
    typer.echo(f"node/{node['hostname']} uncordoned ({node['status']})")


@nodes_app.command("delete")
def nodes_delete(ctx: typer.Context, name: str, force: bool = False) -> None:
    _run(lambda: _api(ctx).request("DELETE", f"/api/v1/nodes/{name}", params={"force": force}))
    typer.echo(f"node/{name} deleted")


# ---- deploy / scale
@app.command("deploy")
def deploy(ctx: typer.Context, file: Path) -> None:
    """Apply a YAML manifest (Deployment and/or Service documents)."""

    def go() -> None:
        api = _api(ctx)
        for doc in load_manifests(file.read_text()):
            path, body = to_request(doc)
            result = api.request(
                "POST", path, json=body, headers={"Idempotency-Key": uuid.uuid4().hex}
            )
            typer.echo(f"{doc['kind'].lower()}/{result['name']} applied")

    _run(go)


@app.command("scale")
def scale(ctx: typer.Context, name: str, replicas: Annotated[int, typer.Option(min=0)]) -> None:
    result = _run(
        lambda: _api(ctx).request(
            "POST",
            f"/api/v1/deployments/{name}/scale",
            json={"replicas": replicas},
            headers={"Idempotency-Key": uuid.uuid4().hex},
        )
    )
    typer.echo(f"deployment/{name} scaled to {result['desired_replicas']}")


@deployments_app.command("list")
def deployments_list(ctx: typer.Context) -> None:
    page = _run(lambda: _api(ctx).request("GET", "/api/v1/deployments"))
    table(
        [
            [d["name"], f"{d['ready_replicas']}/{d['desired_replicas']}", d["image"], d["status"]]
            for d in page["items"]
        ],
        ["NAME", "READY", "IMAGE", "STATUS"],
    )


@deployments_app.command("delete")
def deployments_delete(ctx: typer.Context, name: str) -> None:
    _run(lambda: _api(ctx).request("DELETE", f"/api/v1/deployments/{name}"))
    typer.echo(f"deployment/{name} deleting")


# ---- pods
@pods_app.command("list")
def pods_list(
    ctx: typer.Context,
    status: str | None = None,
    node: str | None = None,
    deployment: str | None = None,
) -> None:
    params = {
        k: v for k, v in {"status": status, "node": node, "deployment": deployment}.items() if v
    }
    page = _run(lambda: _api(ctx).request("GET", "/api/v1/pods", params=params))
    table(
        [
            [
                p["name"],
                p["node"],
                p["status"],
                "yes" if p["ready"] else "no",
                p["restart_count"],
                p["reason"],
            ]
            for p in page["items"]
        ],
        ["NAME", "NODE", "STATUS", "READY", "RESTARTS", "REASON"],
    )


@pods_app.command("logs")
def pods_logs(ctx: typer.Context, name: str, tail: int = 100) -> None:
    typer.echo(
        _run(lambda: _api(ctx).request("GET", f"/api/v1/pods/{name}/logs", params={"tail": tail})),
        nl=False,
    )


@pods_app.command("delete")
def pods_delete(ctx: typer.Context, name: str) -> None:
    _run(lambda: _api(ctx).request("DELETE", f"/api/v1/pods/{name}"))
    typer.echo(f"pod/{name} terminating")


# ---- services
@services_app.command("list")
def services_list(ctx: typer.Context) -> None:
    page = _run(lambda: _api(ctx).request("GET", "/api/v1/services"))
    table(
        [[s["name"], s["port"], s["target_port"], s["selector"]] for s in page["items"]],
        ["NAME", "PORT", "TARGET", "SELECTOR"],
    )


@services_app.command("get")
def services_get(ctx: typer.Context, name: str) -> None:
    svc = _run(lambda: _api(ctx).request("GET", f"/api/v1/services/{name}"))
    typer.echo(f"{svc['name']}  port={svc['port']} target={svc['target_port']}")
    table(
        [[e["pod"], f"{e['address']}:{e['port']}"] for e in svc["endpoints"]],
        ["ENDPOINT POD", "ADDRESS"],
    )


@services_app.command("resolve")
def services_resolve(ctx: typer.Context, name: str) -> None:
    e = _run(lambda: _api(ctx).request("GET", f"/api/v1/services/{name}/resolve"))
    typer.echo(f"{e['address']}:{e['port']}  ({e['pod']})")


@services_app.command("delete")
def services_delete(ctx: typer.Context, name: str) -> None:
    _run(lambda: _api(ctx).request("DELETE", f"/api/v1/services/{name}"))
    typer.echo(f"service/{name} deleted")


@app.command("events")
def events(ctx: typer.Context, limit: int = 20, type: str | None = None) -> None:
    page = _run(
        lambda: _api(ctx).request(
            "GET", "/api/v1/events", params={"limit": limit, **({"type": type} if type else {})}
        )
    )
    table(
        [[e["created_at"], e["type"], e["message"]] for e in page["items"]],
        ["TIME", "TYPE", "MESSAGE"],
    )


if __name__ == "__main__":
    app()
