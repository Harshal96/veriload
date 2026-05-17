"""Dataset Studio helpers for inspecting generated persona pools."""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from veriload.data import PersonaPool, PersonaRecord


@dataclass(frozen=True)
class DatasetValidation:
    """Validation summary for a persona pool."""

    valid: bool
    total_personas: int
    unique_emails: int
    duplicate_emails: tuple[str, ...]
    locales: dict[str, int]
    industries: dict[str, int]


@dataclass(frozen=True)
class FieldExplanation:
    """Human-readable explanation of one persona field."""

    field_path: str
    value: Any
    dependencies: tuple[str, ...]
    note: str


def persona_to_dict(persona: PersonaRecord) -> dict[str, Any]:
    """Convert a persona dataclass tree to plain JSON-compatible data."""

    return asdict(persona)


def validate_dataset(pool: PersonaPool) -> DatasetValidation:
    """Validate uniqueness and summarize segment distribution."""

    email_counts = Counter(persona.contact.email for persona in pool.personas)
    duplicate_emails = tuple(sorted(email for email, count in email_counts.items() if count > 1))
    locales = Counter(persona.locale for persona in pool.personas)
    industries = Counter(persona.job.industry for persona in pool.personas)
    return DatasetValidation(
        valid=not duplicate_emails,
        total_personas=len(pool.personas),
        unique_emails=len(email_counts),
        duplicate_emails=duplicate_emails,
        locales=dict(sorted(locales.items())),
        industries=dict(sorted(industries.items())),
    )


def export_personas_jsonl(path: str | Path, pool: PersonaPool) -> None:
    """Export personas as newline-delimited JSON."""

    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    rows = [json.dumps(persona_to_dict(persona), sort_keys=True) for persona in pool.personas]
    output_path.write_text("\n".join(rows) + ("\n" if rows else ""), encoding="utf-8")


def explain_persona_field(persona: PersonaRecord, field_path: str) -> FieldExplanation:
    """Explain a field value and its known synthetic-data dependencies."""

    value = _resolve_field(persona, field_path)
    dependencies = _KNOWN_DEPENDENCIES.get(field_path, ())
    note = _KNOWN_NOTES.get(
        field_path,
        "Resolved directly from the generated persona record.",
    )
    return FieldExplanation(
        field_path=field_path,
        value=value,
        dependencies=dependencies,
        note=note,
    )


_KNOWN_DEPENDENCIES: dict[str, tuple[str, ...]] = {
    "person.username": ("person.name", "locale"),
    "contact.email": ("person.username", "locale"),
    "contact.phone_e164": ("locale",),
    "job.industry": ("locale", "seed"),
    "company.id": ("job.industry",),
    "company.industry": ("job.industry",),
}

_KNOWN_NOTES: dict[str, str] = {
    "contact.email": "Email is derived from the generated username and forced onto example.invalid.",
    "company.id": "Company IDs are stable within the deterministic generated pool.",
}


def _resolve_field(persona: PersonaRecord, field_path: str) -> Any:
    current: Any = persona
    for part in field_path.split("."):
        try:
            current = getattr(current, part)
        except AttributeError as exc:
            raise AttributeError(field_path) from exc
    return current
