from pathlib import Path

import httpx
import pytest

from examples.auth_object_lifecycle.scenario import AuthenticatedObjectUser
from veriload.config import load_config
from veriload.metrics import EventBus, InMemoryMetricsSink
from veriload.protocols import HttpClient
from veriload.scenarios import load_user_class
from veriload.users import VeriUser


def test_auth_object_lifecycle_example_config_and_scenario_load() -> None:
    example_dir = Path("examples/auth_object_lifecycle")
    config = load_config(example_dir / "veriload.yaml")
    user_class = load_user_class(example_dir / config.scenario.path, config.scenario.user_class)

    assert config.run.base_url == "https://api.example.test"
    assert config.safety.allowed_hosts == ("api.example.test",)
    assert config.safety.max_users == 10
    assert issubclass(user_class, VeriUser)

    task_names = [task.name for task in user_class.discover_tasks()]
    flow_names = [flow.name for flow in user_class.discover_flows()]

    assert task_names == ["create_object_for_later_cleanup", "read_created_object"]
    assert "register synthetic user" in flow_names
    assert "login synthetic user" in flow_names
    assert "delete created objects later" in flow_names
    assert "delete synthetic user" in flow_names


@pytest.mark.asyncio
async def test_auth_object_lifecycle_example_authenticates_and_cleans_up(
    sample_persona,
) -> None:
    calls: list[tuple[str, str]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        body = _json_request(request)

        if request.method == "POST" and request.url.path == "/auth/register":
            assert body["email"] == "ada@example.invalid"
            assert body["password"] == "VeriLoad-p1-0!"
            return httpx.Response(201, json={"user": {"id": "user-1"}})

        if request.method == "POST" and request.url.path == "/auth/login":
            assert body == {
                "email": "ada@example.invalid",
                "password": "VeriLoad-p1-0!",
            }
            return httpx.Response(200, json={"access_token": "token-1"})

        if request.method == "POST" and request.url.path == "/objects":
            assert request.headers["authorization"] == "Bearer token-1"
            assert body["owner_id"] == "user-1"
            assert body["delete_after"]
            return httpx.Response(201, json={"object": {"id": "object-1"}})

        if request.method == "GET" and request.url.path == "/objects/object-1":
            assert request.headers["authorization"] == "Bearer token-1"
            return httpx.Response(200, json={"id": "object-1"})

        if request.method == "DELETE" and request.url.path == "/objects/object-1":
            assert request.headers["authorization"] == "Bearer token-1"
            return httpx.Response(204)

        if request.method == "DELETE" and request.url.path == "/users/user-1":
            assert request.headers["authorization"] == "Bearer token-1"
            return httpx.Response(204)

        return httpx.Response(404, json={"error": "not found"})

    sink = InMemoryMetricsSink()
    client = HttpClient(
        base_url="https://api.example.test",
        events=EventBus((sink,)),
        segment="en_US:Computing",
        persona_id=sample_persona.persona_id,
        transport=httpx.MockTransport(handler),
    )
    user = AuthenticatedObjectUser(
        persona=sample_persona,
        user_index=0,
        run_seed=42,
        http=client,
    )

    await user.on_start()
    await user.create_object_for_later_cleanup()
    await user.read_created_object()
    await user.on_stop()
    await client.aclose()

    assert calls == [
        ("POST", "/auth/register"),
        ("POST", "/auth/login"),
        ("POST", "/objects"),
        ("GET", "/objects/object-1"),
        ("DELETE", "/objects/object-1"),
        ("DELETE", "/users/user-1"),
    ]
    assert user.state["object_ids"] == ()
    assert user.state["last_object_id"] is None
    assert user.state["user_id"] is None
    assert user.state["access_token"] is None
    assert sink.summary().total_failures == 0


def _json_request(request: httpx.Request) -> dict:
    if not request.content:
        return {}
    response = httpx.Response(200, content=request.content)
    body = response.json()
    assert isinstance(body, dict)
    return body
