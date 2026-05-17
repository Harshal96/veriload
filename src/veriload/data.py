"""Persona data plane primitives for VeriLoad."""

from __future__ import annotations

import platform
import random
from dataclasses import dataclass, replace
from typing import Literal, Protocol


@dataclass(frozen=True)
class Person:
    """Human identity fields used in payload generation."""

    name: str
    username: str


@dataclass(frozen=True)
class Contact:
    """Synthetic contact fields."""

    email: str
    phone_e164: str


@dataclass(frozen=True)
class Job:
    """Synthetic job attributes."""

    title: str
    industry: str


@dataclass(frozen=True)
class Company:
    """Synthetic organization attributes."""

    id: str
    name: str
    industry: str


@dataclass(frozen=True)
class PersonaRecord:
    """Minimal VeriSim-compatible persona aggregate used by the runtime."""

    persona_id: str
    locale: str
    person: Person
    contact: Contact
    job: Job
    company: Company

    def copy_with_id(self, persona_id: str) -> PersonaRecord:
        """Return a copy with a different persona identifier."""

        return replace(self, persona_id=persona_id)


@dataclass(frozen=True)
class LocaleWeight:
    """Weighted locale input for deterministic pool generation."""

    locale: str
    weight: float


class PersonaSource(Protocol):
    """Generates persona records for a pool."""

    def generate_persona(self, *, index: int, seed: int, locale: str) -> PersonaRecord:
        """Generate a single persona."""


class PersonaSourceError(RuntimeError):
    """Raised when an external persona source cannot generate data."""


class InMemoryPersonaSource:
    """Deterministic built-in persona source for tests and offline fixtures."""

    _industries = ("Computing", "Retail", "Healthcare Technology", "Finance")
    _titles = ("Engineer", "Manager", "Analyst", "Designer")

    def generate_persona(self, *, index: int, seed: int, locale: str) -> PersonaRecord:
        rng = random.Random(f"{seed}:{index}:{locale}")
        industry = self._industries[rng.randrange(len(self._industries))]
        title = self._titles[rng.randrange(len(self._titles))]
        username = f"{locale.lower()}-{seed}-{index}"
        return PersonaRecord(
            persona_id=f"p{index}",
            locale=locale,
            person=Person(name=f"Persona {index}", username=username),
            contact=Contact(
                email=f"{username}@example.invalid",
                phone_e164=f"+1555{index:07d}"[-12:],
            ),
            job=Job(title=title, industry=industry),
            company=Company(
                id=f"company-{index % 10}",
                name=f"Company {index % 10}",
                industry=industry,
            ),
        )


class VerisimPersonaSource:
    """Persona source backed by the external verisim package."""

    def generate_persona(self, *, index: int, seed: int, locale: str) -> PersonaRecord:
        """Generate one persona using verisim and map it into VeriLoad's model."""

        try:
            from verisim import PersonRecord as VerisimPersonRecord
            from verisim import Verisim
        except ImportError as exc:
            raise PersonaSourceError(
                "VeriLoad data.source='verisim' requires the verisim package. "
                "Install project dependencies with `uv sync`."
            ) from exc

        generator = Verisim(locale=locale, seed=seed + index)
        record = generator.generate(VerisimPersonRecord)
        return _map_verisim_persona(record, persona_id=f"p{index}", locale=locale)


def _map_verisim_persona(record: object, *, persona_id: str, locale: str) -> PersonaRecord:
    return PersonaRecord(
        persona_id=persona_id,
        locale=locale,
        person=Person(
            name=_read_attr(record, "person.name"),
            username=_read_attr(record, "person.username"),
        ),
        contact=Contact(
            email=_read_attr(record, "contact.email"),
            phone_e164=_read_attr(record, "contact.phone.e164"),
        ),
        job=Job(
            title=_read_attr(record, "job.title"),
            industry=_read_attr(record, "job.industry"),
        ),
        company=Company(
            id=_read_attr_with_fallback(record, "company.id", "company.name"),
            name=_read_attr(record, "company.name"),
            industry=_read_attr_with_fallback(record, "company.industry", "job.industry"),
        ),
    )


def _read_attr(root: object, path: str, *, default: object | None = None) -> str:
    current = root
    for part in path.split("."):
        if not hasattr(current, part):
            if default is not None:
                return str(default)
            raise AttributeError(path)
        current = getattr(current, part)
    return str(current)


def _read_attr_with_fallback(root: object, primary: str, fallback: str) -> str:
    try:
        return _read_attr(root, primary)
    except AttributeError:
        return _read_attr(root, fallback)


class PoolExhausted(RuntimeError):
    """Raised when a persona allocator cannot provide another persona."""


@dataclass(frozen=True)
class PersonaPool:
    """Immutable collection of generated personas."""

    personas: tuple[PersonaRecord, ...]

    def __init__(self, personas: list[PersonaRecord] | tuple[PersonaRecord, ...]):
        object.__setattr__(self, "personas", tuple(personas))

    @classmethod
    def generate(
        cls,
        *,
        source: PersonaSource,
        pool_size: int,
        seed: int,
        locales: list[LocaleWeight] | tuple[LocaleWeight, ...] | None = None,
    ) -> PersonaPool:
        """Generate a deterministic persona pool."""

        if pool_size <= 0:
            raise ValueError("pool_size must be greater than 0")
        locale_sequence = _expand_locales(pool_size, locales or (LocaleWeight("en_US", 1.0),))
        personas = [
            source.generate_persona(index=index, seed=seed, locale=locale)
            for index, locale in enumerate(locale_sequence)
        ]
        return cls(personas)

    def partition(self, *, worker_index: int, worker_count: int) -> PersonaPool:
        """Return the deterministic non-overlapping worker partition."""

        if worker_count <= 0:
            raise ValueError("worker_count must be greater than 0")
        if not 0 <= worker_index < worker_count:
            raise ValueError("worker_index must be between 0 and worker_count - 1")
        return PersonaPool(
            [
                persona
                for index, persona in enumerate(self.personas)
                if index % worker_count == worker_index
            ]
        )


AllocationMode = Literal["unique", "reuse", "sequence", "random"]


@dataclass
class PersonaAllocator:
    """Allocates personas to virtual users according to a deterministic mode."""

    pool: PersonaPool
    mode: AllocationMode = "unique"
    seed: int = 0
    _next_index: int = 0

    def acquire(self, user_index: int) -> PersonaRecord:
        """Acquire a persona for one virtual user."""

        if not self.pool.personas:
            raise PoolExhausted("persona pool is empty")
        if self.mode == "unique":
            if self._next_index >= len(self.pool.personas):
                raise PoolExhausted("persona pool exhausted")
            persona = self.pool.personas[self._next_index]
            self._next_index += 1
            return persona
        if self.mode in {"reuse", "sequence"}:
            return self.pool.personas[user_index % len(self.pool.personas)]
        if self.mode == "random":
            rng = random.Random(f"{self.seed}:{user_index}")
            return self.pool.personas[rng.randrange(len(self.pool.personas))]
        raise ValueError(f"Unsupported persona allocation mode: {self.mode}")


def build_run_manifest(
    *,
    seed: int,
    pool_size: int,
    locales: list[str],
    verisim_version: str,
    scenario_hash: str,
    config_hash: str,
    git_sha: str,
    python_version: str | None = None,
    worker_topology: str,
) -> dict[str, object]:
    """Build the replay manifest inputs that identify a run."""

    return {
        "seed": seed,
        "pool_size": pool_size,
        "locales": tuple(locales),
        "verisim_version": verisim_version,
        "scenario_hash": scenario_hash,
        "config_hash": config_hash,
        "git_sha": git_sha,
        "python_version": python_version or platform.python_version(),
        "worker_topology": worker_topology,
    }


def _expand_locales(
    pool_size: int, locales: list[LocaleWeight] | tuple[LocaleWeight, ...]
) -> tuple[str, ...]:
    total_weight = sum(item.weight for item in locales)
    if total_weight <= 0:
        raise ValueError("locale weights must sum to more than 0")
    counts = [int(pool_size * item.weight / total_weight) for item in locales]
    while sum(counts) < pool_size:
        remainder_scores = [
            (pool_size * item.weight / total_weight - count, index)
            for index, (item, count) in enumerate(zip(locales, counts, strict=True))
        ]
        _, index = max(remainder_scores)
        counts[index] += 1
    sequence: list[str] = []
    for item, count in zip(locales, counts, strict=True):
        sequence.extend([item.locale] * count)
    return tuple(sequence[:pool_size])
