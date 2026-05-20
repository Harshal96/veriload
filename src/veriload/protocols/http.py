"""HTTP and GraphQL protocol adapter."""

from __future__ import annotations

import time
from typing import Any

import httpx

from veriload.cleanup import AutoCleanupManager
from veriload.metrics import EventBus, RequestFailed, RequestFinished


class HttpClient:
    """Async HTTP client that emits VeriLoad request metrics."""

    def __init__(
        self,
        *,
        base_url: str,
        events: EventBus,
        segment: str,
        persona_id: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        cleanup_manager: AutoCleanupManager | None = None,
    ) -> None:
        self._events = events
        self._segment = segment
        self._persona_id = persona_id
        self._client = httpx.AsyncClient(base_url=base_url, transport=transport)
        self._cleanup_manager = cleanup_manager

    async def get(self, url: str, *, name: str | None = None, **kwargs: Any) -> httpx.Response:
        """Send a GET request and record a metric event."""

        return await self.request("GET", url, name=name, **kwargs)

    async def post(self, url: str, *, name: str | None = None, **kwargs: Any) -> httpx.Response:
        """Send a POST request and record a metric event."""

        return await self.request("POST", url, name=name, **kwargs)

    async def request(
        self,
        method: str,
        url: str,
        *,
        name: str | None = None,
        **kwargs: Any,
    ) -> httpx.Response:
        """Send an HTTP request and record success or failure."""

        metric_name = name or f"{method.upper()} {url}"
        started = time.perf_counter()
        try:
            response = await self._client.request(method, url, **kwargs)
        except Exception as exc:
            latency_ms = _elapsed_ms(started)
            self._events.emit(
                RequestFailed(
                    name=metric_name,
                    method=method.upper(),
                    error=type(exc).__name__,
                    latency_ms=latency_ms,
                    segment=self._segment,
                    persona_id=self._persona_id,
                )
            )
            raise

        latency_ms = _elapsed_ms(started)
        if response.status_code >= 400:
            self._events.emit(
                RequestFailed(
                    name=metric_name,
                    method=method.upper(),
                    error=f"HTTP {response.status_code}",
                    latency_ms=latency_ms,
                    segment=self._segment,
                    persona_id=self._persona_id,
                )
            )
        else:
            self._events.emit(
                RequestFinished(
                    name=metric_name,
                    method=method.upper(),
                    status_code=response.status_code,
                    latency_ms=latency_ms,
                    segment=self._segment,
                    persona_id=self._persona_id,
                )
            )
            if self._cleanup_manager is not None:
                self._cleanup_manager.register_http_response(
                    method=method,
                    path=url,
                    response=response,
                    cleanup_request=self._cleanup_request,
                )
        return response

    async def _cleanup_request(self, method: str, url: str) -> httpx.Response:
        return await self._client.request(method, url)

    async def graphql(
        self,
        *,
        query: str,
        operation_name: str,
        variables: dict[str, Any] | None = None,
        endpoint: str = "/graphql",
    ) -> httpx.Response:
        """Send a GraphQL operation and record GraphQL errors as failures."""

        payload = {
            "query": query,
            "operationName": operation_name,
            "variables": variables or {},
        }
        metric_name = f"GraphQL {operation_name}"
        started = time.perf_counter()
        try:
            response = await self._client.post(endpoint, json=payload)
        except Exception as exc:
            self._events.emit(
                RequestFailed(
                    name=metric_name,
                    method="GRAPHQL",
                    error=type(exc).__name__,
                    latency_ms=_elapsed_ms(started),
                    segment=self._segment,
                    persona_id=self._persona_id,
                )
            )
            raise

        latency_ms = _elapsed_ms(started)
        graphql_errors = _has_graphql_errors(response)
        if response.status_code >= 400 or graphql_errors:
            error = "GraphQL errors" if graphql_errors else f"HTTP {response.status_code}"
            self._events.emit(
                RequestFailed(
                    name=metric_name,
                    method="GRAPHQL",
                    error=error,
                    latency_ms=latency_ms,
                    segment=self._segment,
                    persona_id=self._persona_id,
                )
            )
        else:
            self._events.emit(
                RequestFinished(
                    name=metric_name,
                    method="GRAPHQL",
                    status_code=response.status_code,
                    latency_ms=latency_ms,
                    segment=self._segment,
                    persona_id=self._persona_id,
                )
            )
        return response

    async def aclose(self) -> None:
        """Close the underlying HTTP connection pool."""

        await self._client.aclose()


def _elapsed_ms(started: float) -> float:
    return (time.perf_counter() - started) * 1000


def _has_graphql_errors(response: httpx.Response) -> bool:
    try:
        body = response.json()
    except ValueError:
        return False
    errors = body.get("errors") if isinstance(body, dict) else None
    return bool(errors)
