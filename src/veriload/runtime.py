"""Runtime assembly for configured VeriLoad runs."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from veriload.config import ConfigError, LoadProfileConfig, RunDatabaseConfig, VeriLoadConfig
from veriload.data import (
    InMemoryPersonaSource,
    LocaleWeight,
    PersonaAllocator,
    PersonaPool,
    VerisimPersonaSource,
)
from veriload.distributed import DistributedRunner, WorkerRunResult
from veriload.engine import (
    DatabaseClientFactory,
    LocalRunner,
    RampProfile,
    SoakProfile,
    SpikeProfile,
    StepProfile,
)
from veriload.metrics import InMemoryMetricsSink, MetricEvent, RunSummary
from veriload.protocols import DatabaseClient
from veriload.safety import assert_persona_pool_safe
from veriload.scenarios import load_user_class


@dataclass(frozen=True)
class ExecutionResult:
    """Full execution result used by report writers and replay artifacts."""

    summary: RunSummary
    events: tuple[MetricEvent, ...]
    pool: PersonaPool
    workers: tuple[WorkerRunResult, ...] = ()


async def run_config(
    config: VeriLoadConfig,
    *,
    config_dir: Path | None = None,
    workers: int = 1,
) -> RunSummary:
    """Build and execute a local run from validated configuration."""

    return (await execute_config(config, config_dir=config_dir, workers=workers)).summary


async def execute_config(
    config: VeriLoadConfig,
    *,
    config_dir: Path | None = None,
    workers: int = 1,
) -> ExecutionResult:
    """Build and execute a run, preserving trace events and pool metadata."""

    if workers <= 0:
        raise ConfigError("workers must be greater than 0")
    if config.scenario is None:
        raise ConfigError("scenario.path and scenario.user_class are required for run")

    scenario_path = config.scenario.path
    if not scenario_path.is_absolute() and config_dir is not None:
        scenario_path = config_dir / scenario_path

    user_class = load_user_class(scenario_path, config.scenario.user_class)
    pool = build_persona_pool(config)
    assert_persona_pool_safe(
        pool,
        require_non_routable_contacts=config.safety.require_non_routable_contacts,
    )
    metrics = InMemoryMetricsSink()
    stop_file = _resolve_optional_path(config.safety.stop_file, config_dir)
    database_factory = build_database_factory(
        _resolve_database_config(config.run.database, config_dir)
    )
    if workers > 1:
        distributed_runner = DistributedRunner(
            user_classes=[user_class],
            pool=pool,
            worker_count=workers,
            profile_factory=lambda _worker_index, target_users: build_profile(
                config.profile,
                target_users=target_users,
            ),
            metrics_sink=metrics,
            run_seed=config.data.seed,
            base_url=config.run.base_url,
            database_factory=database_factory,
            stop_file=stop_file,
            spawn_rate=config.run.spawn_rate,
        )
        result = await distributed_runner.run(target_users=config.run.users)
        return ExecutionResult(
            summary=result.summary,
            events=metrics.events,
            pool=pool,
            workers=result.workers,
        )

    local_runner = LocalRunner(
        user_classes=[user_class],
        profile=build_profile(config.profile, target_users=config.run.users),
        persona_allocator=PersonaAllocator(pool, mode="unique", seed=config.data.seed),
        metrics_sink=metrics,
        run_seed=config.data.seed,
        base_url=config.run.base_url,
        database_factory=database_factory,
        stop_file=stop_file,
        spawn_rate=config.run.spawn_rate,
    )
    summary = await local_runner.run()
    return ExecutionResult(summary=summary, events=metrics.events, pool=pool)


def build_persona_pool(config: VeriLoadConfig) -> PersonaPool:
    """Generate the configured persona pool without sending traffic."""

    locales = [
        LocaleWeight(locale=item.locale, weight=item.weight)
        for item in config.data.locales
    ]
    return PersonaPool.generate(
        source=_persona_source(config.data.source),
        pool_size=config.data.pool_size,
        seed=config.data.seed,
        locales=locales,
    )


def build_profile(
    config: LoadProfileConfig,
    *,
    target_users: int | None = None,
) -> SoakProfile | RampProfile | SpikeProfile | StepProfile:
    """Create a load profile from config."""

    resolved_target_users = config.target_users if target_users is None else target_users
    if resolved_target_users is None:
        raise ConfigError("profile.target_users is required unless target_users is provided")
    if config.type == "soak":
        return SoakProfile(
            target_users=resolved_target_users,
            duration_seconds=config.duration_seconds,
            tick_seconds=config.tick_seconds,
        )
    if config.type == "ramp":
        return RampProfile(
            target_users=resolved_target_users,
            duration_seconds=config.duration_seconds,
            tick_seconds=config.tick_seconds,
        )
    if config.type == "spike":
        return SpikeProfile(
            target_users=resolved_target_users,
            hold_seconds=config.hold_seconds or config.duration_seconds,
            tick_seconds=config.tick_seconds,
        )
    raise ConfigError(f"Unsupported load profile type: {config.type}")


def build_database_factory(config: RunDatabaseConfig | None) -> DatabaseClientFactory | None:
    """Create a per-user database client factory from config."""

    if config is None:
        return None
    if config.driver == "sqlite":
        return lambda events, user: DatabaseClient.from_sqlite(
            config.dsn,
            events=events,
            segment=user.persona_segment,
            persona_id=user.persona.persona_id,
        )
    raise ConfigError(f"Unsupported database driver: {config.driver}")


def _resolve_optional_path(path: Path | None, config_dir: Path | None) -> Path | None:
    if path is None or path.is_absolute() or config_dir is None:
        return path
    return config_dir / path


def _resolve_database_config(
    config: RunDatabaseConfig | None,
    config_dir: Path | None,
) -> RunDatabaseConfig | None:
    if config is None or config.driver != "sqlite":
        return config
    if config.dsn == ":memory:" or config.dsn.startswith("file:"):
        return config
    database_path = Path(config.dsn)
    if database_path.is_absolute() or config_dir is None:
        return config
    return config.model_copy(update={"dsn": str(config_dir / database_path)})


def _persona_source(source_name: str):
    if source_name == "verisim":
        return VerisimPersonaSource()
    if source_name == "memory":
        return InMemoryPersonaSource()
    raise ConfigError(f"Unsupported data.source: {source_name}")
