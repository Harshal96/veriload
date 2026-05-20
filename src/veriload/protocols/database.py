"""Database protocol adapter."""

from __future__ import annotations

import asyncio
import sqlite3
import time
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, cast

from veriload.cleanup import (
    AutoCleanupManager,
    build_delete_statement,
    insert_table_name,
    parse_simple_insert,
)
from veriload.metrics import EventBus, RequestFailed, RequestFinished

QueryParameters = Sequence[Any] | Mapping[str, Any]


class DatabaseCursor(Protocol):
    """Minimal DB-API cursor surface used by the database adapter."""

    description: Sequence[Sequence[Any]] | None
    rowcount: int

    def execute(self, statement: str, parameters: QueryParameters = ()) -> Any:
        """Execute one SQL statement."""

    def fetchall(self) -> Sequence[Any]:
        """Fetch all rows for the current result set."""

    def close(self) -> Any:
        """Close the cursor."""


class DatabaseConnection(Protocol):
    """Minimal DB-API connection surface used by the database adapter."""

    def cursor(self) -> DatabaseCursor:
        """Create a cursor."""

    def commit(self) -> Any:
        """Commit the current transaction."""

    def rollback(self) -> Any:
        """Rollback the current transaction."""

    def close(self) -> Any:
        """Close the connection."""


ConnectionFactory = Callable[[], DatabaseConnection]


@dataclass(frozen=True)
class DatabaseResult:
    """Result returned from a database operation."""

    columns: tuple[str, ...]
    rows: tuple[Any, ...]
    rowcount: int


class DatabaseClient:
    """Async metric-recording wrapper for Python DB-API database drivers."""

    def __init__(
        self,
        *,
        connector: ConnectionFactory,
        events: EventBus,
        segment: str,
        persona_id: str | None = None,
        cleanup_manager: AutoCleanupManager | None = None,
    ) -> None:
        self._connector = connector
        self._events = events
        self._segment = segment
        self._persona_id = persona_id
        self._cleanup_manager = cleanup_manager
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="veriload-db")
        self._connection: DatabaseConnection | None = None
        self._closed = False
        self._rollback_cleanup_registered = False
        self._rollback_cleanup_pending = False

    @classmethod
    def from_sqlite(
        cls,
        database: str | Path,
        *,
        events: EventBus,
        segment: str,
        persona_id: str | None = None,
        connect_kwargs: Mapping[str, Any] | None = None,
        cleanup_manager: AutoCleanupManager | None = None,
    ) -> DatabaseClient:
        """Create a client backed by Python's built-in sqlite3 driver."""

        def connector() -> DatabaseConnection:
            return cast(
                DatabaseConnection,
                sqlite3.connect(str(database), **dict(connect_kwargs or {})),
            )

        return cls(
            connector=connector,
            events=events,
            segment=segment,
            persona_id=persona_id,
            cleanup_manager=cleanup_manager,
        )

    async def execute(
        self,
        statement: str,
        *,
        parameters: QueryParameters | None = None,
        name: str | None = None,
        commit: bool = True,
    ) -> DatabaseResult:
        """Execute one SQL statement and record a metric event."""

        cleanup_insert = self._tracked_insert(statement, parameters)
        if cleanup_insert is not None and self._cleanup_manager is not None:
            if self._cleanup_manager.database_strategy == "rollback":
                commit = False
            result = await self._recorded(
                statement,
                name=name,
                operation=lambda: self._execute_sync(
                    statement,
                    parameters=parameters,
                    fetch=False,
                    commit=commit,
                ),
            )
            if self._cleanup_manager.database_strategy == "delete":
                delete_statement, delete_parameters = build_delete_statement(cleanup_insert)
                self._cleanup_manager.register_db_delete(
                    insert=cleanup_insert,
                    execute=lambda: self.execute_cleanup(
                        delete_statement,
                        parameters=delete_parameters,
                    ),
                )
            else:
                self._register_rollback_cleanup(cleanup_insert.table)
                self._rollback_cleanup_pending = True
            return result

        if self._rollback_cleanup_pending and commit:
            raise RuntimeError("rollback cleanup cannot commit while tracked inserts are pending")

        return await self._recorded(
            statement,
            name=name,
            operation=lambda: self._execute_sync(
                statement,
                parameters=parameters,
                fetch=False,
                commit=commit,
            ),
        )

    async def execute_cleanup(
        self,
        statement: str,
        *,
        parameters: QueryParameters | None = None,
        commit: bool = True,
    ) -> DatabaseResult:
        """Execute cleanup SQL without emitting load-test metrics."""

        return await self._run_db(
            lambda: self._execute_sync(
                statement,
                parameters=parameters,
                fetch=False,
                commit=commit,
            )
        )

    async def rollback_cleanup(self) -> None:
        """Rollback the current transaction without emitting load-test metrics."""

        await self._run_db(self._rollback_sync)
        self._rollback_cleanup_pending = False

    async def query(
        self,
        statement: str,
        *,
        parameters: QueryParameters | None = None,
        name: str | None = None,
    ) -> DatabaseResult:
        """Execute one SQL query, fetch all rows, and record a metric event."""

        return await self._recorded(
            statement,
            name=name,
            operation=lambda: self._execute_sync(
                statement,
                parameters=parameters,
                fetch=True,
                commit=False,
            ),
        )

    async def aclose(self) -> None:
        """Close the database connection and its worker executor."""

        if self._closed:
            return
        try:
            await self._run_db(self._close_sync)
        finally:
            self._closed = True
            self._executor.shutdown(wait=True)

    async def _recorded(
        self,
        statement: str,
        *,
        name: str | None,
        operation: Callable[[], DatabaseResult],
    ) -> DatabaseResult:
        metric_name = name or _default_metric_name(statement)
        started = time.perf_counter()
        try:
            result = await self._run_db(operation)
        except Exception as exc:
            self._events.emit(
                RequestFailed(
                    name=metric_name,
                    method="SQL",
                    error=type(exc).__name__,
                    latency_ms=_elapsed_ms(started),
                    segment=self._segment,
                    persona_id=self._persona_id,
                )
            )
            raise

        self._events.emit(
            RequestFinished(
                name=metric_name,
                method="SQL",
                status_code=0,
                latency_ms=_elapsed_ms(started),
                segment=self._segment,
                persona_id=self._persona_id,
            )
        )
        return result

    async def _run_db(self, operation: Callable[[], Any]) -> Any:
        if self._closed:
            raise RuntimeError("database client is closed")
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._executor, operation)

    def _execute_sync(
        self,
        statement: str,
        *,
        parameters: QueryParameters | None,
        fetch: bool,
        commit: bool,
    ) -> DatabaseResult:
        connection = self._connection_sync()
        cursor = connection.cursor()
        try:
            if parameters is None:
                cursor.execute(statement)
            else:
                cursor.execute(statement, parameters)
            rows = _freeze_rows(cursor.fetchall()) if fetch else ()
            columns = _column_names(cursor.description)
            rowcount = cursor.rowcount
            if commit:
                connection.commit()
            return DatabaseResult(columns=columns, rows=rows, rowcount=rowcount)
        except Exception:
            _rollback_quietly(connection)
            raise
        finally:
            _close_quietly(cursor)

    def _connection_sync(self) -> DatabaseConnection:
        if self._connection is None:
            self._connection = self._connector()
        return self._connection

    def _close_sync(self) -> None:
        if self._connection is not None:
            _close_quietly(self._connection)
            self._connection = None

    def _tracked_insert(
        self,
        statement: str,
        parameters: QueryParameters | None,
    ):
        if self._cleanup_manager is None or not self._cleanup_manager.enabled:
            return None
        table = insert_table_name(statement)
        if table is None or not self._cleanup_manager.database_table_tracked(table):
            return None
        parsed = parse_simple_insert(statement, parameters)
        if parsed is None:
            raise ValueError("auto-cleanup supports simple INSERT statements only")
        return parsed

    def _register_rollback_cleanup(self, target: str) -> None:
        if self._cleanup_manager is None or self._rollback_cleanup_registered:
            return

        async def rollback() -> None:
            await self.rollback_cleanup()

        self._cleanup_manager.register_db_rollback(target=target, rollback=rollback)
        self._rollback_cleanup_registered = True

    def _rollback_sync(self) -> None:
        if self._connection is not None:
            self._connection.rollback()


def _column_names(description: Sequence[Sequence[Any]] | None) -> tuple[str, ...]:
    if description is None:
        return ()
    return tuple(str(column[0]) for column in description)


def _freeze_rows(rows: Sequence[Any]) -> tuple[Any, ...]:
    return tuple(tuple(row) if isinstance(row, list) else row for row in rows)


def _default_metric_name(statement: str) -> str:
    keyword = _first_sql_keyword(statement)
    if not keyword:
        return "SQL"
    return f"SQL {keyword}"


def _first_sql_keyword(statement: str) -> str:
    for token in statement.strip().replace("\n", " ").split():
        normalized = token.strip().upper()
        if normalized:
            return normalized
    return ""


def _rollback_quietly(connection: DatabaseConnection) -> None:
    try:
        connection.rollback()
    except Exception:
        return


def _close_quietly(target: Any) -> None:
    close = getattr(target, "close", None)
    if close is None:
        return
    try:
        close()
    except Exception:
        return


def _elapsed_ms(started: float) -> float:
    return (time.perf_counter() - started) * 1000
