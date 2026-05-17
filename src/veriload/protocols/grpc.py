"""gRPC protocol adapter."""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from typing import Any

from veriload.metrics import EventBus, RequestFailed, RequestFinished

UnaryCallable = Callable[..., Awaitable[Any]]


class GrpcClient:
    """Records metrics around async gRPC stub calls."""

    def __init__(
        self,
        *,
        events: EventBus,
        segment: str,
        persona_id: str | None = None,
    ) -> None:
        self._events = events
        self._segment = segment
        self._persona_id = persona_id

    async def unary(
        self,
        method: str,
        call: UnaryCallable,
        request: Any,
        *,
        metadata: tuple[tuple[str, str], ...] = (),
        timeout: float | None = None,
        name: str | None = None,
    ) -> Any:
        """Execute one async unary gRPC call and record a metric event."""

        metric_name = name or f"gRPC {method}"
        started = time.perf_counter()
        try:
            response = await call(request, metadata=metadata, timeout=timeout)
        except Exception as exc:
            self._events.emit(
                RequestFailed(
                    name=metric_name,
                    method="GRPC",
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
                method="GRPC",
                status_code=0,
                latency_ms=_elapsed_ms(started),
                segment=self._segment,
                persona_id=self._persona_id,
            )
        )
        return response


def _elapsed_ms(started: float) -> float:
    return (time.perf_counter() - started) * 1000
