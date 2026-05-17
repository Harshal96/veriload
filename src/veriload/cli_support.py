"""Shared CLI rendering and file-output helpers."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from veriload.compare import ComparisonResult
from veriload.config import ReportsConfig, load_config
from veriload.data import PersonaPool
from veriload.dataset_studio import DatasetValidation, FieldExplanation
from veriload.metrics import RunSummary
from veriload.replay import ReplayPlan, write_replay_manifest
from veriload.reporting import write_json_report, write_junit_report, write_trace_report
from veriload.runtime import build_persona_pool
from veriload.safety import assert_persona_pool_safe
from veriload.slo import SloResult

console = Console()


def _summary_table(summary: RunSummary, *, workers: int = 1) -> Table:
    table = Table(show_header=True, header_style="bold")
    table.add_column("Metric")
    table.add_column("Value", justify="right")
    table.add_row("Workers", str(workers))
    table.add_row("Total Requests", str(summary.total_requests))
    table.add_row("Total Failures", str(summary.total_failures))
    table.add_row("Error Rate", f"{summary.error_rate:.2%}")
    table.add_row("p95 Latency", f"{summary.latency_ms.p95:.2f} ms")
    return table


def _dataset_validation_table(validation: DatasetValidation) -> Table:
    table = Table(show_header=True, header_style="bold")
    table.add_column("Metric")
    table.add_column("Value", justify="right")
    table.add_row("Total Personas", str(validation.total_personas))
    table.add_row("Unique Emails", str(validation.unique_emails))
    table.add_row("Duplicate Emails", str(len(validation.duplicate_emails)))
    table.add_row("Locales", json.dumps(validation.locales, sort_keys=True))
    table.add_row("Industries", json.dumps(validation.industries, sort_keys=True))
    return table


def _persona_preview_table(pool: PersonaPool, *, limit: int) -> Table:
    table = Table(show_header=True, header_style="bold")
    table.add_column("ID")
    table.add_column("Locale")
    table.add_column("Name")
    table.add_column("Email")
    table.add_column("Industry")
    for persona in pool.personas[:limit]:
        table.add_row(
            persona.persona_id,
            persona.locale,
            persona.person.name,
            persona.contact.email,
            persona.job.industry,
        )
    return table


def _field_explanation_table(explanation: FieldExplanation) -> Table:
    table = Table(show_header=True, header_style="bold")
    table.add_column("Field")
    table.add_column("Value")
    table.add_row("Path", explanation.field_path)
    table.add_row("Value", str(explanation.value))
    table.add_row("Dependencies", ", ".join(explanation.dependencies) or "none")
    table.add_row("Note", explanation.note)
    return table


def _slo_breach_table(slo_result: SloResult) -> Table:
    table = Table(show_header=True, header_style="bold red")
    table.add_column("Scope")
    table.add_column("Metric")
    table.add_column("Observed", justify="right")
    table.add_column("Threshold", justify="right")
    for breach in slo_result.breaches:
        table.add_row(
            breach.scope,
            breach.metric,
            f"{breach.observed:.4g}",
            f"{breach.threshold:.4g}",
        )
    return table


def _comparison_table(result: ComparisonResult) -> Table:
    table = Table(show_header=True, header_style="bold red")
    table.add_column("Scope")
    table.add_column("Metric")
    table.add_column("Baseline", justify="right")
    table.add_column("Current", justify="right")
    table.add_column("Delta", justify="right")
    table.add_column("Allowed", justify="right")
    for regression in result.regressions:
        table.add_row(
            regression.scope,
            regression.metric,
            f"{regression.baseline:.4g}",
            f"{regression.current:.4g}",
            f"{regression.delta:.4g}",
            f"{regression.threshold:.4g}",
        )
    return table


def _comparison_summary_text(result: ComparisonResult) -> str:
    worst = result.worst_regression
    worst_text = ""
    if worst is not None:
        worst_text = (
            f" Worst: {worst.scope} {worst.metric} "
            f"delta {worst.delta:.4g} over allowed {worst.threshold:.4g}."
        )
    return (
        f"{result.regression_count} regression(s) across "
        f"{len(result.affected_scopes)} scope(s).{worst_text}"
    )


def _replay_plan_table(plan: ReplayPlan) -> Table:
    table = Table(show_header=True, header_style="bold")
    table.add_column("Field")
    table.add_column("Value")
    table.add_row("Persona ID", plan.persona.persona_id)
    table.add_row("Email", plan.persona.contact.email)
    table.add_row("Base URL", plan.base_url)
    table.add_row("Scenario", f"{plan.scenario.get('path', '')}:{plan.scenario.get('user_class', '')}")
    table.add_row("Requests", ", ".join(event.get("name", "") for event in plan.events) or "none")
    return table


def _write_reports(
    reports: ReportsConfig | None,
    summary: RunSummary,
    slo_result: SloResult,
    *,
    events: tuple,
    replay_manifest: dict,
    workers: tuple = (),
    base_dir: Path,
) -> None:
    if reports is None:
        return
    if reports.json_path is not None:
        write_json_report(
            _resolve_report_path(reports.json_path, base_dir),
            summary,
            slo_result,
            workers=workers,
        )
    if reports.junit_path is not None:
        write_junit_report(_resolve_report_path(reports.junit_path, base_dir), slo_result)
    if reports.trace_path is not None:
        write_trace_report(_resolve_report_path(reports.trace_path, base_dir), events)
    if reports.replay_path is not None:
        write_replay_manifest(_resolve_report_path(reports.replay_path, base_dir), replay_manifest)


def _resolve_report_path(path: Path, base_dir: Path) -> Path:
    return path if path.is_absolute() else base_dir / path


def _load_safe_pool(config_path: Path) -> tuple[object, PersonaPool]:
    loaded = load_config(config_path)
    pool = build_persona_pool(loaded)
    assert_persona_pool_safe(
        pool,
        require_non_routable_contacts=loaded.safety.require_non_routable_contacts,
    )
    return loaded, pool


def _write_generated_scenario(output: Path, source: str) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(source, encoding="utf-8")
    console.print(
        Panel(
            f"Generated scenario at {output}",
            title="[bold green]Generated scenario[/bold green]",
            border_style="green",
        )
    )


def asdict_like(value: object) -> object:
    """Convert pydantic/dataclass-like values to plain JSON-compatible data."""

    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    try:
        return asdict(value)  # type: ignore[call-overload]
    except TypeError:
        return value
