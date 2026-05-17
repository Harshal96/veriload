import json
from pathlib import Path

from veriload.config import VeriLoadConfig
from veriload.data import InMemoryPersonaSource, PersonaPool
from veriload.metrics import RequestFailed, RequestFinished
from veriload.replay import build_replay_manifest, load_replay_plan


def test_build_replay_manifest_contains_regeneration_inputs_and_trace(sample_persona) -> None:
    config = _config()
    pool = PersonaPool([sample_persona])
    events = (
        RequestFinished(
            name="GET /ok",
            method="GET",
            status_code=200,
            latency_ms=10,
            segment="en_US:Computing",
            persona_id="p1",
        ),
        RequestFailed(
            name="POST /fail",
            method="POST",
            error="HTTP 500",
            latency_ms=20,
            segment="en_US:Computing",
            persona_id="p1",
        ),
    )

    manifest = build_replay_manifest(config, pool, events, workers=1)

    assert manifest["seed"] == 42
    assert manifest["pool_size"] == 1
    assert manifest["failed_personas"] == ["p1"]
    assert manifest["events"][1]["error"] == "HTTP 500"


def test_load_replay_plan_rehydrates_persona_and_filters_trace(tmp_path: Path) -> None:
    pool = PersonaPool.generate(source=InMemoryPersonaSource(), pool_size=2, seed=42)
    manifest = build_replay_manifest(
        _config(pool_size=2),
        pool,
        (
            RequestFinished(
                name="GET /p0",
                method="GET",
                status_code=200,
                latency_ms=1,
                segment="en_US:Retail",
                persona_id="p0",
            ),
            RequestFailed(
                name="GET /p1",
                method="GET",
                error="HTTP 500",
                latency_ms=2,
                segment="en_US:Retail",
                persona_id="p1",
            ),
        ),
        workers=1,
    )
    path = tmp_path / "replay.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")

    plan = load_replay_plan(path, persona_id="p1")

    assert plan.persona.persona_id == "p1"
    assert plan.persona.contact.email.endswith("example.invalid")
    assert [event["name"] for event in plan.events] == ["GET /p1"]


def _config(pool_size: int = 1) -> VeriLoadConfig:
    return VeriLoadConfig(
        scenario={"path": "scenario.py", "user_class": "SmokeUser"},
        run={
            "base_url": "https://api.example.test",
            "users": 1,
            "spawn_rate": 1,
            "max_duration_seconds": 1,
        },
        data={"pool_size": pool_size, "seed": 42, "source": "memory"},
        profile={"type": "soak", "target_users": 1, "duration_seconds": 1},
        safety={"allowed_hosts": ["api.example.test"]},
    )
