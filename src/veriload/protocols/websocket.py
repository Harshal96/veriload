"""WebSocket protocol adapter."""

from __future__ import annotations

import inspect
import json
import time
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import urljoin

from veriload.metrics import EventBus, RequestFailed, RequestFinished

Connector = Callable[[str], Any | Awaitable[Any]]


class WebSocketClient:
    """Small WebSocket client wrapper that records VeriLoad metrics."""

    def __init__(
        self,
        *,
        base_url: str,
        events: EventBus,
        segment: str,
        persona_id: str | None = None,
        connector: Connector | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/") + "/"
        self._events = events
        self._segment = segment
        self._persona_id = persona_id
        self._connector = connector or _default_connector

    async def request_json(self, path: str, *, name: str | None = None, payload: Any) -> Any:
        """Open a WebSocket, send one JSON payload, receive one JSON response, and record a metric."""

        metric_name = name or f"WS {path}"
        started = time.perf_counter()
        try:
            connection = await self._connection(path)
            async with connection as socket:
                await socket.send(json.dumps(payload, sort_keys=True))
                response_text = await socket.recv()
        except Exception as exc:
            self._events.emit(
                RequestFailed(
                    name=metric_name,
                    method="WEBSOCKET",
                    error=type(exc).__name__,
                    latency_ms=_elapsed_ms(started),
                    segment=self._segment,
                    persona_id=self._persona_id,
                )
            )
            raise

        self._events.emit(
            RequestFinished(
                name=metric_name,
                method="WEBSOCKET",
                status_code=101,
                latency_ms=_elapsed_ms(started),
                segment=self._segment,
                persona_id=self._persona_id,
            )
        )
        return json.loads(response_text)

    async def _connection(self, path: str) -> Any:
        connection = self._connector(urljoin(self._base_url, path.lstrip("/")))
        if inspect.isawaitable(connection):
            return await connection
        return connection


def _default_connector(uri: str) -> Any:
    try:
        from websockets.asyncio.client import connect
    except ImportError as exc:
        raise RuntimeError(
            "WebSocket support requires the websockets package. Install it in your test environment."
        ) from exc
    return connect(uri)


def _elapsed_ms(started: float) -> float:
    return (time.perf_counter() - started) * 1000
