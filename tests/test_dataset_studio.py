import json
from pathlib import Path

from veriload.data import InMemoryPersonaSource, PersonaPool
from veriload.dataset_studio import (
    explain_persona_field,
    export_personas_jsonl,
    persona_to_dict,
    validate_dataset,
)


def test_persona_to_dict_exposes_nested_persona_fields(sample_persona) -> None:
    data = persona_to_dict(sample_persona)

    assert data["persona_id"] == "p1"
    assert data["person"]["username"] == "ada"
    assert data["contact"]["email"] == "ada@example.invalid"
    assert data["company"]["id"] == "company-1"


def test_validate_dataset_reports_uniqueness_and_segments() -> None:
    pool = PersonaPool.generate(source=InMemoryPersonaSource(), pool_size=4, seed=42)

    result = validate_dataset(pool)

    assert result.valid
    assert result.total_personas == 4
    assert result.unique_emails == 4
    assert sum(result.locales.values()) == 4
    assert sum(result.industries.values()) == 4


def test_export_personas_jsonl_writes_one_persona_per_line(tmp_path: Path) -> None:
    pool = PersonaPool.generate(source=InMemoryPersonaSource(), pool_size=2, seed=42)
    path = tmp_path / "personas.jsonl"

    export_personas_jsonl(path, pool)

    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert [row["persona_id"] for row in rows] == ["p0", "p1"]
    assert rows[0]["contact"]["email"].endswith("example.invalid")


def test_explain_persona_field_resolves_value_and_dependencies(sample_persona) -> None:
    explanation = explain_persona_field(sample_persona, "contact.email")

    assert explanation.field_path == "contact.email"
    assert explanation.value == "ada@example.invalid"
    assert "person.username" in explanation.dependencies
