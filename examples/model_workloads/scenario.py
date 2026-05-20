"""Generated model workload scenario without optional ORM dependencies."""

from __future__ import annotations

from typing import Any

from veriload import VeriUser, task


class ColumnType:
    """Tiny stand-in for SQLAlchemy column type metadata."""

    def __init__(self, python_type: type) -> None:
        self.python_type = python_type


class Column:
    """Tiny stand-in for SQLAlchemy column metadata used by the workload helper."""

    def __init__(
        self,
        name: str,
        *,
        primary_key: bool = False,
        default: object | None = None,
        python_type: type = str,
    ) -> None:
        self.name = name
        self.key = name
        self.primary_key = primary_key
        self.default = default
        self.server_default = None
        self.type = ColumnType(python_type)


class PrimaryKey:
    """Tiny stand-in for SQLAlchemy primary key metadata."""

    def __init__(self, columns: tuple[Column, ...]) -> None:
        self.columns = columns


class Table:
    """Tiny stand-in for SQLAlchemy table metadata."""

    def __init__(self, name: str, columns: tuple[Column, ...]) -> None:
        self.name = name
        self.columns = columns
        self.primary_key = PrimaryKey(tuple(column for column in columns if column.primary_key))


class Customer:
    """Example SQLModel/SQLAlchemy-like model for generated DB workloads."""

    __table__ = Table(
        "generated_customers",
        (
            Column("id", primary_key=True, python_type=int),
            Column("email"),
            Column("name"),
            Column("company_name"),
            Column("tenant_id"),
        ),
    )

    def __init__(self, **kwargs: Any) -> None:
        for key, value in kwargs.items():
            setattr(self, key, value)


class GeneratedModelWorkloadUser(VeriUser):
    """Generate insert/select SQL from model metadata and clean up the row."""

    async def on_start(self) -> None:
        await self.db.execute(
            """
            CREATE TABLE IF NOT EXISTS generated_customers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT NOT NULL,
                name TEXT NOT NULL,
                company_name TEXT NOT NULL,
                tenant_id TEXT NOT NULL
            )
            """,
            name="DB create generated_customers table",
        )
        self.customer_workload = self.model_workload(
            Customer,
            mode="sql",
            overrides={"tenant_id": "load-test"},
        )

    @task(weight=1)
    async def insert_and_select_customer(self) -> None:
        customer = await self.customer_workload.insert()
        result = await self.customer_workload.select(customer)
        if not result.rows:
            raise RuntimeError("Generated customer workload did not return a row")
        self.stop()
