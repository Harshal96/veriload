import sqlite3

import pytest

from veriload.metrics import EventBus, InMemoryMetricsSink
from veriload.protocols import DatabaseClient


@pytest.mark.asyncio
async def test_database_client_records_queries_and_returns_rows() -> None:
    sink = InMemoryMetricsSink()
    client = DatabaseClient.from_sqlite(
        ":memory:",
        events=EventBus((sink,)),
        segment="en_US:Retail",
        persona_id="p1",
    )

    await client.execute(
        "CREATE TABLE orders (id INTEGER PRIMARY KEY, email TEXT NOT NULL)",
        name="create orders table",
    )
    await client.execute(
        "INSERT INTO orders (email) VALUES (:email)",
        parameters={"email": "ada@example.invalid"},
        name="insert order",
    )
    result = await client.query(
        "SELECT id, email FROM orders WHERE email = ?",
        parameters=("ada@example.invalid",),
        name="select orders by email",
    )
    await client.aclose()

    assert result.columns == ("id", "email")
    assert result.rows == ((1, "ada@example.invalid"),)
    summary = sink.summary()
    assert summary.total_requests == 3
    assert summary.total_failures == 0
    assert summary.endpoints["select orders by email"].total_requests == 1


@pytest.mark.asyncio
async def test_database_client_records_failed_queries() -> None:
    sink = InMemoryMetricsSink()
    client = DatabaseClient.from_sqlite(
        ":memory:",
        events=EventBus((sink,)),
        segment="en_US:Retail",
    )

    with pytest.raises(sqlite3.DatabaseError):
        await client.query("SELECT * FROM missing_table", name="select missing table")
    await client.aclose()

    summary = sink.summary()
    assert summary.total_requests == 1
    assert summary.total_failures == 1
    assert summary.endpoints["select missing table"].total_failures == 1


@pytest.mark.asyncio
async def test_database_client_default_metric_names_do_not_include_literals() -> None:
    sink = InMemoryMetricsSink()
    client = DatabaseClient.from_sqlite(
        ":memory:",
        events=EventBus((sink,)),
        segment="en_US:Retail",
    )

    await client.execute("CREATE TABLE users (email TEXT NOT NULL)")
    await client.execute("INSERT INTO users (email) VALUES (?)", parameters=("ada@example.invalid",))
    await client.aclose()

    endpoint_names = set(sink.summary().endpoints)
    assert endpoint_names == {"SQL CREATE", "SQL INSERT"}
    assert "ada@example.invalid" not in endpoint_names
