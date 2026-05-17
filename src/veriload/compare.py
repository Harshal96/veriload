"""Baseline comparison for VeriLoad JSON reports."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Regression:
    """One detected performance regression."""

    scope: str
    metric: str
    baseline: float
    current: float
    delta: float
    threshold: float


@dataclass(frozen=True)
class ComparisonResult:
    """Result of comparing current run output to a baseline."""

    regressions: tuple[Regression, ...]
    improvements: dict[str, dict[str, float]] = field(default_factory=dict)
    added_endpoints: tuple[str, ...] = ()
    removed_endpoints: tuple[str, ...] = ()

    @property
    def passed(self) -> bool:
        return not self.regressions

    @property
    def regression_count(self) -> int:
        return len(self.regressions)

    @property
    def affected_scopes(self) -> tuple[str, ...]:
        return tuple(sorted({regression.scope for regression in self.regressions}))

    @property
    def worst_regression(self) -> Regression | None:
        if not self.regressions:
            return None
        return max(
            self.regressions,
            key=lambda item: item.delta / item.threshold if item.threshold else item.delta,
        )


def load_run_report(path: str | Path) -> dict[str, Any]:
    """Load a JSON run report."""

    return json.loads(Path(path).read_text(encoding="utf-8"))


def compare_run_reports(
    baseline: dict[str, Any],
    current: dict[str, Any],
    *,
    max_p95_regression_ms: float,
    max_error_rate_regression: float,
) -> ComparisonResult:
    """Compare current metrics against a baseline report."""

    regressions: list[Regression] = []
    improvements: dict[str, dict[str, float]] = {}
    regressions.extend(
        _compare_scope(
            "global",
            baseline,
            current,
            max_p95_regression_ms=max_p95_regression_ms,
            max_error_rate_regression=max_error_rate_regression,
        )
    )
    _store_improvements(improvements, "global", baseline, current)
    baseline_endpoints = baseline.get("endpoints") or {}
    current_endpoints = current.get("endpoints") or {}
    for endpoint in sorted(set(baseline_endpoints) & set(current_endpoints)):
        regressions.extend(
            _compare_scope(
                f"endpoint:{endpoint}",
                baseline_endpoints[endpoint],
                current_endpoints[endpoint],
                max_p95_regression_ms=max_p95_regression_ms,
                max_error_rate_regression=max_error_rate_regression,
            )
        )
        _store_improvements(
            improvements,
            f"endpoint:{endpoint}",
            baseline_endpoints[endpoint],
            current_endpoints[endpoint],
        )
    return ComparisonResult(
        regressions=tuple(regressions),
        improvements=improvements,
        added_endpoints=tuple(sorted(set(current_endpoints) - set(baseline_endpoints))),
        removed_endpoints=tuple(sorted(set(baseline_endpoints) - set(current_endpoints))),
    )


def _compare_scope(
    scope: str,
    baseline: dict[str, Any],
    current: dict[str, Any],
    *,
    max_p95_regression_ms: float,
    max_error_rate_regression: float,
) -> list[Regression]:
    regressions: list[Regression] = []
    baseline_p95 = _p95(baseline)
    current_p95 = _p95(current)
    p95_delta = current_p95 - baseline_p95
    if p95_delta > max_p95_regression_ms:
        regressions.append(
            Regression(
                scope=scope,
                metric="p95_ms",
                baseline=baseline_p95,
                current=current_p95,
                delta=p95_delta,
                threshold=max_p95_regression_ms,
            )
        )

    baseline_error_rate = float(baseline.get("error_rate", 0))
    current_error_rate = float(current.get("error_rate", 0))
    error_delta = current_error_rate - baseline_error_rate
    if error_delta > max_error_rate_regression:
        regressions.append(
            Regression(
                scope=scope,
                metric="error_rate",
                baseline=baseline_error_rate,
                current=current_error_rate,
                delta=error_delta,
                threshold=max_error_rate_regression,
            )
        )
    return regressions


def _p95(report: dict[str, Any]) -> float:
    latency = report.get("latency_ms") or {}
    return float(latency.get("p95", 0))


def _store_improvements(
    improvements: dict[str, dict[str, float]],
    scope: str,
    baseline: dict[str, Any],
    current: dict[str, Any],
) -> None:
    p95_delta = _p95(current) - _p95(baseline)
    error_delta = float(current.get("error_rate", 0)) - float(baseline.get("error_rate", 0))
    scope_improvements = {
        metric: delta
        for metric, delta in (("p95_ms", p95_delta), ("error_rate", error_delta))
        if delta < 0
    }
    if scope_improvements:
        improvements[scope] = scope_improvements
