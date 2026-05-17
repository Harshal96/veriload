"""Operator runtime and utility commands."""

from __future__ import annotations

import json
from pathlib import Path

import typer

from veriload.k8s_operator.collector import collect_report_summary

operator_app = typer.Typer(help="Run VeriLoad Kubernetes Operator utilities.")


@operator_app.command(name="collect")
def collect_command(
    report_json: Path = typer.Option(..., "--report-json", help="Path to a VeriLoad JSON report."),
) -> None:
    """Print a compact JSON summary for operator status collection."""

    typer.echo(json.dumps(collect_report_summary(report_json), sort_keys=True))


@operator_app.command(name="run")
def run_command() -> None:
    """Run the Kopf-based VeriLoad operator."""

    from veriload.k8s_operator.runtime import run_operator

    run_operator()
