"""Report writers for VeriLoad runs."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from xml.etree import ElementTree

from veriload.cleanup import CleanupSummary
from veriload.distributed import WorkerRunResult
from veriload.metrics import MetricEvent, RunSummary
from veriload.slo import SloResult


def write_json_report(
    path: str | Path,
    summary: RunSummary,
    slo_result: SloResult,
    *,
    cleanup: CleanupSummary | None = None,
    workers: tuple[WorkerRunResult, ...] = (),
) -> None:
    """Write a JSON run report."""

    report_path = Path(path)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    payload = asdict(summary)
    payload["slo"] = {
        "passed": slo_result.passed,
        "breaches": [asdict(breach) for breach in slo_result.breaches],
    }
    if cleanup is not None:
        payload["cleanup"] = asdict(cleanup)
    payload["workers"] = [asdict(worker) for worker in workers]
    report_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def write_junit_report(path: str | Path, slo_result: SloResult) -> None:
    """Write a JUnit XML report representing SLO gate status."""

    report_path = Path(path)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    suite = ElementTree.Element(
        "testsuite",
        {
            "name": "veriload",
            "tests": "1",
            "failures": str(len(slo_result.breaches)),
        },
    )
    case = ElementTree.SubElement(
        suite,
        "testcase",
        {
            "classname": "veriload.slo",
            "name": "slo-gates",
        },
    )
    if not slo_result.passed:
        message = "; ".join(breach.message for breach in slo_result.breaches)
        failure = ElementTree.SubElement(case, "failure", {"message": message})
        failure.text = message
    report_path.write_text(
        ElementTree.tostring(suite, encoding="unicode"),
        encoding="utf-8",
    )


def write_trace_report(path: str | Path, events: tuple[MetricEvent, ...]) -> None:
    """Write raw request metric events as JSONL."""

    report_path = Path(path)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    rows = [json.dumps(asdict(event), sort_keys=True) for event in events]
    report_path.write_text("\n".join(rows) + ("\n" if rows else ""), encoding="utf-8")
