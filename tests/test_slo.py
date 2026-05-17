from veriload.config import EndpointSloConfig, GlobalSloConfig, SloConfig
from veriload.metrics import InMemoryMetricsSink, RequestFailed, RequestFinished
from veriload.slo import evaluate_slos


def test_global_slo_passes_when_summary_is_within_thresholds() -> None:
    sink = InMemoryMetricsSink()
    sink.record(
        RequestFinished(
            name="GET /ok",
            method="GET",
            status_code=200,
            latency_ms=20,
            segment="en_US:Retail",
        )
    )

    result = evaluate_slos(
        SloConfig(global_=GlobalSloConfig(max_p95_ms=50, max_error_rate=0.01)),
        sink.summary(),
    )

    assert result.passed
    assert result.breaches == ()


def test_global_slo_reports_latency_and_error_rate_breaches() -> None:
    sink = InMemoryMetricsSink()
    sink.record(
        RequestFailed(
            name="GET /slow",
            method="GET",
            error="HTTP 503",
            latency_ms=120,
            segment="en_US:Retail",
        )
    )

    result = evaluate_slos(
        SloConfig(global_=GlobalSloConfig(max_p95_ms=100, max_error_rate=0.01)),
        sink.summary(),
    )

    assert not result.passed
    assert [breach.metric for breach in result.breaches] == ["p95_ms", "error_rate"]
    assert result.breaches[0].scope == "global"


def test_endpoint_slo_evaluates_named_endpoint_summary() -> None:
    sink = InMemoryMetricsSink()
    sink.record(
        RequestFinished(
            name="GET /ok",
            method="GET",
            status_code=200,
            latency_ms=25,
            segment="en_US:Retail",
        )
    )
    sink.record(
        RequestFailed(
            name="POST /checkout",
            method="POST",
            error="HTTP 500",
            latency_ms=80,
            segment="en_US:Retail",
        )
    )

    result = evaluate_slos(
        SloConfig(
            endpoints=(
                EndpointSloConfig(
                    name="POST /checkout",
                    max_p95_ms=70,
                    max_error_rate=0.01,
                ),
            )
        ),
        sink.summary(),
    )

    assert not result.passed
    assert [(breach.scope, breach.metric) for breach in result.breaches] == [
        ("endpoint:POST /checkout", "p95_ms"),
        ("endpoint:POST /checkout", "error_rate"),
    ]
