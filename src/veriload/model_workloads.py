"""Model-derived database workload generation."""

from __future__ import annotations

import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal, cast

from veriload.cleanup import AutoCleanupManager
from veriload.fixtures.core import value_for_field
from veriload.metrics import EventBus, RequestFailed, RequestFinished
from veriload.protocols.database import DatabaseResult


class ModelWorkloadError(RuntimeError):
    """Raised when a model workload cannot be generated safely."""


@dataclass(frozen=True)
class ModelColumn:
    """Normalized model column metadata."""

    name: str
    primary_key: bool = False
    defaulted: bool = False
    python_type: type | None = None


@dataclass(frozen=True)
class ModelMetadata:
    """Normalized model metadata used to generate workloads."""

    model: object
    model_name: str
    table_name: str
    columns: tuple[ModelColumn, ...]
    source: Literal["sqlmodel", "django"]

    @property
    def primary_keys(self) -> tuple[ModelColumn, ...]:
        """Return primary key columns."""

        return tuple(column for column in self.columns if column.primary_key)

    @property
    def insert_columns(self) -> tuple[ModelColumn, ...]:
        """Return columns used for generated inserts."""

        return tuple(
            column
            for column in self.columns
            if not column.primary_key and not column.defaulted
        )

    @property
    def select_columns(self) -> tuple[ModelColumn, ...]:
        """Return columns selected by generated queries."""

        return self.columns


class SQLModelWorkload:
    """Generated raw-SQL insert/select workload for one model."""

    def __init__(
        self,
        *,
        metadata: ModelMetadata,
        db: Any,
        values: Mapping[str, Any],
        cleanup_manager: AutoCleanupManager | None,
    ) -> None:
        self.metadata = metadata
        self._db = db
        self._values = dict(values)
        self._cleanup_manager = cleanup_manager

    async def insert(self) -> dict[str, Any]:
        """Insert one generated row and return its selected representation."""

        insert_columns = self.metadata.insert_columns
        statement = _insert_statement(self.metadata.table_name, insert_columns)
        parameters = tuple(self._values[column.name] for column in insert_columns)
        await self._db.execute(
            statement,
            parameters=parameters,
            name=f"DB insert {self.metadata.table_name}",
            commit=self._commit_insert(),
        )
        row = await self._lookup_inserted_row()
        created = dict(zip((column.name for column in self.metadata.select_columns), row, strict=False))
        self._register_cleanup(created)
        return created

    async def select(self, created: Mapping[str, Any]) -> DatabaseResult:
        """Select a previously inserted row."""

        where_columns = self._identity_columns(created)
        statement = _select_statement(
            self.metadata.table_name,
            self.metadata.select_columns,
            where_columns,
        )
        parameters = tuple(created[column.name] for column in where_columns)
        return await self._db.query(
            statement,
            parameters=parameters,
            name=f"DB select {self.metadata.table_name}",
        )

    async def _lookup_inserted_row(self) -> tuple[Any, ...]:
        where_columns = self._inserted_value_columns()
        statement = _select_statement(
            self.metadata.table_name,
            self.metadata.select_columns,
            where_columns,
        )
        parameters = tuple(self._values[column.name] for column in where_columns)
        result = await self._db.query(
            statement,
            parameters=parameters,
            name=f"DB lookup inserted {self.metadata.table_name}",
        )
        if not result.rows:
            raise ModelWorkloadError(f"Inserted {self.metadata.model_name} row could not be selected")
        return tuple(result.rows[-1])

    def _register_cleanup(self, created: Mapping[str, Any]) -> None:
        if self._cleanup_manager is None or not self._cleanup_manager.enabled:
            return
        if self._cleanup_manager.database_strategy == "rollback":
            self._cleanup_manager.register_db_rollback(
                target=self.metadata.table_name,
                rollback=self._db.rollback_cleanup,
            )
            return
        where_columns = self._identity_columns(created)
        statement = _delete_statement(self.metadata.table_name, where_columns)
        parameters = tuple(created[column.name] for column in where_columns)
        self._cleanup_manager.register_action(
            kind="db",
            target=self.metadata.table_name,
            cleanup=lambda: self._db.execute_cleanup(statement, parameters=parameters),
        )

    def _commit_insert(self) -> bool:
        if self._cleanup_manager is None:
            return True
        return self._cleanup_manager.database_strategy != "rollback"

    def _identity_columns(self, created: Mapping[str, Any]) -> tuple[ModelColumn, ...]:
        primary_keys = tuple(column for column in self.metadata.primary_keys if column.name in created)
        return primary_keys or self._inserted_value_columns()

    def _inserted_value_columns(self) -> tuple[ModelColumn, ...]:
        return tuple(column for column in self.metadata.insert_columns if column.name in self._values)


class ORMModelWorkload:
    """Generated ORM insert/select workload for one model."""

    def __init__(
        self,
        *,
        metadata: ModelMetadata,
        values: Mapping[str, Any],
        events: EventBus,
        segment: str,
        persona_id: str | None,
        cleanup_manager: AutoCleanupManager | None,
        session_factory: Any | None = None,
    ) -> None:
        self.metadata = metadata
        self._values = dict(values)
        self._events = events
        self._segment = segment
        self._persona_id = persona_id
        self._cleanup_manager = cleanup_manager
        self._session_factory = session_factory

    async def insert(self) -> Any:
        """Insert one row through the model's ORM."""

        started = time.perf_counter()
        try:
            obj = self._insert_sync()
        except Exception as exc:
            self._emit_failure("insert", started, exc)
            raise
        self._emit_success("insert", started)
        self._register_cleanup(obj)
        return obj

    async def select(self, created: Any) -> Any:
        """Select one row through the model's ORM."""

        started = time.perf_counter()
        try:
            obj = self._select_sync(created)
        except Exception as exc:
            self._emit_failure("select", started, exc)
            raise
        self._emit_success("select", started)
        return obj

    def _insert_sync(self) -> Any:
        if self.metadata.source == "django":
            return cast(Any, self.metadata.model).objects.create(**self._values)
        if self._session_factory is None:
            raise ModelWorkloadError("SQLModel ORM workloads require session_factory")
        model_type = cast(type[Any], self.metadata.model)
        obj = model_type(**self._values)
        with _session_scope(self._session_factory()) as session:
            session_any = cast(Any, session)
            session_any.add(obj)
            session_any.commit()
            if hasattr(session_any, "refresh"):
                session_any.refresh(obj)
        return obj

    def _select_sync(self, created: Any) -> Any:
        identity = _object_identity(created, self.metadata)
        if self.metadata.source == "django":
            return cast(Any, self.metadata.model).objects.get(pk=identity)
        if self._session_factory is None:
            raise ModelWorkloadError("SQLModel ORM workloads require session_factory")
        with _session_scope(self._session_factory()) as session:
            get = getattr(session, "get", None)
            if callable(get):
                return get(self.metadata.model, identity)
        raise ModelWorkloadError("SQLModel ORM session does not support get()")

    def _register_cleanup(self, created: Any) -> None:
        if self._cleanup_manager is None or not self._cleanup_manager.enabled:
            return
        identity = _object_identity(created, self.metadata)
        if self.metadata.source == "django":
            self._cleanup_manager.register_action(
                kind="db",
                target=self.metadata.table_name,
                cleanup=lambda: cast(Any, self.metadata.model).objects.filter(pk__in=(identity,)).delete(),
            )
            return

        def cleanup_sqlmodel() -> None:
            if self._session_factory is None:
                raise ModelWorkloadError("SQLModel ORM workloads require session_factory")
            with _session_scope(self._session_factory()) as session:
                session_any = cast(Any, session)
                obj = session_any.get(self.metadata.model, identity) if hasattr(session_any, "get") else created
                if obj is not None:
                    session_any.delete(obj)
                    session_any.commit()

        self._cleanup_manager.register_action(
            kind="db",
            target=self.metadata.table_name,
            cleanup=cleanup_sqlmodel,
        )

    def _emit_success(self, operation: str, started: float) -> None:
        self._events.emit(
            RequestFinished(
                name=f"ORM {operation} {self.metadata.model_name}",
                method="ORM",
                status_code=0,
                latency_ms=_elapsed_ms(started),
                segment=self._segment,
                persona_id=self._persona_id,
            )
        )

    def _emit_failure(self, operation: str, started: float, exc: Exception) -> None:
        self._events.emit(
            RequestFailed(
                name=f"ORM {operation} {self.metadata.model_name}",
                method="ORM",
                error=type(exc).__name__,
                latency_ms=_elapsed_ms(started),
                segment=self._segment,
                persona_id=self._persona_id,
            )
        )


def create_model_workload(
    model: object,
    *,
    mode: Literal["sql", "orm"] = "sql",
    persona: Any,
    run_id: str,
    worker_index: int,
    overrides: Mapping[str, Any] | None = None,
    db: Any | None = None,
    events: EventBus | None = None,
    segment: str = "model",
    persona_id: str | None = None,
    cleanup_manager: AutoCleanupManager | None = None,
    session_factory: Any | None = None,
) -> SQLModelWorkload | ORMModelWorkload:
    """Create a generated insert/select workload for a model."""

    metadata = metadata_for_model(model)
    values = _values_for_insert(
        metadata.insert_columns,
        persona=persona,
        run_id=run_id,
        worker_index=worker_index,
        overrides=overrides or {},
    )
    if mode == "sql":
        if db is None:
            raise ModelWorkloadError("SQL model workloads require db")
        return SQLModelWorkload(
            metadata=metadata,
            db=db,
            values=values,
            cleanup_manager=cleanup_manager,
        )
    if events is None:
        raise ModelWorkloadError("ORM model workloads require events")
    return ORMModelWorkload(
        metadata=metadata,
        values=values,
        events=events,
        segment=segment,
        persona_id=persona_id,
        cleanup_manager=cleanup_manager,
        session_factory=session_factory,
    )


def metadata_for_model(model: object) -> ModelMetadata:
    """Infer normalized metadata from a supported model class."""

    if hasattr(model, "_meta") and hasattr(model, "objects"):
        return _django_metadata(model)
    if hasattr(model, "__table__") or hasattr(model, "model_fields"):
        return _sqlmodel_metadata(model)
    raise ModelWorkloadError(f"Unsupported model for generated workload: {model!r}")


def _sqlmodel_metadata(model: object) -> ModelMetadata:
    table = getattr(model, "__table__", None)
    if table is not None and hasattr(table, "columns"):
        table_name = str(getattr(table, "name", None) or getattr(model, "__name__", "model").lower())
        primary_key_columns = getattr(getattr(table, "primary_key", None), "columns", ())
        primary_key_names = {_column_name(column) for column in primary_key_columns}
        columns = tuple(
            ModelColumn(
                name=_column_name(column),
                primary_key=_column_name(column) in primary_key_names or bool(getattr(column, "primary_key", False)),
                defaulted=_has_default(column),
                python_type=_python_type(column),
            )
            for column in table.columns
        )
    else:
        fields = getattr(model, "model_fields", {})
        if not isinstance(fields, Mapping):
            raise ModelWorkloadError(f"Unsupported SQLModel fields for {model!r}")
        table_name = getattr(model, "__name__", "model").lower()
        columns = tuple(
            ModelColumn(
                name=str(name),
                primary_key=bool(_field_extra(field).get("primary_key")),
                defaulted=getattr(field, "default", None) is not None,
                python_type=getattr(field, "annotation", None),
            )
            for name, field in fields.items()
        )
    return ModelMetadata(
        model=model,
        model_name=getattr(model, "__name__", "Model"),
        table_name=table_name,
        columns=columns,
        source="sqlmodel",
    )


def _django_metadata(model: object) -> ModelMetadata:
    model_any = cast(Any, model)
    meta = model_any._meta
    fields = tuple(getattr(meta, "fields", ()))
    columns = tuple(
        ModelColumn(
            name=_field_attname(field),
            primary_key=bool(getattr(field, "primary_key", False)),
            defaulted=bool(getattr(field, "auto_created", False)),
            python_type=None,
        )
        for field in fields
        if bool(getattr(field, "concrete", True))
    )
    return ModelMetadata(
        model=model,
        model_name=getattr(model, "__name__", "Model"),
        table_name=str(getattr(meta, "db_table", None) or getattr(model, "__name__", "model").lower()),
        columns=columns,
        source="django",
    )


def _values_for_insert(
    columns: tuple[ModelColumn, ...],
    *,
    persona: Any,
    run_id: str,
    worker_index: int,
    overrides: Mapping[str, Any],
) -> dict[str, Any]:
    missing = tuple(
        column.name
        for column in columns
        if column.name not in overrides and not _known_persona_field(column.name)
    )
    if missing:
        joined = ", ".join(missing)
        raise ModelWorkloadError(f"Generated model workload requires overrides for: {joined}")
    return {
        column.name: overrides[column.name]
        if column.name in overrides
        else value_for_field(
            column.name,
            persona=persona,
            run_id=run_id,
            worker_index=worker_index,
            field_type=column.python_type,
        )
        for column in columns
    }


def _known_persona_field(field_name: str) -> bool:
    normalized = field_name.lower()
    return (
        normalized in _KNOWN_PERSONA_FIELDS
        or normalized.endswith("_email")
        or normalized.endswith("_name")
    )


_KNOWN_PERSONA_FIELDS = {
    "email",
    "email_address",
    "contact_email",
    "name",
    "full_name",
    "person_name",
    "display_name",
    "username",
    "user_name",
    "login",
    "phone",
    "phone_e164",
    "phone_number",
    "contact_phone",
    "company",
    "company_name",
    "organization",
    "organization_name",
    "company_id",
    "organization_id",
    "title",
    "job_title",
    "industry",
    "job_industry",
    "company_industry",
    "locale",
    "language_locale",
    "persona_id",
    "external_id",
    "external_user_id",
    "veriload_persona_id",
    "run_id",
    "veriload_run_id",
    "worker_index",
    "veriload_worker_index",
}


def _insert_statement(table: str, columns: tuple[ModelColumn, ...]) -> str:
    column_names = ", ".join(column.name for column in columns)
    placeholders = ", ".join("?" for _ in columns)
    return f"INSERT INTO {table} ({column_names}) VALUES ({placeholders})"


def _select_statement(
    table: str,
    select_columns: tuple[ModelColumn, ...],
    where_columns: tuple[ModelColumn, ...],
) -> str:
    selected = ", ".join(column.name for column in select_columns)
    where_clause = " AND ".join(f"{column.name} = ?" for column in where_columns)
    return f"SELECT {selected} FROM {table} WHERE {where_clause}"


def _delete_statement(table: str, where_columns: tuple[ModelColumn, ...]) -> str:
    where_clause = " AND ".join(f"{column.name} = ?" for column in where_columns)
    return f"DELETE FROM {table} WHERE {where_clause}"


def _column_name(column: object) -> str:
    column_any = cast(Any, column)
    return str(getattr(column_any, "key", None) or column_any.name)


def _has_default(column: object) -> bool:
    return getattr(column, "default", None) is not None or getattr(column, "server_default", None) is not None


def _python_type(column: object) -> type | None:
    explicit = getattr(column, "python_type", None)
    if isinstance(explicit, type):
        return explicit
    column_type = getattr(column, "type", None)
    if column_type is None:
        return None
    try:
        value = cast(Any, column_type).python_type
    except Exception:
        return None
    return value if isinstance(value, type) else None


def _field_extra(field: object) -> dict[str, Any]:
    extra = getattr(field, "json_schema_extra", None)
    return dict(extra) if isinstance(extra, dict) else {}


def _field_attname(field: object) -> str:
    field_any = cast(Any, field)
    return str(getattr(field_any, "attname", None) or field_any.name)


def _object_identity(obj: object, metadata: ModelMetadata) -> object:
    primary_key = metadata.primary_keys[0].name if metadata.primary_keys else "id"
    return getattr(obj, primary_key)


def _elapsed_ms(started: float) -> float:
    return (time.perf_counter() - started) * 1000


class _session_scope:
    def __init__(self, session: object) -> None:
        self._session = session

    def __enter__(self) -> object:
        enter = getattr(self._session, "__enter__", None)
        if enter is None:
            return self._session
        return enter()

    def __exit__(self, exc_type: object, exc: object, tb: object) -> object:
        exit_method = getattr(self._session, "__exit__", None)
        if exit_method is None:
            close = getattr(self._session, "close", None)
            if callable(close):
                close()
            return None
        return exit_method(exc_type, exc, tb)
