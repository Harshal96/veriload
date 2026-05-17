"""Scenario DSL and virtual-user runtime primitives."""

from __future__ import annotations

import asyncio
import functools
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, TypeVar

from veriload.cluster import NullClusterBus
from veriload.data import PersonaRecord
from veriload.fixtures.core import ModelFixtures
from veriload.metrics import EventBus

UserCallable = Callable[..., Awaitable[Any]]
F = TypeVar("F", bound=UserCallable)


@dataclass(frozen=True)
class TaskDefinition:
    """Discovered weighted task metadata."""

    name: str
    weight: int
    method_name: str


@dataclass(frozen=True)
class FlowDefinition:
    """Discovered ordered flow metadata."""

    name: str
    method_name: str


class FlowFailed(RuntimeError):
    """Raised when an ordered flow fails."""


def task(*, weight: int = 1) -> Callable[[F], F]:
    """Mark an async method as a weighted VeriLoad task."""

    if weight <= 0:
        raise ValueError("task weight must be greater than 0")

    def decorator(func: F) -> F:
        setattr(func, "__veriload_task__", TaskDefinition(func.__name__, weight, func.__name__))
        return func

    return decorator


def flow(name: str) -> Callable[[F], F]:
    """Mark an async method as an ordered flow."""

    def decorator(func: F) -> F:
        definition = FlowDefinition(name=name, method_name=func.__name__)
        setattr(func, "__veriload_flow__", definition)

        @functools.wraps(func)
        async def wrapper(self: VeriUser, *args: Any, **kwargs: Any) -> Any:
            try:
                return await func(self, *args, **kwargs)
            except FlowFailed:
                raise
            except Exception as exc:  # noqa: BLE001 - wraps arbitrary user flow failures.
                raise FlowFailed(f"Flow {name!r} failed: {exc}") from exc

        setattr(wrapper, "__veriload_flow__", definition)
        return wrapper  # type: ignore[return-value]

    return decorator


def retryable(*, max_attempts: int, backoff_seconds: float) -> Callable[[F], F]:
    """Retry an async user method on transient exceptions."""

    if max_attempts <= 0:
        raise ValueError("max_attempts must be greater than 0")
    if backoff_seconds < 0:
        raise ValueError("backoff_seconds must be non-negative")

    def decorator(func: F) -> F:
        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            last_error: Exception | None = None
            for attempt in range(max_attempts):
                try:
                    return await func(*args, **kwargs)
                except Exception as exc:  # noqa: BLE001 - user code decides retry semantics.
                    last_error = exc
                    if attempt < max_attempts - 1 and backoff_seconds:
                        await asyncio.sleep(backoff_seconds)
            assert last_error is not None
            raise last_error

        return wrapper  # type: ignore[return-value]

    return decorator


class VeriUser:
    """Base class for standalone VeriLoad virtual users."""

    def __init__(
        self,
        *,
        persona: PersonaRecord,
        user_index: int,
        run_seed: int,
        events: EventBus | None = None,
        http: Any | None = None,
        db: Any | None = None,
        fixtures: ModelFixtures | None = None,
        cluster: Any | None = None,
    ) -> None:
        self.persona = persona
        self.user_index = user_index
        self.state: dict[str, Any] = {}
        self.events = events or EventBus()
        self.http = http
        self.db = db
        self.fixtures = fixtures or ModelFixtures(
            persona=persona,
            run_id=f"seed-{run_seed}",
            worker_index=user_index,
        )
        self.cluster = cluster or NullClusterBus()
        self._rng = random.Random(f"{run_seed}:{user_index}")
        self._stopped = False

    @property
    def persona_segment(self) -> str:
        """Return the default low-cardinality persona segment label."""

        return f"{self.persona.locale}:{self.persona.job.industry}"

    async def on_start(self) -> None:
        """Lifecycle hook called before tasks begin."""

    async def on_stop(self) -> None:
        """Lifecycle hook called before user teardown."""

    def stop(self) -> None:
        """Ask the runner to stop this user after the current task."""

        self._stopped = True

    @property
    def stopped(self) -> bool:
        """Whether this user asked the runner to stop."""

        return self._stopped

    def payload(self, mapping: dict[str, str]) -> dict[str, Any]:
        """Build a JSON-like payload by resolving dotted paths on the persona."""

        return {key: _resolve_path(self.persona, path) for key, path in mapping.items()}

    async def run_once(self) -> None:
        """Run one selected weighted task."""

        task_definition = self._choose_task()
        await getattr(self, task_definition.method_name)()

    def _choose_task(self) -> TaskDefinition:
        tasks = self.discover_tasks()
        if not tasks:
            raise RuntimeError(f"{type(self).__name__} defines no @task methods")
        total_weight = sum(item.weight for item in tasks)
        choice = self._rng.uniform(0, total_weight)
        cursor = 0.0
        for item in tasks:
            cursor += item.weight
            if choice <= cursor:
                return item
        return tasks[-1]

    @classmethod
    def discover_tasks(cls) -> list[TaskDefinition]:
        """Discover weighted task methods declared on this user class."""

        definitions = []
        for name in dir(cls):
            attr = getattr(cls, name)
            definition = getattr(attr, "__veriload_task__", None)
            if isinstance(definition, TaskDefinition):
                definitions.append(definition)
        return sorted(definitions, key=lambda item: item.method_name)

    @classmethod
    def discover_flows(cls) -> list[FlowDefinition]:
        """Discover ordered flow methods declared on this user class."""

        definitions = []
        for name in dir(cls):
            attr = getattr(cls, name)
            definition = getattr(attr, "__veriload_flow__", None)
            if isinstance(definition, FlowDefinition):
                definitions.append(definition)
        return sorted(definitions, key=lambda item: item.method_name)


def _resolve_path(root: object, path: str) -> Any:
    current = root
    for part in path.split("."):
        try:
            current = getattr(current, part)
        except AttributeError as exc:
            raise AttributeError(path) from exc
    return current
