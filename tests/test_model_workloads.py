from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from veriload.cleanup import AutoCleanupManager, CleanupConfigSnapshot
from veriload.metrics import EventBus, InMemoryMetricsSink
from veriload.model_workloads import ModelWorkloadError, create_model_workload
from veriload.protocols import DatabaseClient


class FakeSqlColumn:
    def __init__(
        self,
        name: str,
        *,
        primary_key: bool = False,
        default: object | None = None,
        python_type: type | None = str,
    ) -> None:
        self.name = name
        self.key = name
        self.primary_key = primary_key
        self.default = default
        self.server_default = None
        self.type = type("ColumnType", (), {"python_type": python_type})()


class FakeSqlTable:
    def __init__(self, name: str, columns: tuple[FakeSqlColumn, ...]) -> None:
        self.name = name
        self.columns = columns
        self.primary_key = type(
            "PrimaryKey",
            (),
            {"columns": tuple(column for column in columns if column.primary_key)},
        )()


@dataclass
class Customer:
    id: int | None = None
    email: str = ""
    name: str = ""
    tenant_id: str = ""


Customer.__table__ = FakeSqlTable(
    "customer",
    (
        FakeSqlColumn("id", primary_key=True, python_type=int),
        FakeSqlColumn("email"),
        FakeSqlColumn("name"),
        FakeSqlColumn("tenant_id"),
    ),
)


class FakeDjangoField:
    def __init__(
        self,
        name: str,
        *,
        primary_key: bool = False,
        auto_created: bool = False,
        concrete: bool = True,
    ) -> None:
        self.name = name
        self.attname = name
        self.primary_key = primary_key
        self.auto_created = auto_created
        self.concrete = concrete


class FakeDjangoMeta:
    db_table = "django_customer"

    def __init__(self) -> None:
        self.fields = (
            FakeDjangoField("id", primary_key=True, auto_created=True),
            FakeDjangoField("email"),
            FakeDjangoField("tenant_id"),
        )
        self.pk = self.fields[0]


class FakeQuerySet:
    def __init__(self, manager: FakeManager, identities: tuple[object, ...]) -> None:
        self.manager = manager
        self.identities = identities

    def delete(self) -> None:
        self.manager.deleted.extend(self.identities)


class FakeManager:
    def __init__(self) -> None:
        self.created: list[object] = []
        self.deleted: list[object] = []

    def create(self, **kwargs: Any) -> object:
        obj = type("DjangoCustomer", (), {})()
        for key, value in kwargs.items():
            setattr(obj, key, value)
        obj.id = 202
        self.created.append(obj)
        return obj

    def get(self, **kwargs: Any) -> object:
        identity = kwargs["pk"]
        return next(item for item in self.created if item.id == identity)

    def filter(self, **kwargs: Any) -> FakeQuerySet:
        return FakeQuerySet(self, tuple(kwargs["pk__in"]))


class DjangoCustomer:
    _meta = FakeDjangoMeta()
    objects = FakeManager()


@pytest.mark.asyncio
async def test_sql_model_workload_generates_insert_select_and_cleanup(sample_persona) -> None:
    cleanup = AutoCleanupManager(
        CleanupConfigSnapshot(
            enabled=True,
            http_targets=(),
            database_tables=(),
            database_strategy="delete",
        )
    )
    sink = InMemoryMetricsSink()
    db = DatabaseClient.from_sqlite(
        ":memory:",
        events=EventBus((sink,)),
        segment="en_US:Retail",
        cleanup_manager=cleanup,
    )
    await db.execute(
        "CREATE TABLE customer (id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT, name TEXT, tenant_id TEXT)"
    )
    workload = create_model_workload(
        Customer,
        mode="sql",
        persona=sample_persona,
        run_id="run-1",
        worker_index=0,
        db=db,
        cleanup_manager=cleanup,
        overrides={"tenant_id": "load-test"},
    )

    created = await workload.insert()
    result = await workload.select(created)
    await cleanup.cleanup()
    remaining = await db.query("SELECT email FROM customer")
    await db.aclose()

    assert created["email"] == "ada@example.invalid"
    assert result.rows == ((1, "ada@example.invalid", "Ada Lovelace", "load-test"),)
    assert remaining.rows == ()
    assert sink.summary().endpoints["DB insert customer"].total_requests == 1
    assert sink.summary().endpoints["DB select customer"].total_requests == 1


def test_model_workload_requires_overrides_for_unknown_required_fields(sample_persona) -> None:
    with pytest.raises(ModelWorkloadError, match="tenant_id"):
        create_model_workload(
            Customer,
            mode="sql",
            persona=sample_persona,
            run_id="run-1",
            worker_index=0,
            db=object(),
            cleanup_manager=None,
        )


@pytest.mark.asyncio
async def test_django_orm_model_workload_uses_manager_and_emits_metrics(sample_persona) -> None:
    DjangoCustomer.objects = FakeManager()
    sink = InMemoryMetricsSink()
    cleanup = AutoCleanupManager(
        CleanupConfigSnapshot(
            enabled=True,
            http_targets=(),
            database_tables=(),
            database_strategy="delete",
        )
    )
    workload = create_model_workload(
        DjangoCustomer,
        mode="orm",
        persona=sample_persona,
        run_id="run-1",
        worker_index=0,
        events=EventBus((sink,)),
        cleanup_manager=cleanup,
        overrides={"tenant_id": "load-test"},
    )

    created = await workload.insert()
    selected = await workload.select(created)
    await cleanup.cleanup()

    assert selected.id == 202
    assert created.email == "ada@example.invalid"
    assert DjangoCustomer.objects.deleted == [202]
    assert sink.summary().endpoints["ORM insert DjangoCustomer"].total_requests == 1
    assert sink.summary().endpoints["ORM select DjangoCustomer"].total_requests == 1
