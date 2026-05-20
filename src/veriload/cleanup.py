"""Auto-cleanup primitives for load-test-created state."""

from __future__ import annotations

import inspect
import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal
from urllib.parse import urlparse

QueryParameters = Sequence[Any] | Mapping[str, Any]
CleanupCallable = Callable[[], Awaitable[Any] | Any]


@dataclass(frozen=True)
class CleanupFailure:
    """One cleanup registration or execution failure."""

    kind: str
    target: str
    error: str


class CleanupError(RuntimeError):
    """Raised when auto-cleanup failures should fail a caller."""

    def __init__(self, summary: CleanupSummary) -> None:
        self.summary = summary
        details = "; ".join(
            f"{failure.kind} {failure.target}: {failure.error}"
            for failure in summary.failures
        )
        super().__init__(details or "auto-cleanup failed")


@dataclass(frozen=True)
class CleanupSummary:
    """Summary of auto-cleanup work performed after a run."""

    enabled: bool = False
    attempted: int = 0
    succeeded: int = 0
    failed: int = 0
    failures: tuple[CleanupFailure, ...] = ()

    @property
    def passed(self) -> bool:
        """Return whether cleanup completed without failures."""

        return self.failed == 0


@dataclass(frozen=True)
class CleanupConfigSnapshot:
    """Runtime cleanup config detached from Pydantic models."""

    enabled: bool
    http_targets: tuple[Mapping[str, Any], ...] = ()
    database_tables: tuple[str, ...] = ()
    database_strategy: Literal["delete", "rollback"] = "delete"

    @classmethod
    def disabled(cls) -> CleanupConfigSnapshot:
        """Return a disabled cleanup config."""

        return cls(enabled=False)


@dataclass(frozen=True)
class InsertStatement:
    """A parsed simple INSERT statement for cleanup tracking."""

    table: str
    columns: tuple[str, ...]
    values: tuple[Any, ...]


@dataclass(frozen=True)
class _CleanupAction:
    kind: str
    target: str
    cleanup: CleanupCallable


@dataclass
class AutoCleanupManager:
    """Registers and executes cleanup work for one virtual user."""

    config: CleanupConfigSnapshot = field(default_factory=CleanupConfigSnapshot.disabled)
    _actions: tuple[_CleanupAction, ...] = ()
    _failures: tuple[CleanupFailure, ...] = ()

    @property
    def enabled(self) -> bool:
        """Return whether cleanup tracking is enabled."""

        return self.config.enabled

    @property
    def database_strategy(self) -> Literal["delete", "rollback"]:
        """Return configured database cleanup strategy."""

        return self.config.database_strategy

    def database_table_tracked(self, table: str) -> bool:
        """Return whether a table is configured for raw DB cleanup."""

        return self.enabled and _normalize_identifier(table) in {
            _normalize_identifier(item) for item in self.config.database_tables
        }

    def register_action(self, *, kind: str, target: str, cleanup: CleanupCallable) -> None:
        """Register one cleanup action to run later."""

        if not self.enabled:
            return
        self._actions = (*self._actions, _CleanupAction(kind=kind, target=target, cleanup=cleanup))

    def record_failure(self, *, kind: str, target: str, error: str) -> None:
        """Record a cleanup failure that should fail the run."""

        if not self.enabled:
            return
        self._failures = (*self._failures, CleanupFailure(kind=kind, target=target, error=error))

    def register_http_response(
        self,
        *,
        method: str,
        path: str,
        response: Any,
        cleanup_request: Callable[[str, str], Awaitable[Any]],
    ) -> None:
        """Register cleanup for a successful configured HTTP create response."""

        if not self.enabled:
            return
        target = self._http_target(method=method, path=path)
        if target is None:
            return
        cleanup_path = _cleanup_path_from_response(target, response)
        target_label = f"{method.upper()} {path}"
        if cleanup_path is None:
            self.record_failure(
                kind="http",
                target=target_label,
                error="response did not include a cleanup identity",
            )
            return

        async def cleanup() -> None:
            response_obj = await cleanup_request("DELETE", cleanup_path)
            status_code = getattr(response_obj, "status_code", 204)
            if status_code >= 400:
                raise RuntimeError(f"HTTP {status_code}")

        self.register_action(kind="http", target=target_label, cleanup=cleanup)

    def register_db_delete(self, *, insert: InsertStatement, execute: CleanupCallable) -> None:
        """Register a DB DELETE cleanup action for a tracked insert."""

        self.register_action(kind="db", target=insert.table, cleanup=execute)

    def register_db_rollback(self, *, target: str, rollback: CleanupCallable) -> None:
        """Register a rollback cleanup action for a tracked DB insert."""

        self.register_action(kind="db", target=target, cleanup=rollback)

    async def cleanup(self) -> CleanupSummary:
        """Run all cleanup actions in reverse registration order."""

        if not self.enabled:
            return CleanupSummary(enabled=False)

        attempted = 0
        succeeded = 0
        failures = self._failures
        actions = self._actions
        self._actions = ()
        self._failures = ()
        for action in reversed(actions):
            attempted += 1
            try:
                result = action.cleanup()
                if inspect.isawaitable(result):
                    await result
            except Exception as exc:  # noqa: BLE001 - cleanup failures are reported, not reraised.
                failures = (
                    *failures,
                    CleanupFailure(
                        kind=action.kind,
                        target=action.target,
                        error=str(exc) or type(exc).__name__,
                    ),
                )
            else:
                succeeded += 1

        return CleanupSummary(
            enabled=True,
            attempted=attempted,
            succeeded=succeeded,
            failed=len(failures),
            failures=failures,
        )

    def current_summary(self) -> CleanupSummary:
        """Return a summary of recorded failures before cleanup has run."""

        return CleanupSummary(
            enabled=self.enabled,
            attempted=0,
            succeeded=0,
            failed=len(self._failures),
            failures=self._failures,
        )

    def _http_target(self, *, method: str, path: str) -> Mapping[str, Any] | None:
        normalized_method = method.upper()
        normalized_path = _path_only(path)
        for target in self.config.http_targets:
            target_method = str(target.get("method", "POST")).upper()
            target_path = _path_only(str(target.get("path", "")))
            if target_method == normalized_method and target_path == normalized_path:
                return target
        return None


def merge_cleanup_summaries(summaries: Sequence[CleanupSummary]) -> CleanupSummary:
    """Merge multiple cleanup summaries."""

    if not summaries:
        return CleanupSummary()
    failures: tuple[CleanupFailure, ...] = ()
    for summary in summaries:
        failures = (*failures, *summary.failures)
    return CleanupSummary(
        enabled=any(summary.enabled for summary in summaries),
        attempted=sum(summary.attempted for summary in summaries),
        succeeded=sum(summary.succeeded for summary in summaries),
        failed=sum(summary.failed for summary in summaries),
        failures=failures,
    )


def parse_simple_insert(
    statement: str,
    parameters: QueryParameters | None,
) -> InsertStatement | None:
    """Parse a simple parameterized one-row INSERT statement."""

    match = re.match(
        r"^\s*INSERT\s+INTO\s+([A-Za-z_][A-Za-z0-9_\.]*|\"[^\"]+\")\s*"
        r"\(([^)]+)\)\s*VALUES\s*\(([^)]+)\)\s*;?\s*$",
        statement,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if match is None:
        return None
    table = _normalize_identifier(match.group(1).split(".")[-1])
    columns = tuple(_normalize_identifier(item) for item in match.group(2).split(","))
    placeholders = tuple(item.strip() for item in match.group(3).split(","))
    values = _values_for_placeholders(placeholders, parameters)
    return InsertStatement(table=table, columns=columns, values=values)


def insert_table_name(statement: str) -> str | None:
    """Return the INSERT target table name if the statement starts with INSERT."""

    match = re.match(
        r"^\s*INSERT\s+INTO\s+([A-Za-z_][A-Za-z0-9_\.]*|\"[^\"]+\")",
        statement,
        flags=re.IGNORECASE,
    )
    if match is None:
        return None
    return _normalize_identifier(match.group(1).split(".")[-1])


def build_delete_statement(insert: InsertStatement) -> tuple[str, tuple[Any, ...]]:
    """Build a parameterized DELETE statement for a parsed INSERT."""

    where_clause = " AND ".join(f"{column} = ?" for column in insert.columns)
    return f"DELETE FROM {insert.table} WHERE {where_clause}", insert.values


def _cleanup_path_from_response(target: Mapping[str, Any], response: Any) -> str | None:
    location = _response_header(response, "location")
    if location:
        return location
    body = _response_json(response)
    identity = _identity_from_body(body, tuple(target.get("id_fields") or ("id",)))
    if identity is None:
        return None
    delete_path = str(target.get("delete_path") or f"{str(target.get('path', '')).rstrip('/')}/{{id}}")
    return delete_path.replace("{id}", identity)


def _identity_from_body(body: Mapping[str, Any], paths: tuple[Any, ...]) -> str | None:
    for raw_path in paths:
        current: Any = body
        for part in str(raw_path).split("."):
            if not isinstance(current, Mapping) or part not in current:
                current = None
                break
            current = current[part]
        if isinstance(current, (str, int)) and str(current):
            return str(current)
    return None


def _response_header(response: Any, name: str) -> str | None:
    headers = getattr(response, "headers", None)
    if headers is None:
        return None
    value = headers.get(name) if hasattr(headers, "get") else None
    return str(value) if value else None


def _response_json(response: Any) -> Mapping[str, Any]:
    json_method = getattr(response, "json", None)
    if not callable(json_method):
        return {}
    try:
        body = json_method()
    except ValueError:
        return {}
    return body if isinstance(body, Mapping) else {}


def _values_for_placeholders(
    placeholders: tuple[str, ...],
    parameters: QueryParameters | None,
) -> tuple[Any, ...]:
    if parameters is None:
        raise ValueError("simple INSERT cleanup requires parameters")
    if isinstance(parameters, Mapping):
        values = []
        for placeholder in placeholders:
            key = placeholder.lstrip(":@$")
            if key == placeholder or key not in parameters:
                raise ValueError("simple INSERT cleanup requires named parameters")
            values.append(parameters[key])
        return tuple(values)
    if len(parameters) != len(placeholders):
        raise ValueError("simple INSERT cleanup parameter count does not match columns")
    if not all(placeholder == "?" for placeholder in placeholders):
        raise ValueError("simple INSERT cleanup requires positional placeholders")
    return tuple(parameters)


def _path_only(path: str) -> str:
    parsed = urlparse(path)
    return parsed.path or path or "/"


def _normalize_identifier(value: str) -> str:
    return value.strip().strip('"').lower()
