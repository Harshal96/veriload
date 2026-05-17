"""Dataset inspection CLI commands."""

from __future__ import annotations

from pathlib import Path

import typer
from rich.panel import Panel

from veriload.cli_support import (
    _dataset_validation_table,
    _field_explanation_table,
    _load_safe_pool,
    _persona_preview_table,
    console,
)
from veriload.config import ConfigError
from veriload.dataset_studio import (
    explain_persona_field,
    export_personas_jsonl,
    validate_dataset,
)
from veriload.safety import SafetyError

dataset_app = typer.Typer(help="Inspect, validate, explain, and export generated personas.")


@dataset_app.command(name="preview")
def dataset_preview_command(
    config: Path = typer.Option(
        Path("veriload.yaml"),
        "--config",
        "-c",
        help="Path to the VeriLoad YAML configuration file.",
    ),
    limit: int = typer.Option(5, "--limit", "-n", min=1, help="Number of personas to preview."),
) -> None:
    """Preview generated personas without sending traffic."""

    try:
        _, pool = _load_safe_pool(config)
    except (ConfigError, SafetyError) as exc:
        console.print(Panel(str(exc), title="[bold red]Dataset invalid[/bold red]", border_style="red"))
        raise typer.Exit(1) from exc
    console.print(
        Panel(
            _persona_preview_table(pool, limit=limit),
            title="[bold cyan]Persona Preview[/bold cyan]",
            border_style="cyan",
        )
    )


@dataset_app.command(name="validate")
def dataset_validate_command(
    config: Path = typer.Option(
        Path("veriload.yaml"),
        "--config",
        "-c",
        help="Path to the VeriLoad YAML configuration file.",
    ),
) -> None:
    """Validate generated persona uniqueness and segment distributions."""

    try:
        _, pool = _load_safe_pool(config)
    except (ConfigError, SafetyError) as exc:
        console.print(Panel(str(exc), title="[bold red]Dataset invalid[/bold red]", border_style="red"))
        raise typer.Exit(1) from exc
    validation = validate_dataset(pool)
    title = "[bold green]Dataset valid[/bold green]" if validation.valid else "[bold red]Dataset invalid[/bold red]"
    console.print(
        Panel(
            _dataset_validation_table(validation),
            title=title,
            border_style="green" if validation.valid else "red",
        )
    )
    if not validation.valid:
        raise typer.Exit(1)


@dataset_app.command(name="export")
def dataset_export_command(
    output: Path = typer.Option(..., "--output", "-o", help="Destination JSONL file."),
    config: Path = typer.Option(
        Path("veriload.yaml"),
        "--config",
        "-c",
        help="Path to the VeriLoad YAML configuration file.",
    ),
) -> None:
    """Export the generated persona pool as JSONL."""

    try:
        _, pool = _load_safe_pool(config)
    except (ConfigError, SafetyError) as exc:
        console.print(Panel(str(exc), title="[bold red]Dataset export failed[/bold red]", border_style="red"))
        raise typer.Exit(1) from exc
    export_personas_jsonl(output, pool)
    console.print(
        Panel(
            f"Exported {len(pool.personas)} personas to {output}",
            title="[bold green]Dataset exported[/bold green]",
            border_style="green",
        )
    )


@dataset_app.command(name="explain")
def dataset_explain_command(
    field_path: str = typer.Argument(..., help="Dotted persona field path, e.g. contact.email."),
    config: Path = typer.Option(
        Path("veriload.yaml"),
        "--config",
        "-c",
        help="Path to the VeriLoad YAML configuration file.",
    ),
    persona_index: int = typer.Option(0, "--persona-index", min=0, help="Persona index to inspect."),
) -> None:
    """Explain a generated field value and its known dependencies."""

    try:
        _, pool = _load_safe_pool(config)
        persona = pool.personas[persona_index]
        explanation = explain_persona_field(persona, field_path)
    except (ConfigError, SafetyError, AttributeError, IndexError) as exc:
        console.print(Panel(str(exc), title="[bold red]Explain failed[/bold red]", border_style="red"))
        raise typer.Exit(1) from exc
    console.print(
        Panel(
            _field_explanation_table(explanation),
            title="[bold cyan]Field Explanation[/bold cyan]",
            border_style="cyan",
        )
    )

