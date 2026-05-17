import json
from pathlib import Path

from veriload.importers import (
    generate_scenario_from_curl,
    generate_scenario_from_har,
    generate_scenario_from_openapi,
    generate_scenario_from_postman,
)


def test_generate_scenario_from_curl_creates_request_task() -> None:
    source = generate_scenario_from_curl(
        "curl -X POST https://api.example.test/users -d '{\"name\":\"Ada\"}'",
        class_name="ImportedUser",
    )

    assert "class ImportedUser(VeriUser)" in source
    assert 'await self.http.request("POST", "/users"' in source
    assert "TODO" in source


def test_generate_scenario_from_openapi_creates_task_per_operation(tmp_path: Path) -> None:
    spec_path = tmp_path / "openapi.json"
    spec_path.write_text(
        json.dumps(
            {
                "openapi": "3.1.0",
                "paths": {
                    "/users": {
                        "get": {"operationId": "listUsers"},
                        "post": {"operationId": "createUser"},
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    source = generate_scenario_from_openapi(spec_path, class_name="OpenApiUser")

    assert "class OpenApiUser(VeriUser)" in source
    assert "async def list_users" in source
    assert 'await self.http.request("GET", "/users"' in source
    assert 'await self.http.request("POST", "/users"' in source


def test_generate_scenario_from_har_preserves_recorded_request_order(tmp_path: Path) -> None:
    har_path = tmp_path / "flow.har"
    har_path.write_text(
        json.dumps(
            {
                "log": {
                    "entries": [
                        {"request": {"method": "GET", "url": "https://api.example.test/a"}},
                        {"request": {"method": "POST", "url": "https://api.example.test/b"}},
                    ]
                }
            }
        ),
        encoding="utf-8",
    )

    source = generate_scenario_from_har(har_path, class_name="HarUser")

    assert source.index('"/a"') < source.index('"/b"')
    assert "class HarUser(VeriUser)" in source


def test_generate_scenario_from_postman_reads_nested_items(tmp_path: Path) -> None:
    postman_path = tmp_path / "collection.json"
    postman_path.write_text(
        json.dumps(
            {
                "item": [
                    {
                        "name": "Users",
                        "item": [
                            {
                                "name": "Create",
                                "request": {
                                    "method": "POST",
                                    "url": {"raw": "https://api.example.test/users"},
                                },
                            }
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    source = generate_scenario_from_postman(postman_path, class_name="PostmanUser")

    assert "class PostmanUser(VeriUser)" in source
    assert 'await self.http.request("POST", "/users"' in source
