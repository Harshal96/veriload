"""Replay and debug artifact support."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from veriload.config import VeriLoadConfig
from veriload.data import InMemoryPersonaSource, LocaleWeight, PersonaPool, PersonaRecord
from veriload.dataset_studio import persona_to_dict
from veriload.metrics import MetricEvent, RequestFailed


@dataclass(frozen=True)
class ReplayPlan:
    """Dry-run replay/debug plan for one persona."""

    persona: PersonaRecord
    base_url: str
    scenario: dict[str, str]
    events: tuple[dict[str, Any], ...]


def build_replay_manifest(
    config: VeriLoadConfig,
    pool: PersonaPool,
    events: tuple[MetricEvent, ...],
    *,
    workers: int,
) -> dict[str, Any]:
    """Build a replay manifest from the deterministic run inputs and trace."""

    event_rows = [asdict(event) for event in events]
    failed_personas = sorted(
        {
            event.persona_id
            for event in events
            if isinstance(event, RequestFailed) and event.persona_id is not None
        }
    )
    return {
        "seed": config.data.seed,
        "pool_size": config.data.pool_size,
        "locales": [
            {"locale": item.locale, "weight": item.weight}
            for item in config.data.locales
        ],
        "base_url": config.run.base_url or "",
        "scenario": {
            "path": str(config.scenario.path) if config.scenario else "",
            "user_class": config.scenario.user_class if config.scenario else "",
        },
        "workers": workers,
        "personas": [persona_to_dict(persona) for persona in pool.personas],
        "events": event_rows,
        "failed_personas": failed_personas,
    }


def write_replay_manifest(path: str | Path, manifest: dict[str, Any]) -> None:
    """Write a replay manifest as JSON."""

    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")


def load_replay_plan(path: str | Path, *, persona_id: str) -> ReplayPlan:
    """Load a replay plan for one persona from a replay manifest."""

    manifest = json.loads(Path(path).read_text(encoding="utf-8"))
    persona = _load_persona(manifest, persona_id)
    events = tuple(
        event for event in manifest.get("events", ()) if event.get("persona_id") == persona_id
    )
    return ReplayPlan(
        persona=persona,
        base_url=manifest.get("base_url", ""),
        scenario=manifest.get("scenario", {}),
        events=events,
    )


def _load_persona(manifest: dict[str, Any], persona_id: str) -> PersonaRecord:
    for persona_data in manifest.get("personas", ()):
        if persona_data.get("persona_id") == persona_id:
            return _persona_from_dict(persona_data)

    locales = [
        LocaleWeight(locale=item["locale"], weight=item["weight"])
        for item in manifest.get("locales", [{"locale": "en_US", "weight": 1.0}])
    ]
    pool = PersonaPool.generate(
        source=InMemoryPersonaSource(),
        pool_size=manifest["pool_size"],
        seed=manifest["seed"],
        locales=locales,
    )
    for persona in pool.personas:
        if persona.persona_id == persona_id:
            return persona
    raise ValueError(f"Persona {persona_id!r} not found in replay manifest")


def _persona_from_dict(data: dict[str, Any]) -> PersonaRecord:
    from veriload.data import Company, Contact, Job, Person

    return PersonaRecord(
        persona_id=data["persona_id"],
        locale=data["locale"],
        person=Person(**data["person"]),
        contact=Contact(**data["contact"]),
        job=Job(**data["job"]),
        company=Company(**data["company"]),
    )
