import pytest

from veriload.cluster import ClusterMessage, InProcessClusterBus, distributed_message


@pytest.mark.asyncio
async def test_in_process_cluster_bus_delivers_custom_messages() -> None:
    received: list[ClusterMessage] = []
    bus = InProcessClusterBus(node_id="local")

    async def handle(message: ClusterMessage) -> None:
        received.append(message)

    bus.register("seed-users", handle)

    await bus.send("seed-users", {"count": 2}, target="controller")

    assert received == [
        ClusterMessage(
            name="seed-users",
            payload={"count": 2},
            source="local",
            target="controller",
        )
    ]


def test_distributed_message_decorator_marks_handlers() -> None:
    @distributed_message("seed-users", concurrent=True)
    async def handle(_message: ClusterMessage) -> None:
        return None

    assert handle.__veriload_message__.name == "seed-users"
    assert handle.__veriload_message__.concurrent is True
