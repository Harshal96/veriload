"""Kubernetes CLI commands."""

from __future__ import annotations

from pathlib import Path

import typer
from rich.panel import Panel

from veriload.cli_support import console
from veriload.k8s_operator.manifest import generate_operator_install_manifest
from veriload.kubernetes import generate_kubernetes_manifest

k8s_app = typer.Typer(help="Generate Kubernetes resources for VeriLoad runs.")


@k8s_app.command(name="manifest")
def k8s_manifest_command(
    config: Path = typer.Option(
        Path("veriload.yaml"),
        "--config",
        "-c",
        help="Path to the VeriLoad YAML configuration file.",
    ),
    output: Path = typer.Option(..., "--output", "-o", help="Destination manifest YAML file."),
    name: str = typer.Option("veriload", "--name", help="Kubernetes resource name prefix."),
    image: str = typer.Option(..., "--image", help="Container image containing VeriLoad and scenarios."),
    workers: int = typer.Option(1, "--workers", min=1, help="Number of worker pods."),
    networked: bool = typer.Option(False, "--networked", help="Generate true controller/worker resources."),
    cluster_token_secret: str = typer.Option(
        "veriload-cluster-token",
        "--cluster-token-secret",
        help="Kubernetes Secret containing a token key for networked mode.",
    ),
) -> None:
    """Generate Kubernetes controller and worker job manifests."""

    manifest = generate_kubernetes_manifest(
        name=name,
        image=image,
        config_text=config.read_text(encoding="utf-8"),
        workers=workers,
        networked=networked,
        cluster_token_secret=cluster_token_secret,
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(manifest, encoding="utf-8")
    console.print(
        Panel(
            f"Kubernetes manifest written to {output}",
            title="[bold green]Kubernetes manifest written[/bold green]",
            border_style="green",
        )
    )


@k8s_app.command(name="operator-manifest")
def k8s_operator_manifest_command(
    output: Path = typer.Option(..., "--output", "-o", help="Destination operator install YAML file."),
    name: str = typer.Option("veriload-operator", "--name", help="Operator resource name."),
    namespace: str = typer.Option("veriload-system", "--namespace", help="Operator namespace."),
    image: str = typer.Option(..., "--image", help="Container image containing VeriLoad operator dependencies."),
) -> None:
    """Generate Kubernetes CRD, RBAC, and Deployment manifests for the operator."""

    manifest = generate_operator_install_manifest(name=name, namespace=namespace, image=image)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(manifest, encoding="utf-8")
    console.print(
        Panel(
            f"Kubernetes operator manifest written to {output}",
            title="[bold green]Kubernetes operator manifest written[/bold green]",
            border_style="green",
        )
    )
