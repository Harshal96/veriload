"""Model-backed fixture lifecycle primitives."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from veriload.data import PersonaRecord


class FixtureError(RuntimeError):
    """Raised when a model fixture cannot be created or cleaned up."""


@dataclass(frozen=True)
class FixtureContext:
    """Per-user fixture generation context."""

    persona: PersonaRecord
    run_id: str
    worker_index: int
    overrides: Mapping[str, Any]

    def values_for(
        self,
        field_names: Iterable[str],
        *,
        overrides: Mapping[str, Any] | None = None,
        type_hints: Mapping[str, type] | None = None,
    ) -> dict[str, Any]:
        """Generate model/API values from the current persona."""

        explicit_overrides = dict(overrides or self.overrides)
        hints = dict(type_hints or {})
        return {
            field_name: explicit_overrides[field_name]
            if field_name in explicit_overrides
            else value_for_field(
                field_name,
                persona=self.persona,
                run_id=self.run_id,
                worker_index=self.worker_index,
                field_type=hints.get(field_name),
            )
            for field_name in field_names
        }


@dataclass(frozen=True)
class FixtureRecord:
    """One created fixture entry tracked for later cleanup."""

    connector: ModelConnector
    target: object
    identity: str
    obj: Any
    metadata: Mapping[str, Any] | None = None


class ModelConnector(Protocol):
    """Connector interface implemented by model fixture backends."""

    def supports(self, model: object) -> bool:
        """Return whether this connector can create the supplied model/resource."""

    async def create(self, model: object, context: FixtureContext) -> FixtureRecord:
        """Create one fixture entry and return its cleanup record."""

    async def delete(self, record: FixtureRecord, context: FixtureContext) -> None:
        """Delete one created fixture entry."""


class ModelFixtures:
    """Simple facade for creating persona-backed model fixtures."""

    def __init__(
        self,
        *,
        persona: PersonaRecord,
        run_id: str,
        worker_index: int = 0,
        connectors: Iterable[ModelConnector] = (),
    ) -> None:
        self.persona = persona
        self.run_id = run_id
        self.worker_index = worker_index
        self._connectors = tuple(connectors)
        self._records: tuple[FixtureRecord, ...] = ()

    @property
    def records(self) -> tuple[FixtureRecord, ...]:
        """Return the currently tracked cleanup records."""

        return self._records

    def with_connectors(self, *connectors: ModelConnector) -> ModelFixtures:
        """Return a copy with additional connectors configured."""

        updated = ModelFixtures(
            persona=self.persona,
            run_id=self.run_id,
            worker_index=self.worker_index,
            connectors=(*self._connectors, *connectors),
        )
        updated._records = self._records
        return updated

    async def create(
        self,
        model: object,
        *,
        overrides: Mapping[str, Any] | None = None,
    ) -> Any:
        """Create a fixture for a model/resource and remember it for cleanup."""

        connector = self._connector_for(model)
        context = self._context(overrides or {})
        record = await connector.create(model, context)
        self._records = (*self._records, record)
        return record.obj

    async def cleanup(self) -> None:
        """Delete all tracked fixtures in reverse creation order."""

        records = self._records
        if not records:
            return
        self._records = ()
        context = self._context({})
        for record in reversed(records):
            await record.connector.delete(record, context)

    def _connector_for(self, model: object) -> ModelConnector:
        for connector in self._connectors:
            if connector.supports(model):
                return connector
        raise FixtureError(f"No fixture connector supports {model!r}")

    def _context(self, overrides: Mapping[str, Any]) -> FixtureContext:
        return FixtureContext(
            persona=self.persona,
            run_id=self.run_id,
            worker_index=self.worker_index,
            overrides=dict(overrides),
        )


def value_for_field(
    field_name: str,
    *,
    persona: PersonaRecord,
    run_id: str,
    worker_index: int,
    field_type: type | None = None,
) -> Any:
    """Return a deterministic persona-backed value for a field name."""

    normalized = field_name.lower()
    if normalized in {"email", "email_address", "contact_email"} or normalized.endswith("_email"):
        return persona.contact.email
    if normalized in {"name", "full_name", "person_name", "display_name"}:
        return persona.person.name
    if normalized in {"username", "user_name", "login"}:
        return persona.person.username
    if normalized in {"phone", "phone_e164", "phone_number", "contact_phone"}:
        return persona.contact.phone_e164
    if normalized in {"company", "company_name", "organization", "organization_name"}:
        return persona.company.name
    if normalized in {"company_id", "organization_id"}:
        return persona.company.id
    if normalized in {"title", "job_title"}:
        return persona.job.title
    if normalized in {"industry", "job_industry", "company_industry"}:
        return persona.job.industry
    if normalized in {"locale", "language_locale"}:
        return persona.locale
    if normalized in {"persona_id", "external_id", "external_user_id", "veriload_persona_id"}:
        return persona.persona_id
    if normalized in {"run_id", "veriload_run_id"}:
        return run_id
    if normalized in {"worker_index", "veriload_worker_index"}:
        return worker_index
    return _fallback_value(
        field_name,
        persona=persona,
        run_id=run_id,
        worker_index=worker_index,
        field_type=field_type,
    )


def _fallback_value(
    field_name: str,
    *,
    persona: PersonaRecord,
    run_id: str,
    worker_index: int,
    field_type: type | None,
) -> Any:
    stable_number = sum(ord(char) for char in f"{run_id}:{persona.persona_id}:{field_name}")
    if field_type is int:
        return stable_number + worker_index
    if field_type is float:
        return float(stable_number + worker_index)
    if field_type is bool:
        return True
    if field_type is bytes:
        return f"{run_id}:{persona.persona_id}:{field_name}".encode()
    return f"{run_id}-{persona.persona_id}-{field_name}"
