"""SQLModel fixture scenario.

This example assumes SQLModel is installed in the load-test environment and the
target schema already exists.
"""

from __future__ import annotations

import os

from sqlmodel import Field, Session, SQLModel, create_engine

from veriload import VeriUser, task
from veriload.fixtures import SQLModelConnector


class Customer(SQLModel, table=True):
    """Example SQLModel table populated from the current VeriLoad persona."""

    id: int | None = Field(default=None, primary_key=True)
    email: str
    name: str
    company_name: str
    run_id: str
    tenant_id: str


def session_factory() -> Session:
    """Create a short-lived SQLModel session for one fixture operation."""

    engine = create_engine(os.environ["DATABASE_URL"])
    return Session(engine)


class SQLModelFixtureUser(VeriUser):
    """Create one SQLModel row, read it during load, then delete it."""

    async def on_start(self) -> None:
        self.fixtures = self.fixtures.with_connectors(
            SQLModelConnector(session_factory=session_factory)
        )
        self.customer = await self.fixtures.create(
            Customer,
            overrides={"tenant_id": os.environ.get("TENANT_ID", "load-test")},
        )

    async def on_stop(self) -> None:
        await self.fixtures.cleanup()

    @task(weight=1)
    async def lookup_customer(self) -> None:
        result = await self.db.query(
            "SELECT email FROM customer WHERE id = ?",
            parameters=(self.customer.id,),
            name="DB select SQLModel fixture",
        )
        assert result.rows
        self.stop()
