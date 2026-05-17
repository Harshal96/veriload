from veriload.compare import compare_run_reports


def test_compare_run_reports_passes_when_current_is_within_thresholds() -> None:
    result = compare_run_reports(
        _report(p95=100, error_rate=0.01),
        _report(p95=105, error_rate=0.011),
        max_p95_regression_ms=10,
        max_error_rate_regression=0.01,
    )

    assert result.passed
    assert result.regressions == ()


def test_compare_run_reports_detects_global_and_endpoint_regressions() -> None:
    baseline = _report(p95=100, error_rate=0.01, checkout_p95=200, checkout_error_rate=0.02)
    current = _report(p95=130, error_rate=0.05, checkout_p95=260, checkout_error_rate=0.08)

    result = compare_run_reports(
        baseline,
        current,
        max_p95_regression_ms=10,
        max_error_rate_regression=0.01,
    )

    assert not result.passed
    assert [(item.scope, item.metric) for item in result.regressions] == [
        ("global", "p95_ms"),
        ("global", "error_rate"),
        ("endpoint:POST /checkout", "p95_ms"),
        ("endpoint:POST /checkout", "error_rate"),
    ]
    assert result.regression_count == 4
    assert result.affected_scopes == ("endpoint:POST /checkout", "global")
    assert result.worst_regression is not None
    assert result.worst_regression.scope == "endpoint:POST /checkout"


def test_compare_run_reports_summarizes_improvements_and_missing_endpoints() -> None:
    baseline = _report(p95=100, error_rate=0.02)
    baseline["endpoints"]["GET /removed"] = {
        "latency_ms": {"p95": 80},
        "error_rate": 0,
    }
    current = _report(p95=90, error_rate=0.01)
    current["endpoints"]["GET /new"] = {
        "latency_ms": {"p95": 70},
        "error_rate": 0,
    }

    result = compare_run_reports(
        baseline,
        current,
        max_p95_regression_ms=10,
        max_error_rate_regression=0.01,
    )

    assert result.passed
    assert result.improvements["global"]["p95_ms"] == -10
    assert result.added_endpoints == ("GET /new",)
    assert result.removed_endpoints == ("GET /removed",)


def _report(
    *,
    p95: float,
    error_rate: float,
    checkout_p95: float = 200,
    checkout_error_rate: float = 0.01,
) -> dict:
    return {
        "latency_ms": {"p95": p95},
        "error_rate": error_rate,
        "endpoints": {
            "POST /checkout": {
                "latency_ms": {"p95": checkout_p95},
                "error_rate": checkout_error_rate,
            }
        },
    }
