"""Scenario template CLI commands."""

from __future__ import annotations

from pathlib import Path

import typer
from rich.panel import Panel

from veriload.cli_support import console
from veriload.templates import write_auth_lifecycle_template

template_app = typer.Typer(help="Generate CLI-first scenario templates.")


@template_app.command(name="auth-lifecycle")
def auth_lifecycle_command(
    output_dir: Path = typer.Option(..., "--output-dir", "-o", help="Destination template directory."),
    base_url: str = typer.Option(..., "--base-url", help="Target staging or test API base URL."),
    user_class: str = typer.Option(
        "AuthenticatedObjectUser",
        "--user-class",
        help="Generated VeriUser class name.",
    ),
    overwrite: bool = typer.Option(False, "--overwrite", help="Overwrite existing template files."),
) -> None:
    """Generate an authenticated object lifecycle scenario template."""

    written = write_auth_lifecycle_template(
        output_dir,
        base_url=base_url,
        user_class=user_class,
        overwrite=overwrite,
    )
    console.print(
        Panel(
            f"Auth lifecycle template written to {output_dir} ({len(written)} file(s) updated).",
            title="[bold green]Template written[/bold green]",
            border_style="green",
        )
    )
