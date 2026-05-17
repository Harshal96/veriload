"""Kopf handler helpers for the VeriLoad operator."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from veriload.k8s_operator.resources import (
    FINALIZER,
    GROUP,
    KIND,
    PLURAL,
    VERSION,
    build_collector_job,
    build_owned_resources,
    detect_spec_drift,
    resource_names,
    result_ref,
)


def build_initial_status(*, name: str, generation: int, spec: dict[str, Any]) -> dict[str, Any]:
    """Build the initial status patch for a run."""

    expected_workers = int(spec.get("execution", {}).get("workers", 1))
    return {
        "phase": "Preparing",
        "observedGeneration": generation,
        "runID": f"{name}-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}",
        "startTime": datetime.now(UTC).isoformat(),
        "completionTime": None,
        "expectedWorkers": expected_workers,
        "readyWorkers": 0,
        "resultRef": result_ref(name, spec),
        "conditions": [
            _condition("ConfigReady", "Unknown", "Reconciling", "Config resources are being prepared."),
            _condition("ControllerReady", "Unknown", "Reconciling", "Controller job is being prepared."),
            _condition("WorkersReady", "False", "WorkersPending", "Workers have not connected yet."),
            _condition("Progressing", "True", "Reconciling", "VeriLoad run is being reconciled."),
            _condition("ArtifactsReady", "False", "CollectorPending", "Artifacts have not been collected yet."),
            _condition("Succeeded", "False", "RunPending", "Run has not completed yet."),
            _condition("Failed", "False", "RunPending", "Run has not failed."),
            _condition("SpecDrifted", "False", "SpecAccepted", "No immutable spec drift detected."),
        ],
    }


def plan_reconcile(*, name: str, namespace: str, spec: dict[str, Any], uid: str | None) -> list[dict[str, Any]]:
    """Build resources for a reconcile pass."""

    owner = {
        "apiVersion": f"{GROUP}/{VERSION}",
        "kind": KIND,
        "name": name,
        "uid": uid,
    }
    return build_owned_resources(name=name, namespace=namespace, spec=spec, owner=owner)


def build_collector_resource(*, name: str, namespace: str, spec: dict[str, Any], uid: str | None) -> dict[str, Any]:
    """Build the collector Job resource for a terminal controller run."""

    owner = {
        "apiVersion": f"{GROUP}/{VERSION}",
        "kind": KIND,
        "name": name,
        "uid": uid,
    }
    return build_collector_job(name=name, namespace=namespace, spec=spec, owner=owner)


def terminal_cleanup_resource_names(*, name: str, spec: dict[str, Any], phase: str) -> list[str]:
    """Return child resources to delete after a terminal phase."""

    cleanup_policy = spec.get("cleanupPolicy", "DeleteOnSuccess")
    if cleanup_policy == "Retain":
        return []
    if cleanup_policy == "DeleteOnSuccess" and phase != "Succeeded":
        return []
    names = resource_names(name)
    return [
        names.config,
        names.token,
        names.controller_service,
        names.controller_job,
        names.worker_job,
        names.collector_job,
    ]


def cleanup_resource_names(*, name: str, spec: dict[str, Any]) -> list[str]:
    """Return child resources to delete when the CR is deleted."""

    names = resource_names(name)
    resources = [
        names.config,
        names.token,
        names.controller_service,
        names.controller_job,
        names.worker_job,
        names.collector_job,
    ]
    if not bool(spec.get("artifacts", {}).get("retain", True)):
        resources.append(names.artifacts)
    return resources


def spec_drift_status(old: dict[str, Any], new: dict[str, Any]) -> dict[str, Any] | None:
    """Return a status patch for immutable spec drift, if any."""

    if not detect_spec_drift(old, new):
        return None
    return {
        "conditions": [
            _condition(
                "SpecDrifted",
                "True",
                "ImmutableFieldChanged",
                "Immutable fields changed after the run started; active workloads were left unchanged.",
            )
        ]
    }


def register_handlers(kopf: Any, client: Any) -> None:
    """Register Kopf handlers with lazily supplied modules."""

    @kopf.on.create(GROUP, VERSION, PLURAL)
    @kopf.on.resume(GROUP, VERSION, PLURAL)
    def reconcile_create_or_resume(
        spec: dict[str, Any],
        name: str,
        namespace: str,
        meta: dict[str, Any],
        patch: dict[str, Any],
        **_: Any,
    ) -> None:
        patch.setdefault("metadata", {}).setdefault("finalizers", []).append(FINALIZER)
        patch["status"] = build_initial_status(
            name=name,
            generation=int(meta.get("generation", 1)),
            spec=spec,
        )
        resources = plan_reconcile(name=name, namespace=namespace, spec=spec, uid=meta.get("uid"))
        _apply_resources(client, namespace, resources)

    @kopf.on.update(GROUP, VERSION, PLURAL)
    def reconcile_update(
        old: dict[str, Any],
        new: dict[str, Any],
        status: dict[str, Any],
        patch: dict[str, Any],
        **_: Any,
    ) -> None:
        phase = status.get("phase")
        if phase in {"Running", "Collecting", "Succeeded", "Failed"}:
            drift_patch = spec_drift_status(old.get("spec", {}), new.get("spec", {}))
            if drift_patch is not None:
                patch["status"] = drift_patch

    @kopf.on.delete(GROUP, VERSION, PLURAL)
    def reconcile_delete(spec: dict[str, Any], name: str, namespace: str, **_: Any) -> None:
        _delete_resources(client, namespace, cleanup_resource_names(name=name, spec=spec))


def _condition(condition_type: str, status: str, reason: str, message: str) -> dict[str, str]:
    return {
        "type": condition_type,
        "status": status,
        "reason": reason,
        "message": message,
        "lastTransitionTime": datetime.now(UTC).isoformat(),
    }


def _apply_resources(client: Any, namespace: str, resources: list[dict[str, Any]]) -> None:
    # The runtime path uses dynamic clients so CRD tests do not need Kubernetes installed.
    dynamic = client.DynamicClient(client.ApiClient())
    for resource in resources:
        api = dynamic.resources.get(api_version=resource["apiVersion"], kind=resource["kind"])
        api.patch(
            namespace=namespace,
            name=resource["metadata"]["name"],
            body=resource,
            content_type="application/apply-patch+yaml",
            field_manager="veriload-operator",
        )


def _delete_resources(client: Any, namespace: str, names: list[str]) -> None:
    dynamic = client.DynamicClient(client.ApiClient())
    for child_name in names:
        # Best-effort deletion by name across known child kinds; missing resources are ignored by Kubernetes clients.
        for api_version, kind in (
            ("v1", "ConfigMap"),
            ("v1", "Secret"),
            ("v1", "Service"),
            ("v1", "PersistentVolumeClaim"),
            ("batch/v1", "Job"),
        ):
            api = dynamic.resources.get(api_version=api_version, kind=kind)
            try:
                api.delete(namespace=namespace, name=child_name)
            except Exception:
                continue
