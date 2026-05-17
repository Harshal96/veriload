import httpx
import pytest

from veriload.metrics import EventBus, InMemoryMetricsSink
from veriload.protocols import HttpClient


@pytest.mark.asyncio
async def test_http_client_records_successful_requests() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/items"
        return httpx.Response(200, json={"ok": True})

    sink = InMemoryMetricsSink()
    client = HttpClient(
        base_url="https://api.example.test",
        events=EventBus((sink,)),
        segment="en_US:Retail",
        transport=httpx.MockTransport(handler),
    )

    response = await client.get("/items", name="GET /items")
    await client.aclose()

    summary = sink.summary()
    assert response.json() == {"ok": True}
    assert summary.total_requests == 1
    assert summary.total_failures == 0
    assert summary.segments["en_US:Retail"].total_requests == 1


@pytest.mark.asyncio
async def test_http_client_records_http_errors_as_failures() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"error": "unavailable"})

    sink = InMemoryMetricsSink()
    client = HttpClient(
        base_url="https://api.example.test",
        events=EventBus((sink,)),
        segment="en_US:Retail",
        transport=httpx.MockTransport(handler),
    )

    response = await client.post("/items", name="POST /items", json={"name": "x"})
    await client.aclose()

    summary = sink.summary()
    assert response.status_code == 503
    assert summary.total_requests == 1
    assert summary.total_failures == 1


@pytest.mark.asyncio
async def test_graphql_records_operation_name_and_payload() -> None:
    seen_payload = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal seen_payload
        seen_payload = httpx.Response(200, content=request.content).json()
        return httpx.Response(200, json={"data": {"viewer": {"id": "1"}}})

    sink = InMemoryMetricsSink()
    client = HttpClient(
        base_url="https://api.example.test",
        events=EventBus((sink,)),
        segment="en_US:Retail",
        transport=httpx.MockTransport(handler),
    )

    response = await client.graphql(
        query="query Viewer($id: ID!) { viewer(id: $id) { id } }",
        operation_name="Viewer",
        variables={"id": "1"},
    )
    await client.aclose()

    assert response.json()["data"]["viewer"]["id"] == "1"
    assert seen_payload["operationName"] == "Viewer"
    assert sink.summary().total_failures == 0


@pytest.mark.asyncio
async def test_graphql_errors_are_metric_failures() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"errors": [{"message": "bad query"}]})

    sink = InMemoryMetricsSink()
    client = HttpClient(
        base_url="https://api.example.test",
        events=EventBus((sink,)),
        segment="en_US:Retail",
        transport=httpx.MockTransport(handler),
    )

    response = await client.graphql(query="{ broken }", operation_name="Broken")
    await client.aclose()

    assert response.status_code == 200
    assert sink.summary().total_failures == 1
