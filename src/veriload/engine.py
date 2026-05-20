"""Standalone local async load engine."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from veriload.cleanup import AutoCleanupManager, CleanupConfigSnapshot, CleanupSummary, merge_cleanup_summaries
from veriload.data import PersonaAllocator
from veriload.metrics import EventBus, InMemoryMetricsSink, RunSummary
from veriload.protocols import HttpClient
from veriload.safety import stop_requested
from veriload.users import VeriUser

DatabaseClientFactory = Callable[[EventBus, VeriUser], Any]


@dataclass(frozen=True)
class LoadTarget:
    """Desired load state at an elapsed time."""

    elapsed_seconds: float
    active_users: int | None
    arrival_rate: float | None = None


class SoakProfile:
    """Hold a constant closed-model user count."""

    def __init__(
        self, *, target_users: int, duration_seconds: float, tick_seconds: float = 1
    ) -> None:
        self.target_users = target_users
        self.duration_seconds = duration_seconds
        self.tick_seconds = tick_seconds

    async def ticks(self) -> AsyncIterator[LoadTarget]:
        elapsed = 0.0
        while elapsed < self.duration_seconds:
            yield LoadTarget(elapsed_seconds=_nice_number(elapsed), active_users=self.target_users)
            elapsed += self.tick_seconds or self.duration_seconds
            if self.tick_seconds == 0:
                break
        yield LoadTarget(elapsed_seconds=_nice_number(self.duration_seconds), active_users=0)


class RampProfile:
    """Linearly ramp from zero to a target user count."""

    def __init__(
        self, *, target_users: int, duration_seconds: float, tick_seconds: float = 1
    ) -> None:
        self.target_users = target_users
        self.duration_seconds = duration_seconds
        self.tick_seconds = tick_seconds

    async def ticks(self) -> AsyncIterator[LoadTarget]:
        elapsed = 0.0
        while elapsed <= self.duration_seconds:
            fraction = elapsed / self.duration_seconds if self.duration_seconds else 1
            yield LoadTarget(
                elapsed_seconds=_nice_number(elapsed),
                active_users=int(self.target_users * fraction),
            )
            elapsed += self.tick_seconds or self.duration_seconds
            if self.tick_seconds == 0:
                break
        yield LoadTarget(elapsed_seconds=_nice_number(self.duration_seconds), active_users=0)


class SpikeProfile:
    """Jump to target load immediately, then drop after the hold."""

    def __init__(self, *, target_users: int, hold_seconds: float, tick_seconds: float = 1) -> None:
        self.target_users = target_users
        self.hold_seconds = hold_seconds
        self.tick_seconds = tick_seconds

    async def ticks(self) -> AsyncIterator[LoadTarget]:
        elapsed = 0.0
        while elapsed < self.hold_seconds:
            yield LoadTarget(elapsed_seconds=_nice_number(elapsed), active_users=self.target_users)
            elapsed += self.tick_seconds or self.hold_seconds
            if self.tick_seconds == 0:
                break
        yield LoadTarget(elapsed_seconds=_nice_number(self.hold_seconds), active_users=0)


class StepProfile:
    """Hold a sequence of closed-model user counts."""

    def __init__(self, *, steps: list[tuple[int, float]], tick_seconds: float = 1) -> None:
        self.steps = tuple(steps)
        self.tick_seconds = tick_seconds

    async def ticks(self) -> AsyncIterator[LoadTarget]:
        elapsed = 0.0
        for users, hold_seconds in self.steps:
            step_elapsed = 0.0
            while step_elapsed < hold_seconds:
                yield LoadTarget(elapsed_seconds=_nice_number(elapsed), active_users=users)
                step_elapsed += self.tick_seconds or hold_seconds
                elapsed += self.tick_seconds or hold_seconds
                if self.tick_seconds == 0:
                    break
        yield LoadTarget(elapsed_seconds=_nice_number(elapsed), active_users=0)


class LocalRunner:
    """Runs VeriLoad users in the local process."""

    def __init__(
        self,
        *,
        user_classes: list[type[VeriUser]],
        profile: SoakProfile | RampProfile | SpikeProfile | StepProfile,
        persona_allocator: PersonaAllocator,
        metrics_sink: InMemoryMetricsSink,
        run_seed: int,
        base_url: str | None = None,
        database_factory: DatabaseClientFactory | None = None,
        cleanup_config: CleanupConfigSnapshot | None = None,
        stop_file: Path | None = None,
        spawn_rate: float | None = None,
        cluster: Any | None = None,
    ) -> None:
        if spawn_rate is not None and spawn_rate < 0:
            raise ValueError("spawn_rate must be non-negative")
        self.user_classes = tuple(user_classes)
        self.profile = profile
        self.persona_allocator = persona_allocator
        self.metrics_sink = metrics_sink
        self.run_seed = run_seed
        self.base_url = base_url
        self.database_factory = database_factory
        self.cleanup_config = cleanup_config or CleanupConfigSnapshot.disabled()
        self.stop_file = stop_file
        self.spawn_rate = spawn_rate
        self.cluster = cluster
        self._cleanup_summaries: tuple[CleanupSummary, ...] = ()

    async def run(self) -> RunSummary:
        """Execute a local closed-model run and return the metrics summary."""

        tasks: tuple[asyncio.Task[None], ...] = ()
        user_index = 0
        spawn_budget = 0.0
        async for target in self.profile.ticks():
            if stop_requested(self.stop_file):
                break
            if self._spawn_rate_limited():
                spawn_budget = min(
                    spawn_budget + (self.spawn_rate or 0) * self.profile.tick_seconds,
                    max(self.spawn_rate or 0, 1),
                )
            desired_users = target.active_users or 0
            active_tasks = tuple(task for task in tasks if not task.done())
            deficit = max(desired_users - len(active_tasks), 0)
            if self._spawn_rate_limited():
                start_count = min(deficit, int(spawn_budget))
                spawn_budget -= start_count
            else:
                start_count = deficit
            new_tasks = tuple(
                asyncio.create_task(self._run_user(user_index + offset))
                for offset in range(start_count)
            )
            user_index += start_count
            tasks = (*active_tasks, *new_tasks)
            if self.profile.tick_seconds:
                await asyncio.sleep(self.profile.tick_seconds)
        if tasks:
            await asyncio.gather(*tasks)
        return self.metrics_sink.summary()

    @property
    def cleanup_summary(self) -> CleanupSummary:
        """Return aggregate cleanup results for this runner."""

        return merge_cleanup_summaries(self._cleanup_summaries)

    async def _run_user(self, user_index: int) -> None:
        persona = self.persona_allocator.acquire(user_index)
        user_class = self.user_classes[user_index % len(self.user_classes)]
        events = EventBus((self.metrics_sink,))
        cleanup_manager = AutoCleanupManager(self.cleanup_config)
        user = user_class(
            persona=persona,
            user_index=user_index,
            run_seed=self.run_seed,
            events=events,
            cluster=self.cluster,
            cleanup_manager=cleanup_manager,
        )
        if self.base_url is not None:
            user.http = HttpClient(
                base_url=self.base_url,
                events=events,
                segment=user.persona_segment,
                persona_id=user.persona.persona_id,
                cleanup_manager=cleanup_manager,
            )
        if self.database_factory is not None:
            user.db = self.database_factory(events, user)
        try:
            await user.on_start()
            while not user.stopped and not stop_requested(self.stop_file):
                await user.run_once()
        finally:
            try:
                await user.on_stop()
            finally:
                try:
                    cleanup_summary = await cleanup_manager.cleanup()
                    self._cleanup_summaries = (*self._cleanup_summaries, cleanup_summary)
                finally:
                    await _aclose_if_present(user.http)
                    await _aclose_if_present(user.db)

    def _spawn_rate_limited(self) -> bool:
        return self.spawn_rate is not None and self.profile.tick_seconds > 0


def _nice_number(value: float) -> float | int:
    return int(value) if value.is_integer() else value


async def _aclose_if_present(client: Any | None) -> None:
    if client is None or not hasattr(client, "aclose"):
        return
    await client.aclose()
