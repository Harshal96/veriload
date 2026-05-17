"""Artifact collection helpers for the VeriLoad operator."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def collect_report_summary(report_json: str | Path) -> dict[str, Any]:
    """Read a VeriLoad JSON report and return a compact CR status summary."""

    payload = json.loads(Path(report_json).read_text(encoding="utf-8"))
    return {
        "totalRequests": int(payload.get("total_requests", 0)),
        "totalFailures": int(payload.get("total_failures", 0)),
        "errorRate": float(payload.get("error_rate", 0)),
        "p95Ms": float(payload.get("latency_ms", {}).get("p95", 0)),
    }
