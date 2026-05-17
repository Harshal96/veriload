import pytest

from veriload.data import PersonaAllocator, PersonaPool
from veriload.engine import LocalRunner, SoakProfile
from veriload.metrics import InMemoryMetricsSink, RequestFinished
from veriload.users import VeriUser, task


class CountingUser(VeriUser):
    @task(weight=1)
    async def count(self) -> None:
        self.state["count"] = self.state.get("count", 0) + 1
        self.events.emit(
            RequestFinished(
                name="count",
                method="INTERNAL",
                status_code=200,
                latency_ms=1.5,
                segment=self.persona_segment,
            )
        )
        self.stop()


@pytest.mark.asyncio
async def test_local_runner_executes_users_and_collects_metrics(sample_persona) -> None:
    sink = InMemoryMetricsSink()
    runner = LocalRunner(
        user_classes=[CountingUser],
        profile=SoakProfile(target_users=2, duration_seconds=1, tick_seconds=0),
        persona_allocator=PersonaAllocator(
            PersonaPool([sample_persona, sample_persona.copy_with_id("p2")]),
            mode="unique",
        ),
        metrics_sink=sink,
        run_seed=10,
    )

    summary = await runner.run()

    assert summary.total_requests == 2
    assert summary.total_failures == 0
    assert summary.segments["en_US:Computing"].total_requests == 2


@pytest.mark.asyncio
async def test_local_runner_limits_user_starts_by_spawn_rate(sample_persona) -> None:
    sink = InMemoryMetricsSink()
    runner = LocalRunner(
        user_classes=[CountingUser],
        profile=SoakProfile(target_users=5, duration_seconds=1, tick_seconds=1),
        persona_allocator=PersonaAllocator(
            PersonaPool(
                [
                    sample_persona.copy_with_id(f"p{index}")
                    for index in range(5)
                ]
            ),
            mode="unique",
        ),
        metrics_sink=sink,
        run_seed=10,
        spawn_rate=2,
    )

    summary = await runner.run()

    assert summary.total_requests == 2


@pytest.mark.asyncio
async def test_local_runner_honors_existing_stop_file(tmp_path, sample_persona) -> None:
    stop_file = tmp_path / "stop"
    stop_file.write_text("stop", encoding="utf-8")
    sink = InMemoryMetricsSink()
    runner = LocalRunner(
        user_classes=[CountingUser],
        profile=SoakProfile(target_users=2, duration_seconds=1, tick_seconds=0),
        persona_allocator=PersonaAllocator(
            PersonaPool([sample_persona, sample_persona.copy_with_id("p2")]),
            mode="unique",
        ),
        metrics_sink=sink,
        run_seed=10,
        stop_file=stop_file,
    )

    summary = await runner.run()

    assert summary.total_requests == 0
