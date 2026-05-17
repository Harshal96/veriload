import pytest

from veriload.metrics import EventBus, InMemoryMetricsSink
from veriload.protocols import GrpcClient


@pytest.mark.asyncio
async def test_grpc_client_records_successful_unary_call() -> None:
    seen_request = {}
    seen_metadata = ()

    async def say_hello(request: dict, *, metadata: tuple, timeout: float) -> dict:
        nonlocal seen_request, seen_metadata
        seen_request = request
        seen_metadata = metadata
        assert timeout == 3
        return {"message": f"hello {request['name']}"}

    sink = InMemoryMetricsSink()
    client = GrpcClient(events=EventBus((sink,)), segment="en_US:Retail")

    response = await client.unary(
        "helloworld.Greeter/SayHello",
        say_hello,
        {"name": "Ada"},
        metadata=(("authorization", "token"),),
        timeout=3,
    )

    assert response == {"message": "hello Ada"}
    assert seen_request == {"name": "Ada"}
    assert seen_metadata == (("authorization", "token"),)
    summary = sink.summary()
    assert summary.total_requests == 1
    assert summary.total_failures == 0
    assert summary.endpoints["gRPC helloworld.Greeter/SayHello"].total_requests == 1


@pytest.mark.asyncio
async def test_grpc_client_records_failed_unary_call() -> None:
    async def say_hello(request: dict, *, metadata: tuple, timeout: float | None) -> dict:
        raise RuntimeError("deadline exceeded")

    sink = InMemoryMetricsSink()
    client = GrpcClient(events=EventBus((sink,)), segment="en_US:Retail")

    with pytest.raises(RuntimeError, match="deadline exceeded"):
        await client.unary("helloworld.Greeter/SayHello", say_hello, {"name": "Ada"})

    summary = sink.summary()
    assert summary.total_requests == 1
    assert summary.total_failures == 1
