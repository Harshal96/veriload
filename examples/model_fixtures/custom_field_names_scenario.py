"""Fixture scenario for models whose fields do not match built-in aliases."""

from __future__ import annotations

import os

from sqlmodel import Field, Session, SQLModel, create_engine

from veriload import VeriUser, task
from veriload.fixtures import SQLModelConnector


class Customer(SQLModel, table=True):
    """Example table with app-specific names that need explicit wiring."""

    id: int | None = Field(default=None, primary_key=True)
    primary_email: str
    legal_name: str
    account_company: str
    external_ref: str
    tenant_id: str


def session_factory() -> Session:
    engine = create_engine(os.environ["DATABASE_URL"])
    return Session(engine)


class CustomFieldNamesUser(VeriUser):
    """Use overrides to wire non-standard model fields to persona attributes."""

    async def on_start(self) -> None:
        self.fixtures = self.fixtures.with_connectors(
            SQLModelConnector(session_factory=session_factory)
        )
        self.customer = await self.fixtures.create(
            Customer,
            overrides={
                "primary_email": self.persona.contact.email,
                "legal_name": self.persona.person.name,
                "account_company": self.persona.company.name,
                "external_ref": self.persona.persona_id,
                "tenant_id": os.environ.get("TENANT_ID", "load-test"),
            },
        )

    async def on_stop(self) -> None:
        await self.fixtures.cleanup()

    @task(weight=1)
    async def lookup_customer(self) -> None:
        result = await self.db.query(
            "SELECT primary_email FROM customer WHERE id = ?",
            parameters=(self.customer.id,),
            name="DB select custom field fixture",
        )
        assert result.rows
        self.stop()
