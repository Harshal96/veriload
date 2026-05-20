"""Top-level VeriLoad CLI commands."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import typer
from rich.json import JSON
from rich.panel import Panel

from veriload.cli_support import (
    _comparison_summary_text,
    _comparison_table,
    _persona_preview_table,
    _replay_plan_table,
    _slo_breach_table,
    _summary_table,
    _write_reports,
    asdict_like,
    console,
)
from veriload.compare import compare_run_reports, load_run_report
from veriload.config import ConfigError, load_config
from veriload.data import PersonaSourceError
from veriload.replay import build_replay_manifest, load_replay_plan
from veriload.runtime import build_persona_pool, execute_config
from veriload.safety import SafetyError, assert_persona_pool_safe
from veriload.scenarios import ScenarioLoadError
from veriload.slo import evaluate_slos


def register_core_commands(app: typer.Typer) -> None:
    """Register top-level commands on the root app."""

    app.command(name="validate")(validate_command)
    app.command(name="run")(run_command)
    app.command(name="compare")(compare_command)
    app.command(name="replay")(replay_command)


def validate_command(
    config: Path = typer.Option(
        Path("veriload.yaml"),
        "--config",
        "-c",
        help="Path to the VeriLoad YAML configuration file.",
    ),
    show_personas: int = typer.Option(
        0,
        "--show-personas",
        min=0,
        help="Preview the first N generated personas after validation.",
    ),
) -> None:
    """Validate a VeriLoad configuration file."""

    try:
        loaded = load_config(config)
        pool = build_persona_pool(loaded)
        assert_persona_pool_safe(
            pool,
            require_non_routable_contacts=loaded.safety.require_non_routable_contacts,
        )
    except (ConfigError, PersonaSourceError, SafetyError) as exc:
        console.print(
            Panel(
                str(exc),
                title="[bold red]Configuration invalid[/bold red]",
                border_style="red",
            )
        )
        raise typer.Exit(1) from exc

    rendered = json.dumps(asdict_like(loaded), indent=2, sort_keys=True)
    console.print(
        Panel(
            JSON(rendered),
            title="[bold green]Configuration valid[/bold green]",
            border_style="green",
        )
    )
    if show_personas:
        console.print(
            Panel(
                _persona_preview_table(pool, limit=show_personas),
                title="[bold cyan]Persona Preview[/bold cyan]",
                border_style="cyan",
            )
        )


def run_command(
    config: Path = typer.Option(
        Path("veriload.yaml"),
        "--config",
        "-c",
        help="Path to the VeriLoad YAML configuration file.",
    ),
    workers: int = typer.Option(
        1,
        "--workers",
        min=1,
        help="Number of in-process workers to use for this run.",
    ),
) -> None:
    """Execute a local VeriLoad scenario."""

    try:
        loaded = load_config(config)
        execution = asyncio.run(execute_config(loaded, config_dir=config.parent, workers=workers))
        summary = execution.summary
        slo_result = evaluate_slos(loaded.slo, summary)
        _write_reports(
            loaded.reports,
            summary,
            slo_result,
            events=execution.events,
            replay_manifest=build_replay_manifest(
                loaded,
                execution.pool,
                execution.events,
                workers=workers,
            ),
            cleanup=execution.cleanup,
            workers=execution.workers,
            base_dir=config.parent,
        )
    except (ConfigError, PersonaSourceError, SafetyError, ScenarioLoadError) as exc:
        console.print(
            Panel(str(exc), title="[bold red]Run failed[/bold red]", border_style="red")
        )
        raise typer.Exit(1) from exc

    if not execution.cleanup.passed:
        failures = "\n".join(
            f"{failure.kind} {failure.target}: {failure.error}"
            for failure in execution.cleanup.failures
        )
        console.print(
            Panel(
                failures or "Auto-cleanup failed.",
                title="[bold red]Cleanup failed[/bold red]",
                border_style="red",
            )
        )
        raise typer.Exit(1)

    if not slo_result.passed:
        console.print(
            Panel(
                _slo_breach_table(slo_result),
                title="[bold red]SLO breached[/bold red]",
                border_style="red",
            )
        )
        raise typer.Exit(1)

    console.print(
        Panel(
            _summary_table(summary, workers=workers),
            title="[bold green]Run complete[/bold green]",
            border_style="green",
        )
    )


def compare_command(
    baseline: Path = typer.Argument(..., help="Baseline JSON report."),
    current: Path = typer.Argument(..., help="Current JSON report."),
    max_p95_regression_ms: float = typer.Option(
        0,
        "--max-p95-regression-ms",
        min=0,
        help="Allowed p95 latency increase in milliseconds.",
    ),
    max_error_rate_regression: float = typer.Option(
        0,
        "--max-error-rate-regression",
        min=0,
        help="Allowed absolute error-rate increase.",
    ),
) -> None:
    """Compare a current JSON report against a baseline."""

    result = compare_run_reports(
        load_run_report(baseline),
        load_run_report(current),
        max_p95_regression_ms=max_p95_regression_ms,
        max_error_rate_regression=max_error_rate_regression,
    )
    if not result.passed:
        console.print(_comparison_summary_text(result))
        console.print(
            Panel(
                _comparison_table(result),
                title="[bold red]Regression detected[/bold red]",
                border_style="red",
            )
        )
        raise typer.Exit(1)
    console.print(
        Panel(
            "Current report is within configured regression thresholds.",
            title="[bold green]No regression[/bold green]",
            border_style="green",
        )
    )


def replay_command(
    manifest: Path = typer.Argument(..., help="Replay manifest generated by `veriload run`."),
    persona_id: str = typer.Option(..., "--persona-id", help="Persona ID to inspect/replay."),
) -> None:
    """Print a dry-run replay plan for one persona."""

    try:
        plan = load_replay_plan(manifest, persona_id=persona_id)
    except (ValueError, KeyError) as exc:
        console.print(Panel(str(exc), title="[bold red]Replay failed[/bold red]", border_style="red"))
        raise typer.Exit(1) from exc
    console.print(
        Panel(
            _replay_plan_table(plan),
            title="[bold cyan]Replay plan[/bold cyan]",
            border_style="cyan",
        )
    )
