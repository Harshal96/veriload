"""SLO gate evaluation."""

from __future__ import annotations

from dataclasses import dataclass

from veriload.config import EndpointSloConfig, GlobalSloConfig, SloConfig
from veriload.metrics import RunSummary, SegmentSummary


@dataclass(frozen=True)
class SloBreach:
    """One threshold breach."""

    scope: str
    metric: str
    observed: float
    threshold: float

    @property
    def message(self) -> str:
        return (
            f"{self.scope} {self.metric} observed {self.observed:.4g} "
            f"above threshold {self.threshold:.4g}"
        )


@dataclass(frozen=True)
class SloResult:
    """Result of evaluating SLO thresholds."""

    breaches: tuple[SloBreach, ...] = ()

    @property
    def passed(self) -> bool:
        return not self.breaches


def evaluate_slos(config: SloConfig | None, summary: RunSummary) -> SloResult:
    """Evaluate SLO thresholds against a run summary."""

    if config is None:
        return SloResult()

    breaches: list[SloBreach] = []
    if config.global_ is not None:
        breaches.extend(_evaluate_thresholds("global", config.global_, summary))

    for endpoint in config.endpoints:
        endpoint_summary = summary.endpoints.get(endpoint.name)
        if endpoint_summary is None:
            continue
        breaches.extend(_evaluate_thresholds(f"endpoint:{endpoint.name}", endpoint, endpoint_summary))

    return SloResult(breaches=tuple(breaches))


def _evaluate_thresholds(
    scope: str,
    config: GlobalSloConfig | EndpointSloConfig,
    summary: RunSummary | SegmentSummary,
) -> list[SloBreach]:
    breaches: list[SloBreach] = []
    if config.max_p95_ms is not None and summary.latency_ms.p95 > config.max_p95_ms:
        breaches.append(
            SloBreach(
                scope=scope,
                metric="p95_ms",
                observed=summary.latency_ms.p95,
                threshold=config.max_p95_ms,
            )
        )
    if config.max_error_rate is not None and summary.error_rate > config.max_error_rate:
        breaches.append(
            SloBreach(
                scope=scope,
                metric="error_rate",
                observed=summary.error_rate,
                threshold=config.max_error_rate,
            )
        )
    return breaches
