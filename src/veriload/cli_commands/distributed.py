"""Networked distributed CLI commands."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import typer
from rich.panel import Panel

from veriload.cli_support import _summary_table, _write_reports, console
from veriload.config import ConfigError, load_config
from veriload.data import PersonaSourceError
from veriload.distributed_network import (
    NetworkDistributedController,
    NetworkDistributedError,
    NetworkDistributedWorker,
)
from veriload.replay import build_replay_manifest
from veriload.runtime import build_persona_pool
from veriload.safety import SafetyError
from veriload.slo import evaluate_slos

distributed_app = typer.Typer(help="Run VeriLoad across networked controller and worker processes.")


@distributed_app.command(name="controller")
def distributed_controller_command(
    config: Path = typer.Option(
        Path("veriload.yaml"),
        "--config",
        "-c",
        help="Path to the VeriLoad YAML configuration file.",
    ),
    bind_host: str = typer.Option("127.0.0.1", "--bind-host", help="Controller bind host."),
    bind_port: int = typer.Option(5557, "--bind-port", min=1, help="Controller bind port."),
    expect_workers: int = typer.Option(1, "--expect-workers", min=1, help="Workers required before starting."),
    cluster_token_env: str = typer.Option(
        "VERILOAD_CLUSTER_TOKEN",
        "--cluster-token-env",
        help="Environment variable containing the shared cluster token.",
    ),
    ready_timeout_seconds: float = typer.Option(
        30,
        "--ready-timeout-seconds",
        min=0.1,
        help="Seconds to wait for expected workers.",
    ),
) -> None:
    """Run the networked distributed controller."""

    try:
        loaded = load_config(config)
        token = _cluster_token(cluster_token_env)
        result = asyncio.run(
            NetworkDistributedController(
                config=loaded,
                config_path=config,
                bind_host=bind_host,
                bind_port=bind_port,
                expect_workers=expect_workers,
                cluster_token=token,
                ready_timeout_seconds=ready_timeout_seconds,
            ).run()
        )
        slo_result = evaluate_slos(loaded.slo, result.summary)
        _write_reports(
            loaded.reports,
            result.summary,
            slo_result,
            events=(),
            replay_manifest=build_replay_manifest(
                loaded,
                build_persona_pool(loaded),
                (),
                workers=expect_workers,
            ),
            cleanup=result.cleanup,
            workers=result.workers,
            base_dir=config.parent,
        )
    except (ConfigError, PersonaSourceError, SafetyError, NetworkDistributedError, TimeoutError) as exc:
        console.print(Panel(str(exc), title="[bold red]Controller failed[/bold red]", border_style="red"))
        raise typer.Exit(1) from exc

    if not result.cleanup.passed:
        failures = "\n".join(
            f"{failure.kind} {failure.target}: {failure.error}"
            for failure in result.cleanup.failures
        )
        console.print(Panel(failures, title="[bold red]Cleanup failed[/bold red]", border_style="red"))
        raise typer.Exit(1)

    if not slo_result.passed:
        console.print(Panel("SLO breached.", title="[bold red]SLO breached[/bold red]", border_style="red"))
        raise typer.Exit(1)
    console.print(
        Panel(
            _summary_table(result.summary, workers=expect_workers),
            title="[bold green]Distributed run complete[/bold green]",
            border_style="green",
        )
    )


@distributed_app.command(name="worker")
def distributed_worker_command(
    controller_host: str = typer.Option(..., "--controller-host", help="Controller hostname or IP."),
    controller_port: int = typer.Option(5557, "--controller-port", min=1, help="Controller port."),
    work_dir: Path = typer.Option(
        Path(".veriload/worker"),
        "--work-dir",
        help="Worker bundle cache and execution directory.",
    ),
    cluster_token_env: str = typer.Option(
        "VERILOAD_CLUSTER_TOKEN",
        "--cluster-token-env",
        help="Environment variable containing the shared cluster token.",
    ),
    node_id: str | None = typer.Option(None, "--node-id", help="Stable worker node identifier."),
) -> None:
    """Run a networked distributed worker."""

    try:
        result = asyncio.run(
            NetworkDistributedWorker(
                controller_host=controller_host,
                controller_port=controller_port,
                work_dir=work_dir,
                cluster_token=_cluster_token(cluster_token_env),
                node_id=node_id,
            ).run()
        )
    except (OSError, ConfigError, NetworkDistributedError, RuntimeError) as exc:
        console.print(Panel(str(exc), title="[bold red]Worker failed[/bold red]", border_style="red"))
        raise typer.Exit(1) from exc
    console.print(
        Panel(
            f"Worker {result.worker_index} completed {result.summary.total_requests} requests.",
            title="[bold green]Worker complete[/bold green]",
            border_style="green",
        )
    )


def _cluster_token(env_name: str) -> str:
    token = os.environ.get(env_name)
    if not token:
        raise ConfigError(f"{env_name} must be set for networked distributed mode")
    return token
