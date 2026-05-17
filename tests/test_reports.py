import json
from pathlib import Path
from xml.etree import ElementTree

from veriload.metrics import InMemoryMetricsSink, RequestFailed, RequestFinished
from veriload.reporting import write_json_report, write_junit_report, write_trace_report
from veriload.slo import SloBreach, SloResult


def test_write_json_report_includes_summary_endpoints_segments_and_slo(tmp_path: Path) -> None:
    summary = _summary()
    result = SloResult(
        breaches=(
            SloBreach(
                scope="global",
                metric="p95_ms",
                observed=150,
                threshold=100,
            ),
        )
    )

    path = tmp_path / "summary.json"
    write_json_report(path, summary, result)

    report = json.loads(path.read_text(encoding="utf-8"))
    assert report["total_requests"] == 2
    assert report["endpoints"]["GET /ok"]["total_requests"] == 1
    assert report["segments"]["en_US:Retail"]["total_failures"] == 1
    assert report["slo"]["passed"] is False
    assert report["slo"]["breaches"][0]["metric"] == "p95_ms"


def test_write_junit_report_marks_breaches_as_failures(tmp_path: Path) -> None:
    result = SloResult(
        breaches=(
            SloBreach(
                scope="endpoint:POST /checkout",
                metric="error_rate",
                observed=1.0,
                threshold=0.01,
            ),
        )
    )

    path = tmp_path / "junit.xml"
    write_junit_report(path, result)

    root = ElementTree.fromstring(path.read_text(encoding="utf-8"))
    assert root.attrib["name"] == "veriload"
    assert root.attrib["failures"] == "1"
    failure = root.find("./testcase/failure")
    assert failure is not None
    assert "endpoint:POST /checkout" in failure.attrib["message"]


def test_write_trace_report_outputs_request_events_as_jsonl(tmp_path: Path) -> None:
    path = tmp_path / "trace.jsonl"

    write_trace_report(
        path,
        (
            RequestFinished(
                name="GET /ok",
                method="GET",
                status_code=200,
                latency_ms=10,
                segment="en_US:Retail",
                persona_id="p0",
            ),
        ),
    )

    row = json.loads(path.read_text(encoding="utf-8").strip())
    assert row["persona_id"] == "p0"
    assert row["name"] == "GET /ok"


def _summary():
    sink = InMemoryMetricsSink()
    sink.record(
        RequestFinished(
            name="GET /ok",
            method="GET",
            status_code=200,
            latency_ms=30,
            segment="en_US:Retail",
        )
    )
    sink.record(
        RequestFailed(
            name="POST /checkout",
            method="POST",
            error="HTTP 500",
            latency_ms=150,
            segment="en_US:Retail",
        )
    )
    return sink.summary()
