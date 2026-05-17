import pytest

from veriload.metrics import EventBus, InMemoryMetricsSink
from veriload.protocols import WebSocketClient


class FakeWebSocket:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send(self, message: str) -> None:
        self.sent.append(message)

    async def recv(self) -> str:
        return '{"accepted": true}'


class FakeConnection:
    def __init__(self, socket: FakeWebSocket) -> None:
        self.socket = socket

    async def __aenter__(self) -> FakeWebSocket:
        return self.socket

    async def __aexit__(self, exc_type: object, exc: object, traceback: object) -> None:
        return None


@pytest.mark.asyncio
async def test_websocket_client_records_json_exchange_metrics() -> None:
    socket = FakeWebSocket()
    seen_uri = ""

    async def connector(uri: str) -> FakeConnection:
        nonlocal seen_uri
        seen_uri = uri
        return FakeConnection(socket)

    sink = InMemoryMetricsSink()
    client = WebSocketClient(
        base_url="wss://socket.example.test",
        events=EventBus((sink,)),
        segment="en_US:Retail",
        connector=connector,
    )

    response = await client.request_json("/events", name="WS /events", payload={"ping": True})

    assert seen_uri == "wss://socket.example.test/events"
    assert socket.sent == ['{"ping": true}']
    assert response == {"accepted": True}
    summary = sink.summary()
    assert summary.total_requests == 1
    assert summary.total_failures == 0
    assert summary.endpoints["WS /events"].total_requests == 1


@pytest.mark.asyncio
async def test_websocket_client_records_failures() -> None:
    async def connector(uri: str) -> FakeConnection:
        raise RuntimeError("connection refused")

    sink = InMemoryMetricsSink()
    client = WebSocketClient(
        base_url="wss://socket.example.test",
        events=EventBus((sink,)),
        segment="en_US:Retail",
        connector=connector,
    )

    with pytest.raises(RuntimeError, match="connection refused"):
        await client.request_json("/events", name="WS /events", payload={"ping": True})

    summary = sink.summary()
    assert summary.total_requests == 1
    assert summary.total_failures == 1
