"""Scenario module loading."""

from __future__ import annotations

import importlib.util
from pathlib import Path

from veriload.users import VeriUser


class ScenarioLoadError(Exception):
    """Raised when a scenario module or user class cannot be loaded."""


def load_user_class(path: str | Path, user_class: str) -> type[VeriUser]:
    """Load a VeriUser subclass from a Python scenario file."""

    scenario_path = Path(path)
    if not scenario_path.exists():
        raise ScenarioLoadError(f"Scenario file not found: {scenario_path}")

    module_name = f"veriload_scenario_{scenario_path.stem}_{abs(hash(scenario_path))}"
    spec = importlib.util.spec_from_file_location(module_name, scenario_path)
    if spec is None or spec.loader is None:
        raise ScenarioLoadError(f"Unable to import scenario file: {scenario_path}")

    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        raise ScenarioLoadError(f"Scenario import failed: {exc}") from exc

    loaded = getattr(module, user_class, None)
    if loaded is None:
        raise ScenarioLoadError(f"Scenario does not define user class {user_class!r}")
    if not isinstance(loaded, type) or not issubclass(loaded, VeriUser):
        raise ScenarioLoadError(f"{user_class!r} must be a VeriUser subclass")
    return loaded
