import asyncio
from pathlib import Path

import pytest

from veriload.config import VeriLoadConfig
from veriload.distributed_network import NetworkDistributedController, NetworkDistributedWorker


def _write_networked_config(tmp_path: Path) -> tuple[Path, VeriLoadConfig]:
    scenario_path = tmp_path / "scenario.py"
    scenario_path.write_text(
        """
from veriload import VeriUser, task
from veriload.metrics import RequestFinished

class CountUser(VeriUser):
    @task(weight=1)
    async def count(self):
        await self.cluster.send("persona-started", {"persona_id": self.persona.persona_id})
        self.events.emit(RequestFinished(
            name="count",
            method="INTERNAL",
            status_code=200,
            latency_ms=1,
            segment=self.persona_segment,
            persona_id=self.persona.persona_id,
        ))
        self.stop()
""",
        encoding="utf-8",
    )
    config_path = tmp_path / "veriload.yaml"
    config_path.write_text(
        """
scenario:
  path: scenario.py
  user_class: CountUser
run:
  base_url: "https://api.example.test"
  users: 4
  spawn_rate: 4
  max_duration_seconds: 1
data:
  pool_size: 4
  seed: 42
  source: memory
profile:
  type: soak
  target_users: 4
  duration_seconds: 1
  tick_seconds: 0
safety:
  allowed_hosts:
    - api.example.test
""",
        encoding="utf-8",
    )
    return config_path, VeriLoadConfig.model_validate(
        {
            "scenario": {"path": scenario_path, "user_class": "CountUser"},
            "run": {
                "base_url": "https://api.example.test",
                "users": 4,
                "spawn_rate": 4,
                "max_duration_seconds": 1,
            },
            "data": {"pool_size": 4, "seed": 42, "source": "memory"},
            "profile": {"type": "soak", "target_users": 4, "duration_seconds": 1, "tick_seconds": 0},
            "safety": {"allowed_hosts": ["api.example.test"]},
        }
    )


@pytest.mark.asyncio
async def test_networked_controller_runs_remote_workers(tmp_path: Path) -> None:
    config_path, config = _write_networked_config(tmp_path)
    controller = NetworkDistributedController(
        config=config,
        config_path=config_path,
        bind_host="127.0.0.1",
        bind_port=0,
        expect_workers=2,
        cluster_token="secret",
        ready_timeout_seconds=2,
    )

    controller_task = asyncio.create_task(controller.run())
    await controller.wait_until_serving()
    worker_tasks = [
        asyncio.create_task(
            NetworkDistributedWorker(
                controller_host="127.0.0.1",
                controller_port=controller.port,
                work_dir=tmp_path / f"worker-{index}",
                cluster_token="secret",
                node_id=f"worker-{index}",
            ).run()
        )
        for index in range(2)
    ]

    result = await asyncio.wait_for(controller_task, timeout=5)
    await asyncio.gather(*worker_tasks)

    assert result.summary.total_requests == 4
    assert result.worker_count == 2
    assert [worker.target_users for worker in result.workers] == [2, 2]
    assert sorted(persona_id for worker in result.workers for persona_id in worker.persona_ids) == [
        "p0",
        "p1",
        "p2",
        "p3",
    ]
    assert sorted(message.payload["persona_id"] for message in controller.custom_messages) == [
        "p0",
        "p1",
        "p2",
        "p3",
    ]


@pytest.mark.asyncio
async def test_networked_controller_rejects_bad_worker_token(tmp_path: Path) -> None:
    config_path, config = _write_networked_config(tmp_path)
    controller = NetworkDistributedController(
        config=config,
        config_path=config_path,
        bind_host="127.0.0.1",
        bind_port=0,
        expect_workers=1,
        cluster_token="secret",
        ready_timeout_seconds=0.25,
    )

    controller_task = asyncio.create_task(controller.run())
    await controller.wait_until_serving()

    with pytest.raises(Exception, match="signature|closed"):
        await NetworkDistributedWorker(
            controller_host="127.0.0.1",
            controller_port=controller.port,
            work_dir=tmp_path / "worker",
            cluster_token="wrong",
            node_id="worker-bad",
        ).run()

    with pytest.raises(TimeoutError):
        await controller_task
