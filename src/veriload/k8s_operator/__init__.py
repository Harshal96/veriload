"""Kubernetes Operator support for VeriLoad."""

from veriload.k8s_operator.collector import collect_report_summary
from veriload.k8s_operator.manifest import generate_operator_install_manifest
from veriload.k8s_operator.resources import build_owned_resources

__all__ = [
    "build_owned_resources",
    "collect_report_summary",
    "generate_operator_install_manifest",
]
