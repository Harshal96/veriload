from veriload.metrics import InMemoryMetricsSink, RequestFailed, RequestFinished


def test_metrics_summary_includes_latency_percentiles_and_segments() -> None:
    sink = InMemoryMetricsSink()
    for latency in [10, 20, 30, 40, 50]:
        sink.record(
            RequestFinished(
                name="GET /items",
                method="GET",
                status_code=200,
                latency_ms=latency,
                segment="en_US:Retail",
            )
        )
    sink.record(
        RequestFailed(
            name="POST /items",
            method="POST",
            error="boom",
            latency_ms=60,
            segment="hi_IN:Retail",
        )
    )

    summary = sink.summary()

    assert summary.total_requests == 6
    assert summary.total_failures == 1
    assert summary.latency_ms.p95 == 60
    assert summary.segments["en_US:Retail"].latency_ms.p50 == 30
    assert summary.segments["hi_IN:Retail"].error_rate == 1.0
