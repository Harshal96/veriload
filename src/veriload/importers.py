"""Scenario skeleton generators for common API description formats."""

from __future__ import annotations

import re
import shlex
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml


def generate_scenario_from_curl(command: str, *, class_name: str = "ImportedUser") -> str:
    """Generate a scenario from a cURL command."""

    tokens = shlex.split(command)
    if tokens and tokens[0] == "curl":
        tokens = tokens[1:]
    method = "GET"
    body: str | None = None
    url = ""
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token in {"-X", "--request"} and index + 1 < len(tokens):
            method = tokens[index + 1].upper()
            index += 2
            continue
        if token in {"-d", "--data", "--data-raw", "--data-binary"} and index + 1 < len(tokens):
            body = tokens[index + 1]
            method = "POST" if method == "GET" else method
            index += 2
            continue
        if token.startswith("http://") or token.startswith("https://") or token.startswith("/"):
            url = token
        index += 1
    return _scenario_source(
        class_name=class_name,
        requests=(
            RequestSpec(
                method=method,
                path=_path_from_url(url),
                operation_name=f"{method} {_path_from_url(url)}",
                body=body,
            ),
        ),
    )


def generate_scenario_from_openapi(path: str | Path, *, class_name: str = "ImportedUser") -> str:
    """Generate a scenario from an OpenAPI JSON/YAML file."""

    spec = _load_structured_file(path)
    requests: list[RequestSpec] = []
    for route, operations in (spec.get("paths") or {}).items():
        if not isinstance(operations, dict):
            continue
        for method, operation in operations.items():
            if method.lower() not in _HTTP_METHODS:
                continue
            operation_id = operation.get("operationId") if isinstance(operation, dict) else None
            requests.append(
                RequestSpec(
                    method=method.upper(),
                    path=_normalize_templated_path(route),
                    operation_name=operation_id or f"{method.upper()} {route}",
                )
            )
    return _scenario_source(class_name=class_name, requests=tuple(requests))


def generate_scenario_from_har(path: str | Path, *, class_name: str = "ImportedUser") -> str:
    """Generate a scenario from a HAR file."""

    har = _load_structured_file(path)
    requests = []
    entries = ((har.get("log") or {}).get("entries") or [])
    for entry in entries:
        request = entry.get("request") or {}
        method = (request.get("method") or "GET").upper()
        url = request.get("url") or "/"
        post_data = request.get("postData") or {}
        requests.append(
            RequestSpec(
                method=method,
                path=_path_from_url(url),
                operation_name=f"{method} {_path_from_url(url)}",
                body=post_data.get("text"),
            )
        )
    return _scenario_source(class_name=class_name, requests=tuple(requests))


def generate_scenario_from_postman(path: str | Path, *, class_name: str = "ImportedUser") -> str:
    """Generate a scenario from a Postman collection."""

    collection = _load_structured_file(path)
    requests = [
        RequestSpec(
            method=(item["request"].get("method") or "GET").upper(),
            path=_postman_url_path(item["request"].get("url")),
            operation_name=item.get("name") or "Postman request",
        )
        for item in _walk_postman_items(collection.get("item") or [])
        if isinstance(item.get("request"), dict)
    ]
    return _scenario_source(class_name=class_name, requests=tuple(requests))


class RequestSpec:
    """Internal generated-request description."""

    def __init__(
        self,
        *,
        method: str,
        path: str,
        operation_name: str,
        body: str | None = None,
    ) -> None:
        self.method = method.upper()
        self.path = path or "/"
        self.operation_name = operation_name
        self.body = body


_HTTP_METHODS = {"get", "post", "put", "patch", "delete", "head", "options"}


def _scenario_source(*, class_name: str, requests: tuple[RequestSpec, ...]) -> str:
    task_methods = "\n\n".join(_task_source(index, request) for index, request in enumerate(requests))
    if not task_methods:
        task_methods = (
            "    @task(weight=1)\n"
            "    async def imported_placeholder(self) -> None:\n"
            "        # TODO: Add requests from your source artifact.\n"
            "        self.stop()\n"
        )
    return (
        "from veriload import VeriUser, task\n\n\n"
        f"class {class_name}(VeriUser):\n"
        "    \"\"\"Generated starter scenario. Review auth, payloads, and IDs before load tests.\"\"\"\n\n"
        f"{task_methods}\n"
    )


def _task_source(index: int, request: RequestSpec) -> str:
    method_name = _method_name(request.operation_name, fallback=f"request_{index + 1}")
    body_line = ""
    if request.body:
        body_line = f"\n            content={request.body!r},"
    return (
        "    @task(weight=1)\n"
        f"    async def {method_name}(self) -> None:\n"
        "        # TODO: Replace hard-coded IDs/payloads with self.payload(...) where appropriate.\n"
        f"        await self.http.request(\"{request.method}\", \"{request.path}\","
        f"\n            name=\"{request.method} {request.path}\",{body_line}\n"
        "        )\n"
    )


def _load_structured_file(path: str | Path) -> dict[str, Any]:
    text = Path(path).read_text(encoding="utf-8")
    loaded = yaml.safe_load(text)
    return loaded if isinstance(loaded, dict) else {}


def _path_from_url(url: str) -> str:
    if not url:
        return "/"
    if url.startswith("/"):
        return url
    parsed = urlparse(url)
    path = parsed.path or "/"
    return f"{path}?{parsed.query}" if parsed.query else path


def _normalize_templated_path(path: str) -> str:
    return re.sub(r"\{[^}]+\}", "example", path)


def _method_name(name: str, *, fallback: str) -> str:
    candidate = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", name).lower()
    candidate = re.sub(r"[^a-z0-9_]+", "_", candidate).strip("_")
    candidate = re.sub(r"_+", "_", candidate)
    if not candidate or candidate[0].isdigit():
        return fallback
    return candidate


def _walk_postman_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    flattened: list[dict[str, Any]] = []
    for item in items:
        if "item" in item:
            flattened.extend(_walk_postman_items(item.get("item") or []))
        else:
            flattened.append(item)
    return flattened


def _postman_url_path(value: Any) -> str:
    if isinstance(value, str):
        return _path_from_url(value)
    if isinstance(value, dict):
        raw = value.get("raw")
        if isinstance(raw, str):
            return _path_from_url(raw)
        parts = value.get("path")
        if isinstance(parts, list):
            return "/" + "/".join(str(part) for part in parts)
    return "/"
