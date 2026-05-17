import pytest

from veriload.data import (
    InMemoryPersonaSource,
    LocaleWeight,
    PersonaAllocator,
    PersonaPool,
    PoolExhausted,
    build_run_manifest,
)


def test_persona_pool_generation_is_deterministic() -> None:
    source = InMemoryPersonaSource()

    first = PersonaPool.generate(
        source=source,
        pool_size=5,
        seed=123,
        locales=[LocaleWeight(locale="en_US", weight=0.7), LocaleWeight(locale="hi_IN", weight=0.3)],
    )
    second = PersonaPool.generate(
        source=source,
        pool_size=5,
        seed=123,
        locales=[LocaleWeight(locale="en_US", weight=0.7), LocaleWeight(locale="hi_IN", weight=0.3)],
    )

    assert [persona.person.username for persona in first.personas] == [
        persona.person.username for persona in second.personas
    ]
    assert [persona.locale for persona in first.personas] == [
        persona.locale for persona in second.personas
    ]


def test_unique_allocator_fails_on_exhaustion(sample_persona) -> None:
    allocator = PersonaAllocator(PersonaPool([sample_persona]), mode="unique")

    assert allocator.acquire(0) == sample_persona
    with pytest.raises(PoolExhausted):
        allocator.acquire(1)


def test_reuse_allocator_round_robins(sample_persona) -> None:
    allocator = PersonaAllocator(PersonaPool([sample_persona]), mode="reuse")

    assert allocator.acquire(0) == sample_persona
    assert allocator.acquire(1) == sample_persona


def test_partitioning_is_deterministic_and_non_overlapping() -> None:
    pool = PersonaPool.generate(source=InMemoryPersonaSource(), pool_size=10, seed=4)

    first = pool.partition(worker_index=0, worker_count=2)
    second = pool.partition(worker_index=1, worker_count=2)

    first_ids = {persona.persona_id for persona in first.personas}
    second_ids = {persona.persona_id for persona in second.personas}
    assert not first_ids & second_ids
    assert first_ids | second_ids == {persona.persona_id for persona in pool.personas}


def test_manifest_contains_replay_inputs() -> None:
    manifest = build_run_manifest(
        seed=123,
        pool_size=10,
        locales=["en_US"],
        verisim_version="0.1.0",
        scenario_hash="abc",
        config_hash="def",
        git_sha="1234567",
        python_version="3.12.0",
        worker_topology="local",
    )

    assert manifest["seed"] == 123
    assert manifest["scenario_hash"] == "abc"
    assert manifest["worker_topology"] == "local"


def test_persona_pool_rejects_empty_generation() -> None:
    with pytest.raises(ValueError, match="pool_size"):
        PersonaPool.generate(source=InMemoryPersonaSource(), pool_size=0, seed=1)


def test_partition_rejects_invalid_worker_arguments(sample_persona) -> None:
    pool = PersonaPool([sample_persona])

    with pytest.raises(ValueError, match="worker_count"):
        pool.partition(worker_index=0, worker_count=0)

    with pytest.raises(ValueError, match="worker_index"):
        pool.partition(worker_index=2, worker_count=2)


def test_random_allocator_is_deterministic() -> None:
    pool = PersonaPool.generate(source=InMemoryPersonaSource(), pool_size=4, seed=11)
    first = PersonaAllocator(pool, mode="random", seed=99)
    second = PersonaAllocator(pool, mode="random", seed=99)

    assert first.acquire(3).persona_id == second.acquire(3).persona_id


def test_allocator_rejects_empty_pool() -> None:
    allocator = PersonaAllocator(PersonaPool([]), mode="reuse")

    with pytest.raises(PoolExhausted, match="empty"):
        allocator.acquire(0)


def test_allocator_rejects_unknown_mode(sample_persona) -> None:
    allocator = PersonaAllocator(PersonaPool([sample_persona]), mode="mystery")  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="Unsupported"):
        allocator.acquire(0)
