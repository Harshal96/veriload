"""In-process distributed execution primitives.

This module is the controller/worker foundation. It deliberately avoids a
network control plane for now, but keeps the same ownership boundaries:
controller plans partitions, workers execute isolated persona shards, and
metrics aggregate at the controller.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from veriload.data import PersonaAllocator, PersonaPool
from veriload.engine import (
    DatabaseClientFactory,
    LocalRunner,
    RampProfile,
    SoakProfile,
    SpikeProfile,
    StepProfile,
)
from veriload.metrics import InMemoryMetricsSink, RunSummary
from veriload.users import VeriUser

Profile = SoakProfile | RampProfile | SpikeProfile | StepProfile
ProfileFactory = Callable[[int, int], Profile]


@dataclass(frozen=True)
class WorkerPartition:
    """Personas assigned to one worker."""

    worker_index: int
    worker_count: int
    persona_ids: tuple[str, ...]


@dataclass(frozen=True)
class WorkerRunResult:
    """Execution result metadata for one worker."""

    worker_index: int
    target_users: int
    persona_ids: tuple[str, ...]
    summary: RunSummary

    @property
    def persona_count(self) -> int:
        return len(self.persona_ids)


@dataclass(frozen=True)
class DistributedRunResult:
    """Aggregated distributed run result."""

    summary: RunSummary
    workers: tuple[WorkerRunResult, ...]

    @property
    def worker_count(self) -> int:
        return len(self.workers)

    @property
    def total_personas(self) -> int:
        return sum(worker.persona_count for worker in self.workers)


def plan_worker_partitions(pool: PersonaPool, *, worker_count: int) -> tuple[WorkerPartition, ...]:
    """Plan deterministic non-overlapping persona partitions."""

    if worker_count <= 0:
        raise ValueError("worker_count must be greater than 0")
    return tuple(
        WorkerPartition(
            worker_index=index,
            worker_count=worker_count,
            persona_ids=tuple(persona.persona_id for persona in pool.partition(
                worker_index=index,
                worker_count=worker_count,
            ).personas),
        )
        for index in range(worker_count)
    )


class DistributedRunner:
    """Runs multiple local workers concurrently with isolated persona shards."""

    def __init__(
        self,
        *,
        user_classes: list[type[VeriUser]],
        pool: PersonaPool,
        worker_count: int,
        profile_factory: ProfileFactory,
        metrics_sink: InMemoryMetricsSink,
        run_seed: int,
        base_url: str | None = None,
        database_factory: DatabaseClientFactory | None = None,
        stop_file: Path | None = None,
        spawn_rate: float | None = None,
    ) -> None:
        if worker_count <= 0:
            raise ValueError("worker_count must be greater than 0")
        if spawn_rate is not None and spawn_rate < 0:
            raise ValueError("spawn_rate must be non-negative")
        self.user_classes = tuple(user_classes)
        self.pool = pool
        self.worker_count = worker_count
        self.profile_factory = profile_factory
        self.metrics_sink = metrics_sink
        self.run_seed = run_seed
        self.base_url = base_url
        self.database_factory = database_factory
        self.stop_file = stop_file
        self.spawn_rate = spawn_rate

    async def run(self, *, target_users: int) -> DistributedRunResult:
        """Execute all workers and return aggregate metrics."""

        partitions = plan_worker_partitions(self.pool, worker_count=self.worker_count)
        worker_results = tuple(
            await asyncio.gather(
                *(
                    self._run_worker(
                        partition,
                        _target_users_for_worker(
                            target_users,
                            self.worker_count,
                            partition.worker_index,
                        ),
                        target_users,
                    )
                    for partition in partitions
                )
            )
        )
        return DistributedRunResult(
            summary=self.metrics_sink.summary(),
            workers=worker_results,
        )

    async def _run_worker(
        self,
        partition: WorkerPartition,
        target_users: int,
        total_target_users: int,
    ) -> WorkerRunResult:
        worker_pool = self.pool.partition(
            worker_index=partition.worker_index,
            worker_count=self.worker_count,
        )
        worker_sink = InMemoryMetricsSink()
        runner = LocalRunner(
            user_classes=list(self.user_classes),
            profile=self.profile_factory(partition.worker_index, target_users),
            persona_allocator=PersonaAllocator(
                worker_pool,
                mode="unique",
                seed=self.run_seed + partition.worker_index,
            ),
            metrics_sink=worker_sink,
            run_seed=self.run_seed + partition.worker_index,
            base_url=self.base_url,
            database_factory=self.database_factory,
            stop_file=self.stop_file,
            spawn_rate=_spawn_rate_for_worker(
                self.spawn_rate,
                total_target_users=total_target_users,
                target_users=target_users,
            ),
        )
        summary = await runner.run()
        for event in worker_sink.events:
            self.metrics_sink.record(event)
        return WorkerRunResult(
            worker_index=partition.worker_index,
            target_users=target_users,
            persona_ids=partition.persona_ids,
            summary=summary,
        )


def _target_users_for_worker(target_users: int, worker_count: int, worker_index: int) -> int:
    base = target_users // worker_count
    remainder = target_users % worker_count
    return base + (1 if worker_index < remainder else 0)


def _spawn_rate_for_worker(
    spawn_rate: float | None,
    *,
    total_target_users: int,
    target_users: int,
) -> float | None:
    if spawn_rate is None:
        return None
    if total_target_users <= 0 or target_users <= 0:
        return 0
    return spawn_rate * (target_users / total_target_users)
