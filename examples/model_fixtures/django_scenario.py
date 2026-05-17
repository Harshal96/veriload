"""Django ORM fixture scenario.

Run this from a load-test environment where `DJANGO_SETTINGS_MODULE` is set and
Django has been initialized before VeriLoad imports the scenario.
"""

from __future__ import annotations

import os

from customers.models import Customer

from veriload import VeriUser, task
from veriload.fixtures import DjangoORMConnector


class DjangoFixtureUser(VeriUser):
    """Create one Django model instance and delete only that owned row."""

    async def on_start(self) -> None:
        self.fixtures = self.fixtures.with_connectors(DjangoORMConnector())
        self.customer = await self.fixtures.create(
            Customer,
            overrides={"tenant_id": os.environ.get("TENANT_ID", "load-test")},
        )

    async def on_stop(self) -> None:
        await self.fixtures.cleanup()

    @task(weight=1)
    async def fetch_customer(self) -> None:
        response = await self.http.get(
            f"/customers/{self.customer.id}",
            name="GET /customers/{id}",
        )
        response.raise_for_status()
        self.stop()
