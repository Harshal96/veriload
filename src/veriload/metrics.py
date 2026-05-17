"""Metrics event model and in-memory aggregation."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class RequestFinished:
    """Successful request metric event."""

    name: str
    method: str
    status_code: int
    latency_ms: float
    segment: str
    persona_id: str | None = None


@dataclass(frozen=True)
class RequestFailed:
    """Failed request metric event."""

    name: str
    method: str
    error: str
    latency_ms: float
    segment: str
    persona_id: str | None = None


MetricEvent = RequestFinished | RequestFailed


class MetricsSink(Protocol):
    """Receives metric events."""

    def record(self, event: MetricEvent) -> None:
        """Record one metric event."""


class EventBus:
    """Small synchronous event bus for runtime events."""

    def __init__(self, sinks: tuple[MetricsSink, ...] | None = None) -> None:
        self._sinks = sinks or ()

    def emit(self, event: MetricEvent) -> None:
        """Emit one event to all configured sinks."""

        for sink in self._sinks:
            sink.record(event)


@dataclass(frozen=True)
class LatencySummary:
    """Latency percentile summary in milliseconds."""

    p50: float
    p90: float
    p95: float
    p99: float
    mean: float
    max: float


@dataclass(frozen=True)
class SegmentSummary:
    """Aggregated metrics for one persona segment."""

    total_requests: int
    total_failures: int
    error_rate: float
    latency_ms: LatencySummary


@dataclass(frozen=True)
class RunSummary:
    """Aggregated metrics for a run."""

    total_requests: int
    total_failures: int
    error_rate: float
    latency_ms: LatencySummary
    segments: dict[str, SegmentSummary]
    endpoints: dict[str, SegmentSummary]


class InMemoryMetricsSink:
    """Immutable-style in-memory metrics accumulator for tests and local runs."""

    def __init__(self) -> None:
        self._events: tuple[MetricEvent, ...] = ()

    def record(self, event: MetricEvent) -> None:
        """Record one event."""

        self._events = (*self._events, event)

    @property
    def events(self) -> tuple[MetricEvent, ...]:
        """Recorded metric events in emission order."""

        return self._events

    def summary(self) -> RunSummary:
        """Aggregate recorded request events."""

        return _summarize(self._events)


def _summarize(events: tuple[MetricEvent, ...]) -> RunSummary:
    total = len(events)
    failures = sum(isinstance(event, RequestFailed) for event in events)
    segments: dict[str, SegmentSummary] = {}
    for segment in sorted({event.segment for event in events}):
        segment_events = tuple(event for event in events if event.segment == segment)
        segments[segment] = _metric_group_summary(segment_events)
    endpoints: dict[str, SegmentSummary] = {}
    for endpoint in sorted({event.name for event in events}):
        endpoint_events = tuple(event for event in events if event.name == endpoint)
        endpoints[endpoint] = _metric_group_summary(endpoint_events)
    return RunSummary(
        total_requests=total,
        total_failures=failures,
        error_rate=failures / total if total else 0.0,
        latency_ms=_latency_summary([event.latency_ms for event in events]),
        segments=segments,
        endpoints=endpoints,
    )


def _metric_group_summary(events: tuple[MetricEvent, ...]) -> SegmentSummary:
    return SegmentSummary(
        total_requests=len(events),
        total_failures=sum(isinstance(event, RequestFailed) for event in events),
        error_rate=_error_rate(events),
        latency_ms=_latency_summary([event.latency_ms for event in events]),
    )


def _error_rate(events: tuple[MetricEvent, ...]) -> float:
    if not events:
        return 0.0
    return sum(isinstance(event, RequestFailed) for event in events) / len(events)


def _latency_summary(values: list[float]) -> LatencySummary:
    if not values:
        return LatencySummary(p50=0, p90=0, p95=0, p99=0, mean=0, max=0)
    ordered = sorted(values)
    return LatencySummary(
        p50=_percentile(ordered, 50),
        p90=_percentile(ordered, 90),
        p95=_percentile(ordered, 95),
        p99=_percentile(ordered, 99),
        mean=sum(ordered) / len(ordered),
        max=ordered[-1],
    )


def _percentile(ordered: list[float], percentile: int) -> float:
    if len(ordered) == 1:
        return ordered[0]
    rank = math.ceil((percentile / 100) * len(ordered)) - 1
    index = min(max(rank, 0), len(ordered) - 1)
    return ordered[index]
