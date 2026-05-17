from pathlib import Path

import pytest

from veriload.config import VeriLoadConfig
from veriload.data import InMemoryPersonaSource, PersonaPool
from veriload.distributed import DistributedRunner, plan_worker_partitions
from veriload.engine import SoakProfile
from veriload.metrics import InMemoryMetricsSink, RequestFinished
from veriload.runtime import run_config
from veriload.users import VeriUser, task


class DistributedCountingUser(VeriUser):
    @task(weight=1)
    async def count(self) -> None:
        self.events.emit(
            RequestFinished(
                name="count",
                method="INTERNAL",
                status_code=200,
                latency_ms=1,
                segment=self.persona_segment,
            )
        )
        self.stop()


def test_plan_worker_partitions_are_deterministic_and_non_overlapping() -> None:
    pool = PersonaPool.generate(source=InMemoryPersonaSource(), pool_size=7, seed=42)

    first = plan_worker_partitions(pool, worker_count=3)
    second = plan_worker_partitions(pool, worker_count=3)

    assert first == second
    all_ids = [persona_id for partition in first for persona_id in partition.persona_ids]
    assert sorted(all_ids) == sorted(persona.persona_id for persona in pool.personas)
    assert len(all_ids) == len(set(all_ids))


@pytest.mark.asyncio
async def test_distributed_runner_executes_workers_with_partitioned_personas() -> None:
    pool = PersonaPool.generate(source=InMemoryPersonaSource(), pool_size=4, seed=42)
    sink = InMemoryMetricsSink()
    runner = DistributedRunner(
        user_classes=[DistributedCountingUser],
        pool=pool,
        worker_count=2,
        profile_factory=lambda _worker_index, target_users: SoakProfile(
            target_users=target_users,
            duration_seconds=1,
            tick_seconds=0,
        ),
        metrics_sink=sink,
        run_seed=42,
    )

    result = await runner.run(target_users=4)

    assert result.summary.total_requests == 4
    assert [len(worker.persona_ids) for worker in result.workers] == [2, 2]


@pytest.mark.asyncio
async def test_distributed_runner_reports_worker_load_and_summaries() -> None:
    pool = PersonaPool.generate(source=InMemoryPersonaSource(), pool_size=5, seed=42)
    sink = InMemoryMetricsSink()
    runner = DistributedRunner(
        user_classes=[DistributedCountingUser],
        pool=pool,
        worker_count=2,
        profile_factory=lambda _worker_index, target_users: SoakProfile(
            target_users=target_users,
            duration_seconds=1,
            tick_seconds=0,
        ),
        metrics_sink=sink,
        run_seed=42,
    )

    result = await runner.run(target_users=5)

    assert result.worker_count == 2
    assert result.total_personas == 5
    assert [(worker.worker_index, worker.target_users) for worker in result.workers] == [(0, 3), (1, 2)]
    assert [worker.summary.total_requests for worker in result.workers] == [3, 2]
    assert result.summary.total_requests == 5


@pytest.mark.asyncio
async def test_run_config_supports_multiple_workers(tmp_path: Path) -> None:
    scenario_path = tmp_path / "scenario.py"
    scenario_path.write_text(
        """
from veriload import VeriUser, task
from veriload.metrics import RequestFinished

class CountUser(VeriUser):
    @task(weight=1)
    async def count(self):
        self.events.emit(RequestFinished(
            name="count",
            method="INTERNAL",
            status_code=200,
            latency_ms=1,
            segment=self.persona_segment,
        ))
        self.stop()
""",
        encoding="utf-8",
    )
    config = VeriLoadConfig(
        scenario={"path": scenario_path, "user_class": "CountUser"},
        run={
            "base_url": "https://api.example.test",
            "users": 4,
            "spawn_rate": 4,
            "max_duration_seconds": 1,
        },
        data={"pool_size": 4, "seed": 42, "source": "memory"},
        profile={
            "type": "soak",
            "duration_seconds": 1,
            "tick_seconds": 0,
        },
        safety={"allowed_hosts": ["api.example.test"]},
    )

    summary = await run_config(config, config_dir=tmp_path, workers=2)

    assert summary.total_requests == 4
