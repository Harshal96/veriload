"""OpenAPI resource fixture scenario.

The connector uses the provided OpenAPI file to create a cleanup-safe resource
through HTTP, then deletes the created resource during `on_stop()`.
"""

from __future__ import annotations

from pathlib import Path

from veriload import VeriUser, task
from veriload.fixtures import OpenAPIConnector

OPENAPI_PATH = Path(__file__).with_name("openapi.yaml")


class OpenAPIResourceFixtureUser(VeriUser):
    """Create a Customer resource with POST and delete it with DELETE."""

    async def on_start(self) -> None:
        self.fixtures = self.fixtures.with_connectors(
            OpenAPIConnector.from_file(
                OPENAPI_PATH,
                http=self.http,
                resource="Customer",
            )
        )
        self.customer = await self.fixtures.create("Customer")

    async def on_stop(self) -> None:
        await self.fixtures.cleanup()

    @task(weight=1)
    async def read_customer(self) -> None:
        response = await self.http.get(
            f"/customers/{self.customer['id']}",
            name="GET /customers/{customerId}",
        )
        response.raise_for_status()
        self.stop()
