import httpx
import pytest

from veriload.cleanup import AutoCleanupManager, CleanupConfigSnapshot
from veriload.metrics import EventBus, InMemoryMetricsSink
from veriload.protocols import DatabaseClient, HttpClient


@pytest.mark.asyncio
async def test_http_cleanup_uses_location_without_recording_request_metrics() -> None:
    requests: list[tuple[str, str]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append((request.method, request.url.path))
        if request.method == "POST":
            return httpx.Response(201, headers={"Location": "/objects/abc"}, json={"id": "abc"})
        return httpx.Response(204)

    sink = InMemoryMetricsSink()
    cleanup = AutoCleanupManager(
        CleanupConfigSnapshot(
            enabled=True,
            http_targets=(
                {
                    "method": "POST",
                    "path": "/objects",
                    "delete_path": "/objects/{id}",
                    "id_fields": ("id",),
                },
            ),
            database_tables=(),
            database_strategy="delete",
        )
    )
    client = HttpClient(
        base_url="https://api.example.test",
        events=EventBus((sink,)),
        segment="en_US:Retail",
        transport=httpx.MockTransport(handler),
        cleanup_manager=cleanup,
    )

    await client.post("/objects", name="POST /objects", json={"name": "Ada"})
    cleanup_summary = await cleanup.cleanup()
    await client.aclose()

    assert requests == [("POST", "/objects"), ("DELETE", "/objects/abc")]
    assert sink.summary().total_requests == 1
    assert cleanup_summary.attempted == 1
    assert cleanup_summary.failed == 0


@pytest.mark.asyncio
async def test_http_cleanup_records_failure_when_response_has_no_identity() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(201, json={"ok": True})

    cleanup = AutoCleanupManager(
        CleanupConfigSnapshot(
            enabled=True,
            http_targets=(
                {
                    "method": "POST",
                    "path": "/objects",
                    "delete_path": "/objects/{id}",
                    "id_fields": ("id",),
                },
            ),
            database_tables=(),
            database_strategy="delete",
        )
    )
    client = HttpClient(
        base_url="https://api.example.test",
        events=EventBus((InMemoryMetricsSink(),)),
        segment="en_US:Retail",
        transport=httpx.MockTransport(handler),
        cleanup_manager=cleanup,
    )

    await client.post("/objects", name="POST /objects")
    cleanup_summary = await cleanup.cleanup()
    await client.aclose()

    assert cleanup_summary.failed == 1
    assert cleanup_summary.failures[0].kind == "http"
    assert "identity" in cleanup_summary.failures[0].error


@pytest.mark.asyncio
async def test_database_cleanup_deletes_configured_inserted_row() -> None:
    cleanup = AutoCleanupManager(
        CleanupConfigSnapshot(
            enabled=True,
            http_targets=(),
            database_tables=("orders",),
            database_strategy="delete",
        )
    )
    client = DatabaseClient.from_sqlite(
        ":memory:",
        events=EventBus((InMemoryMetricsSink(),)),
        segment="en_US:Retail",
        cleanup_manager=cleanup,
    )

    await client.execute("CREATE TABLE orders (email TEXT NOT NULL, region TEXT NOT NULL)")
    await client.execute(
        "INSERT INTO orders (email, region) VALUES (?, ?)",
        parameters=("ada@example.invalid", "NA"),
    )
    await cleanup.cleanup()
    result = await client.query("SELECT email FROM orders")
    await client.aclose()

    assert result.rows == ()


@pytest.mark.asyncio
async def test_database_cleanup_rolls_back_configured_inserted_row() -> None:
    cleanup = AutoCleanupManager(
        CleanupConfigSnapshot(
            enabled=True,
            http_targets=(),
            database_tables=("orders",),
            database_strategy="rollback",
        )
    )
    client = DatabaseClient.from_sqlite(
        ":memory:",
        events=EventBus((InMemoryMetricsSink(),)),
        segment="en_US:Retail",
        cleanup_manager=cleanup,
    )

    await client.execute("CREATE TABLE orders (email TEXT NOT NULL)")
    await client.execute("INSERT INTO orders (email) VALUES (?)", parameters=("ada@example.invalid",))
    visible_before_cleanup = await client.query("SELECT email FROM orders")
    await cleanup.cleanup()
    visible_after_cleanup = await client.query("SELECT email FROM orders")
    await client.aclose()

    assert visible_before_cleanup.rows == (("ada@example.invalid",),)
    assert visible_after_cleanup.rows == ()


@pytest.mark.asyncio
async def test_database_cleanup_fails_fast_for_unsupported_rollback_insert() -> None:
    cleanup = AutoCleanupManager(
        CleanupConfigSnapshot(
            enabled=True,
            http_targets=(),
            database_tables=("orders",),
            database_strategy="rollback",
        )
    )
    client = DatabaseClient.from_sqlite(
        ":memory:",
        events=EventBus((InMemoryMetricsSink(),)),
        segment="en_US:Retail",
        cleanup_manager=cleanup,
    )

    await client.execute("CREATE TABLE orders (email TEXT NOT NULL)")
    with pytest.raises(ValueError, match="simple INSERT"):
        await client.execute("INSERT INTO orders SELECT 'ada@example.invalid'")
    await client.aclose()
