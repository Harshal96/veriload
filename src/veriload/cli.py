"""Command-line entry points for VeriLoad."""

from __future__ import annotations

import typer

from veriload.cli_commands.core import register_core_commands
from veriload.cli_commands.dataset import dataset_app
from veriload.cli_commands.distributed import distributed_app
from veriload.cli_commands.imports import import_app
from veriload.cli_commands.kubernetes import k8s_app
from veriload.cli_commands.operator import operator_app
from veriload.cli_commands.templates import template_app

app = typer.Typer(
    add_completion=False,
    help="Realistic-state load testing powered by deterministic VeriSim personas.",
    no_args_is_help=True,
)


@app.callback()
def root() -> None:
    """Realistic-state load testing powered by deterministic VeriSim personas."""


register_core_commands(app)
app.add_typer(dataset_app, name="dataset")
app.add_typer(distributed_app, name="distributed")
app.add_typer(import_app, name="import")
app.add_typer(k8s_app, name="k8s")
app.add_typer(operator_app, name="operator")
app.add_typer(template_app, name="template")


def main() -> None:
    """Console script entrypoint."""

    app()


if __name__ == "__main__":
    main()
