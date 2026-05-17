"""SQLModel/SQLAlchemy fixture connector."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, cast

from veriload.fixtures.core import FixtureContext, FixtureRecord, ModelConnector

SessionFactory = Callable[[], Any]


class SQLModelConnector(ModelConnector):
    """Create and delete SQLModel-style ORM rows."""

    def __init__(self, *, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory

    def supports(self, model: object) -> bool:
        return hasattr(model, "__table__") or hasattr(model, "model_fields")

    async def create(self, model: object, context: FixtureContext) -> FixtureRecord:
        columns = _model_columns(model)
        field_names = tuple(
            _column_name(column)
            for column in columns
            if not column.primary_key and not _has_default(column)
        )
        type_hints = {
            _column_name(column): python_type
            for column in columns
            if not column.primary_key and (python_type := _python_type(column)) is not None
        }
        model_type = cast(type[Any], model)
        obj = model_type(**context.values_for(field_names, type_hints=type_hints))
        with _session_scope(self._session_factory()) as session:
            session_any = cast(Any, session)
            session_any.add(obj)
            session_any.commit()
            if hasattr(session_any, "refresh"):
                session_any.refresh(obj)
            identity = _identity_from_obj(obj, _primary_key_names(model))
        return FixtureRecord(
            connector=self,
            target=model,
            identity=identity,
            obj=obj,
            metadata={"primary_keys": _primary_key_names(model)},
        )

    async def delete(self, record: FixtureRecord, context: FixtureContext) -> None:
        primary_keys = tuple((record.metadata or {}).get("primary_keys") or ())
        with _session_scope(self._session_factory()) as session:
            obj = _lookup_for_delete(session, record, primary_keys)
            if obj is None:
                return
            session_any = cast(Any, session)
            session_any.delete(obj)
            session_any.commit()


class _ColumnInfo:
    def __init__(
        self,
        *,
        name: str,
        primary_key: bool = False,
        default: object | None = None,
        nullable: bool = False,
        python_type: type | None = None,
    ) -> None:
        self.name = name
        self.primary_key = primary_key
        self.default = default
        self.nullable = nullable
        self.python_type = python_type


def _model_columns(model: object) -> tuple[Any, ...]:
    table = getattr(model, "__table__", None)
    if table is not None and hasattr(table, "columns"):
        return tuple(table.columns)
    fields = getattr(model, "model_fields", None)
    if isinstance(fields, dict):
        return tuple(
            _ColumnInfo(
                name=name,
                primary_key=bool(_field_extra(field).get("primary_key")),
                default=getattr(field, "default", None),
                python_type=getattr(field, "annotation", None),
            )
            for name, field in fields.items()
        )
    return ()


def _primary_key_names(model: object) -> tuple[str, ...]:
    table = getattr(model, "__table__", None)
    primary_key = getattr(table, "primary_key", None)
    columns = getattr(primary_key, "columns", None)
    if columns is not None:
        return tuple(_column_name(column) for column in columns)
    return tuple(_column_name(column) for column in _model_columns(model) if column.primary_key)


def _field_extra(field: object) -> dict[str, Any]:
    extra = getattr(field, "json_schema_extra", None)
    return dict(extra) if isinstance(extra, dict) else {}


def _column_name(column: object) -> str:
    column_any = cast(Any, column)
    return str(getattr(column_any, "key", None) or column_any.name)


def _has_default(column: object) -> bool:
    default = getattr(column, "default", None)
    server_default = getattr(column, "server_default", None)
    return default is not None or server_default is not None


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


def _identity_from_obj(obj: object, primary_keys: tuple[str, ...]) -> str:
    if not primary_keys:
        return str(getattr(obj, "id", ""))
    return ":".join(str(getattr(obj, key)) for key in primary_keys)


def _lookup_for_delete(session: object, record: FixtureRecord, primary_keys: tuple[str, ...]) -> object | None:
    if hasattr(session, "get") and len(primary_keys) == 1:
        return session.get(record.target, _coerce_identity(record.identity))
    return record.obj


def _coerce_identity(identity: str) -> object:
    try:
        return int(identity)
    except ValueError:
        return identity


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
