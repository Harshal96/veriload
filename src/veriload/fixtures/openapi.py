"""OpenAPI-backed HTTP fixture connector."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from veriload.fixtures.core import FixtureContext, FixtureRecord, ModelConnector


@dataclass(frozen=True)
class _Operation:
    method: str
    path: str
    operation: dict[str, Any]


class OpenAPIConnector(ModelConnector):
    """Create and delete resources through OpenAPI-described HTTP operations."""

    def __init__(
        self,
        *,
        spec: dict[str, Any],
        http: Any,
        resource: str,
    ) -> None:
        self._spec = spec
        self._http = http
        self._resource = resource
        self._create_operation = _find_create_operation(spec, resource)
        self._delete_operation = _find_delete_operation(spec, resource)

    @classmethod
    def from_dict(cls, spec: dict[str, Any], *, http: Any, resource: str) -> OpenAPIConnector:
        """Build a connector from an already-loaded OpenAPI document."""

        return cls(spec=spec, http=http, resource=resource)

    @classmethod
    def from_file(cls, path: str | Path, *, http: Any, resource: str) -> OpenAPIConnector:
        """Build a connector from a JSON/YAML OpenAPI document."""

        loaded = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        return cls(spec=loaded if isinstance(loaded, dict) else {}, http=http, resource=resource)

    def supports(self, model: object) -> bool:
        return _resource_name(model) == self._resource.lower()

    async def create(self, model: object, context: FixtureContext) -> FixtureRecord:
        field_names = _request_body_fields(self._create_operation.operation)
        payload = context.values_for(field_names)
        response = await self._http.request(
            self._create_operation.method,
            self._create_operation.path,
            name=f"{self._create_operation.method} {self._create_operation.path}",
            json=payload,
        )
        body = _response_json(response)
        identity = str(body.get("id") or body.get(f"{self._resource.lower()}Id") or "")
        return FixtureRecord(
            connector=self,
            target=model,
            identity=identity,
            obj=body,
            metadata={"id_field": "id"},
        )

    async def delete(self, record: FixtureRecord, context: FixtureContext) -> None:
        path = _fill_path_parameter(self._delete_operation.path, record.identity)
        await self._http.request(
            self._delete_operation.method,
            path,
            name=f"{self._delete_operation.method} {self._delete_operation.path}",
        )


def _find_create_operation(spec: dict[str, Any], resource: str) -> _Operation:
    resource_key = resource.lower()
    for path, operations in _path_operations(spec):
        operation = operations.get("post")
        if isinstance(operation, dict) and _operation_matches(resource_key, path, operation):
            return _Operation(method="POST", path=path, operation=operation)
    raise ValueError(f"OpenAPI spec has no POST create operation for {resource!r}")


def _find_delete_operation(spec: dict[str, Any], resource: str) -> _Operation:
    resource_key = resource.lower()
    for path, operations in _path_operations(spec):
        operation = operations.get("delete")
        if isinstance(operation, dict) and _operation_matches(resource_key, path, operation):
            return _Operation(method="DELETE", path=path, operation=operation)
    raise ValueError(f"OpenAPI spec has no DELETE cleanup operation for {resource!r}")


def _path_operations(spec: dict[str, Any]) -> tuple[tuple[str, dict[str, Any]], ...]:
    paths = spec.get("paths")
    if not isinstance(paths, dict):
        return ()
    return tuple(
        (str(path), operations)
        for path, operations in paths.items()
        if isinstance(operations, dict)
    )


def _operation_matches(resource_key: str, path: str, operation: dict[str, Any]) -> bool:
    operation_id = str(operation.get("operationId", "")).lower()
    normalized_path = path.lower().replace("_", "").replace("-", "")
    singular_path = normalized_path.rstrip("s")
    return (
        resource_key in operation_id
        or f"/{resource_key}" in normalized_path
        or f"/{resource_key}" in singular_path
    )


def _request_body_fields(operation: dict[str, Any]) -> tuple[str, ...]:
    schema = _json_request_schema(operation)
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return ()
    required = schema.get("required")
    if isinstance(required, list) and required:
        ordered = [str(item) for item in required if str(item) in properties]
        ordered.extend(str(name) for name in properties if str(name) not in ordered)
        return tuple(ordered)
    return tuple(str(name) for name in properties)


def _json_request_schema(operation: dict[str, Any]) -> dict[str, Any]:
    request_body = operation.get("requestBody")
    if not isinstance(request_body, dict):
        return {}
    content = request_body.get("content")
    if not isinstance(content, dict):
        return {}
    media = content.get("application/json")
    if not isinstance(media, dict):
        return {}
    schema = media.get("schema")
    if not isinstance(schema, dict):
        return {}
    return schema


def _response_json(response: object) -> dict[str, Any]:
    if isinstance(response, dict):
        return response
    json_method = getattr(response, "json", None)
    if callable(json_method):
        body = json_method()
        return body if isinstance(body, dict) else {}
    return {}


def _fill_path_parameter(path: str, identity: str) -> str:
    if "{" not in path:
        return path
    prefix, remainder = path.split("{", 1)
    _, suffix = remainder.split("}", 1)
    return f"{prefix}{identity}{suffix}"


def _resource_name(model: object) -> str:
    if isinstance(model, str):
        return model.lower()
    return getattr(model, "__name__", str(model)).lower()
