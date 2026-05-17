"""Scenario import CLI commands."""

from __future__ import annotations

from pathlib import Path

import typer

from veriload.cli_support import _write_generated_scenario
from veriload.importers import (
    generate_scenario_from_curl,
    generate_scenario_from_har,
    generate_scenario_from_openapi,
    generate_scenario_from_postman,
)

import_app = typer.Typer(help="Generate editable scenario skeletons from existing API artifacts.")


@import_app.command(name="curl")
def import_curl_command(
    command: str = typer.Argument(..., help="Quoted cURL command."),
    output: Path = typer.Option(..., "--output", "-o", help="Destination Python scenario file."),
    class_name: str = typer.Option("ImportedUser", "--class-name", help="Generated VeriUser class name."),
) -> None:
    """Generate a scenario skeleton from a cURL command."""

    _write_generated_scenario(
        output,
        generate_scenario_from_curl(command, class_name=class_name),
    )


@import_app.command(name="openapi")
def import_openapi_command(
    source: Path = typer.Argument(..., help="OpenAPI JSON/YAML file."),
    output: Path = typer.Option(..., "--output", "-o", help="Destination Python scenario file."),
    class_name: str = typer.Option("ImportedUser", "--class-name", help="Generated VeriUser class name."),
) -> None:
    """Generate a scenario skeleton from OpenAPI."""

    _write_generated_scenario(
        output,
        generate_scenario_from_openapi(source, class_name=class_name),
    )


@import_app.command(name="har")
def import_har_command(
    source: Path = typer.Argument(..., help="HAR file."),
    output: Path = typer.Option(..., "--output", "-o", help="Destination Python scenario file."),
    class_name: str = typer.Option("ImportedUser", "--class-name", help="Generated VeriUser class name."),
) -> None:
    """Generate a scenario skeleton from a HAR recording."""

    _write_generated_scenario(
        output,
        generate_scenario_from_har(source, class_name=class_name),
    )


@import_app.command(name="postman")
def import_postman_command(
    source: Path = typer.Argument(..., help="Postman collection JSON file."),
    output: Path = typer.Option(..., "--output", "-o", help="Destination Python scenario file."),
    class_name: str = typer.Option("ImportedUser", "--class-name", help="Generated VeriUser class name."),
) -> None:
    """Generate a scenario skeleton from a Postman collection."""

    _write_generated_scenario(
        output,
        generate_scenario_from_postman(source, class_name=class_name),
    )


