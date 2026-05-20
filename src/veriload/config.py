"""Configuration loading and validation for VeriLoad."""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

import yaml
from pydantic import BaseModel, ConfigDict, Field, PositiveInt, model_validator


class ConfigError(Exception):
    """Raised when VeriLoad configuration is invalid."""


class RunDatabaseConfig(BaseModel):
    """Database target settings for database load tests."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    driver: Literal["sqlite"]
    dsn: str = Field(min_length=1)


class RunConfig(BaseModel):
    """Runtime execution settings."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    base_url: str | None = None
    database: RunDatabaseConfig | None = None
    users: PositiveInt
    spawn_rate: PositiveInt
    max_duration_seconds: PositiveInt


class CleanupHttpTargetConfig(BaseModel):
    """HTTP create target eligible for automatic cleanup."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    method: str = "POST"
    path: str = Field(min_length=1)
    delete_path: str | None = None
    id_fields: tuple[str, ...] = ("id",)


class CleanupHttpConfig(BaseModel):
    """HTTP auto-cleanup settings."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    targets: tuple[CleanupHttpTargetConfig, ...] = ()


class CleanupDatabaseConfig(BaseModel):
    """Database auto-cleanup settings."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    strategy: Literal["delete", "rollback"] = "delete"
    tables: tuple[str, ...] = ()


class CleanupConfig(BaseModel):
    """Auto-cleanup settings for resources created by load tests."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool = False
    http: CleanupHttpConfig = Field(default_factory=CleanupHttpConfig)
    database: CleanupDatabaseConfig = Field(default_factory=CleanupDatabaseConfig)


class ScenarioConfig(BaseModel):
    """Scenario module and user class to execute."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    path: Path
    user_class: str


class LocaleConfig(BaseModel):
    """Weighted locale entry for persona generation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    locale: str
    weight: float = Field(gt=0)


class DataConfig(BaseModel):
    """Synthetic-data settings."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    pool_size: PositiveInt
    seed: int
    locales: tuple[LocaleConfig, ...] = (LocaleConfig(locale="en_US", weight=1.0),)
    conflict_mode: str = "strict"
    source: Literal["verisim", "memory"] = "verisim"


class LoadProfileConfig(BaseModel):
    """Load-profile configuration shared by local and distributed runners."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    type: str
    target_users: int | None = Field(default=None, gt=0)
    duration_seconds: float = Field(gt=0)
    hold_seconds: float | None = Field(default=None, gt=0)
    tick_seconds: float = Field(default=1, ge=0)


class SafetyConfig(BaseModel):
    """Safety controls that prevent accidental unsafe runs."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    allowed_hosts: tuple[str, ...]
    max_users: int | None = Field(default=None, gt=0)
    max_rps: float | None = Field(default=None, gt=0)
    stop_file: Path | None = None
    require_non_routable_contacts: bool = True


class GlobalSloConfig(BaseModel):
    """Global SLO thresholds applied to the whole run."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    max_p95_ms: float | None = Field(default=None, gt=0)
    max_error_rate: float | None = Field(default=None, ge=0, le=1)


class EndpointSloConfig(BaseModel):
    """Endpoint-specific SLO thresholds."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    max_p95_ms: float | None = Field(default=None, gt=0)
    max_error_rate: float | None = Field(default=None, ge=0, le=1)


class SloConfig(BaseModel):
    """SLO gate configuration."""

    global_: GlobalSloConfig | None = Field(default=None, alias="global")
    endpoints: tuple[EndpointSloConfig, ...] = ()

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


class ReportsConfig(BaseModel):
    """Report output paths."""

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    json_path: Path | None = Field(default=None, alias="json")
    junit_path: Path | None = Field(default=None, alias="junit")
    trace_path: Path | None = Field(default=None, alias="trace")
    replay_path: Path | None = Field(default=None, alias="replay")


class VeriLoadConfig(BaseModel):
    """Top-level VeriLoad configuration."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    scenario: ScenarioConfig | None = None
    run: RunConfig
    data: DataConfig
    profile: LoadProfileConfig
    cleanup: CleanupConfig | None = None
    slo: SloConfig | None = None
    reports: ReportsConfig | None = None
    safety: SafetyConfig

    @model_validator(mode="before")
    @classmethod
    def default_profile_target_users(cls, data: Any) -> Any:
        """Treat run.users as the canonical profile target when omitted."""

        if not isinstance(data, Mapping):
            return data
        run_data = data.get("run")
        profile_data = data.get("profile")
        run_users = _run_users_from_input(run_data)
        if run_users is None:
            return data
        if isinstance(profile_data, Mapping):
            if profile_data.get("target_users") is not None:
                return data
            return {
                **data,
                "profile": {**profile_data, "target_users": run_users},
            }
        if isinstance(profile_data, LoadProfileConfig) and profile_data.target_users is None:
            return {
                **data,
                "profile": profile_data.model_copy(update={"target_users": run_users}),
            }
        return data

    @model_validator(mode="after")
    def validate_runtime_contract(self) -> VeriLoadConfig:
        if self.run.base_url is None and self.run.database is None:
            raise ConfigError("run.base_url or run.database is required")
        if self.run.base_url is not None:
            parsed = urlparse(self.run.base_url)
            host = parsed.hostname
            if host is None:
                raise ConfigError("run.base_url must include a hostname")
            if host not in self.safety.allowed_hosts:
                raise ConfigError(
                    f"run.base_url host {host!r} is not present in safety.allowed_hosts"
                )
        if self.safety.max_users is not None and self.run.users > self.safety.max_users:
            raise ConfigError("run.users exceeds safety.max_users")
        if self.safety.max_rps is not None and self.run.spawn_rate > self.safety.max_rps:
            raise ConfigError("run.spawn_rate exceeds safety.max_rps")
        if self.profile.target_users != self.run.users:
            raise ConfigError("profile.target_users must match run.users")
        if self.profile.duration_seconds > self.run.max_duration_seconds:
            raise ConfigError("profile.duration_seconds exceeds run.max_duration_seconds")
        if (
            self.profile.hold_seconds is not None
            and self.profile.hold_seconds > self.run.max_duration_seconds
        ):
            raise ConfigError("profile.hold_seconds exceeds run.max_duration_seconds")
        return self


def load_config(
    path: str | Path,
    *,
    cli_overrides: dict[str, Any] | None = None,
    env: Mapping[str, str] | None = None,
) -> VeriLoadConfig:
    """Load config from YAML, environment variables, and CLI overrides."""

    raw = _read_yaml(Path(path))
    merged = _deep_merge(raw, _env_overrides(os.environ if env is None else env))
    merged = _deep_merge(merged, _dotted_overrides(cli_overrides or {}))
    try:
        return VeriLoadConfig.model_validate(merged)
    except ConfigError:
        raise
    except ValueError as exc:
        raise ConfigError(str(exc)) from exc


def _read_yaml(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            loaded = yaml.safe_load(handle)
    except FileNotFoundError as exc:
        raise ConfigError(f"Config file not found: {path}") from exc
    if loaded is None:
        return {}
    if not isinstance(loaded, dict):
        raise ConfigError("Config file must contain a YAML mapping")
    return loaded


def _env_overrides(env: Mapping[str, str]) -> dict[str, Any]:
    overrides: dict[str, Any] = {}
    prefix = "VERILOAD_"
    for key, value in env.items():
        if not key.startswith(prefix):
            continue
        path = key[len(prefix) :].lower().split("__")
        _set_nested(overrides, path, _parse_scalar(value))
    return overrides


def _dotted_overrides(overrides: dict[str, Any]) -> dict[str, Any]:
    nested: dict[str, Any] = {}
    for key, value in overrides.items():
        _set_nested(nested, key.split("."), value)
    return nested


def _set_nested(target: dict[str, Any], path: list[str], value: Any) -> None:
    if not path:
        return
    head, *tail = path
    if not tail:
        target[head] = value
        return
    child = target.get(head)
    if not isinstance(child, dict):
        child = {}
    target[head] = child
    _set_nested(child, tail, value)


def _deep_merge(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    result = dict(left)
    for key, value in right.items():
        existing = result.get(key)
        if isinstance(existing, dict) and isinstance(value, dict):
            result[key] = _deep_merge(existing, value)
        else:
            result[key] = value
    return result


def _parse_scalar(value: str) -> str | int | float | bool:
    lowered = value.lower()
    if lowered in {"true", "false"}:
        return lowered == "true"
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        return value


def _run_users_from_input(value: Any) -> Any:
    if isinstance(value, Mapping):
        return value.get("users")
    if isinstance(value, RunConfig):
        return value.users
    return None
