"""Pure Kubernetes resource builders for the VeriLoad operator."""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from typing import Any

GROUP = "veriload.io"
VERSION = "v1alpha1"
KIND = "VeriLoadRun"
PLURAL = "veriloadruns"
FINALIZER = "veriload.io/finalizer"
APP_LABEL = "veriload-operator"
REPORT_MOUNT = "/reports"
CONFIG_MOUNT = "/etc/veriload"
WORKSPACE_MOUNT = "/workspace"


@dataclass(frozen=True)
class ResourceNames:
    """Deterministic child-resource names for one VeriLoadRun."""

    config: str
    token: str
    controller_service: str
    controller_job: str
    worker_job: str
    artifacts: str
    collector_job: str


def resource_names(name: str) -> ResourceNames:
    """Return all child-resource names for a run."""

    return ResourceNames(
        config=f"{name}-config",
        token=f"{name}-token",
        controller_service=f"{name}-controller",
        controller_job=f"{name}-controller",
        worker_job=f"{name}-worker",
        artifacts=f"{name}-artifacts",
        collector_job=f"{name}-collector",
    )


def build_owned_resources(
    *,
    name: str,
    namespace: str,
    spec: dict[str, Any],
    owner: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Build the desired v1 child resources for a VeriLoadRun."""

    resources: list[dict[str, Any]] = []
    if _config_ref(spec) is None:
        resources.append(build_config_map(name=name, namespace=namespace, spec=spec, owner=owner))
    if _token_ref(spec) is None:
        resources.append(build_token_secret(name=name, namespace=namespace, owner=owner))
    resources.append(build_controller_service(name=name, namespace=namespace, spec=spec, owner=owner))
    if _artifact_pvc_enabled(spec):
        resources.append(build_artifact_pvc(name=name, namespace=namespace, spec=spec, owner=owner))
    resources.append(build_controller_job(name=name, namespace=namespace, spec=spec, owner=owner))
    resources.append(build_worker_job(name=name, namespace=namespace, spec=spec, owner=owner))
    return resources


def build_config_map(
    *,
    name: str,
    namespace: str,
    spec: dict[str, Any],
    owner: dict[str, Any] | None,
) -> dict[str, Any]:
    """Build the inline run-config ConfigMap."""

    names = resource_names(name)
    return {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": _metadata(names.config, namespace, name, owner),
        "data": {"veriload.yaml": str(spec.get("config", {}).get("inline", ""))},
    }


def build_token_secret(*, name: str, namespace: str, owner: dict[str, Any] | None) -> dict[str, Any]:
    """Build the cluster-token Secret used by controller and workers."""

    names = resource_names(name)
    return {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": _metadata(names.token, namespace, name, owner),
        "type": "Opaque",
        "stringData": {"token": secrets.token_urlsafe(32)},
    }


def build_controller_service(
    *,
    name: str,
    namespace: str,
    spec: dict[str, Any],
    owner: dict[str, Any] | None,
) -> dict[str, Any]:
    """Build the controller Service used by workers."""

    names = resource_names(name)
    port = _controller_port(spec)
    return {
        "apiVersion": "v1",
        "kind": "Service",
        "metadata": _metadata(names.controller_service, namespace, name, owner),
        "spec": {
            "selector": _selector(name, "controller"),
            "ports": [{"name": "control", "port": port, "targetPort": port}],
        },
    }


def build_artifact_pvc(
    *,
    name: str,
    namespace: str,
    spec: dict[str, Any],
    owner: dict[str, Any] | None,
) -> dict[str, Any]:
    """Build the artifact PVC for reports."""

    names = resource_names(name)
    storage = spec.get("artifacts", {}).get("storage", {})
    return {
        "apiVersion": "v1",
        "kind": "PersistentVolumeClaim",
        "metadata": _metadata(names.artifacts, namespace, name, owner),
        "spec": {
            "accessModes": ["ReadWriteOnce"],
            "resources": {"requests": {"storage": str(storage.get("size", "1Gi"))}},
        },
    }


def build_controller_job(
    *,
    name: str,
    namespace: str,
    spec: dict[str, Any],
    owner: dict[str, Any] | None,
) -> dict[str, Any]:
    """Build the distributed controller Job."""

    names = resource_names(name)
    execution = spec.get("execution", {})
    args = [
        "distributed",
        "controller",
        "--config",
        f"{CONFIG_MOUNT}/veriload.yaml",
        "--bind-host",
        "0.0.0.0",
        "--bind-port",
        str(_controller_port(spec)),
        "--expect-workers",
        str(_workers(spec)),
        "--cluster-token-env",
        "VERILOAD_CLUSTER_TOKEN",
        "--ready-timeout-seconds",
        str(execution.get("readyTimeoutSeconds", 60)),
    ]
    container = _container(
        name="controller",
        image=_image(spec),
        args=args,
        env=(
            _cluster_token_env(spec, name)
            + _report_env(name, enabled=_artifacts_enabled(spec))
            + list(spec.get("controller", {}).get("env", ()))
        ),
        role_spec=spec.get("controller", {}),
        mounts=_volume_mounts(name, spec, include_artifacts=_artifacts_enabled(spec)),
    )
    volumes = _volumes(name, spec, include_artifacts=_artifacts_enabled(spec))
    return _job(
        name=names.controller_job,
        namespace=namespace,
        run_name=name,
        role="controller",
        owner=owner,
        parallelism=1,
        container=container,
        pod_spec=_pod_spec(spec.get("controller", {}), volumes=volumes),
    )


def build_worker_job(
    *,
    name: str,
    namespace: str,
    spec: dict[str, Any],
    owner: dict[str, Any] | None,
) -> dict[str, Any]:
    """Build the distributed worker Job."""

    names = resource_names(name)
    args = [
        "distributed",
        "worker",
        "--controller-host",
        names.controller_service,
        "--controller-port",
        str(_controller_port(spec)),
        "--cluster-token-env",
        "VERILOAD_CLUSTER_TOKEN",
    ]
    container = _container(
        name="worker",
        image=_image(spec),
        args=args,
        env=_cluster_token_env(spec, name) + list(spec.get("worker", {}).get("env", ())),
        role_spec=spec.get("worker", {}),
        mounts=_volume_mounts(name, spec, include_artifacts=False),
    )
    return _job(
        name=names.worker_job,
        namespace=namespace,
        run_name=name,
        role="worker",
        owner=owner,
        parallelism=_workers(spec),
        container=container,
        pod_spec=_pod_spec(spec.get("worker", {}), volumes=_volumes(name, spec, include_artifacts=False)),
    )


def build_collector_job(
    *,
    name: str,
    namespace: str,
    spec: dict[str, Any],
    owner: dict[str, Any] | None,
) -> dict[str, Any]:
    """Build the artifact collector Job."""

    names = resource_names(name)
    container = _container(
        name="collector",
        image=_image(spec),
        args=["operator", "collect", "--report-json", f"{REPORT_MOUNT}/{name}/run.json"],
        env=[],
        role_spec=spec.get("controller", {}),
        mounts=[_artifact_mount()],
    )
    return _job(
        name=names.collector_job,
        namespace=namespace,
        run_name=name,
        role="collector",
        owner=owner,
        parallelism=1,
        container=container,
        pod_spec=_pod_spec(spec.get("controller", {}), volumes=[_artifact_volume(name)]),
    )


def detect_spec_drift(old: dict[str, Any], new: dict[str, Any]) -> bool:
    """Return whether an immutable post-start spec field changed."""

    immutable_fields = (
        "image",
        "config",
        "files",
        "execution",
        "clusterTokenSecretRef",
        "controller",
        "worker",
        "artifacts",
    )
    return any(old.get(field) != new.get(field) for field in immutable_fields)


def result_ref(name: str, spec: dict[str, Any]) -> dict[str, str] | None:
    """Build a status resultRef for artifact storage."""

    if not _artifact_pvc_enabled(spec):
        return None
    return {
        "kind": "PersistentVolumeClaim",
        "name": resource_names(name).artifacts,
        "path": f"{REPORT_MOUNT}/{name}",
    }


def _metadata(resource_name: str, namespace: str, run_name: str, owner: dict[str, Any] | None) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "name": resource_name,
        "namespace": namespace,
        "labels": _labels(run_name),
    }
    if owner is not None and owner.get("uid"):
        metadata["ownerReferences"] = [
            {
                "apiVersion": owner.get("apiVersion", f"{GROUP}/{VERSION}"),
                "kind": owner.get("kind", KIND),
                "name": owner.get("name", run_name),
                "uid": owner["uid"],
                "controller": True,
                "blockOwnerDeletion": True,
            }
        ]
    return metadata


def _labels(run_name: str) -> dict[str, str]:
    return {
        "app.kubernetes.io/name": "veriload",
        "app.kubernetes.io/managed-by": APP_LABEL,
        "veriload.io/run": run_name,
    }


def _selector(run_name: str, role: str) -> dict[str, str]:
    return {**_labels(run_name), "veriload.io/role": role}


def _job(
    *,
    name: str,
    namespace: str,
    run_name: str,
    role: str,
    owner: dict[str, Any] | None,
    parallelism: int,
    container: dict[str, Any],
    pod_spec: dict[str, Any],
) -> dict[str, Any]:
    template_metadata = {
        "labels": _selector(run_name, role),
    }
    template_spec = {
        **pod_spec,
        "restartPolicy": "Never",
        "containers": [container],
    }
    return {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": _metadata(name, namespace, run_name, owner),
        "spec": {
            "parallelism": parallelism,
            "completions": parallelism,
            "template": {
                "metadata": template_metadata,
                "spec": template_spec,
            },
        },
    }


def _container(
    *,
    name: str,
    image: str,
    args: list[str],
    env: list[dict[str, Any]],
    role_spec: dict[str, Any],
    mounts: list[dict[str, Any]],
) -> dict[str, Any]:
    container: dict[str, Any] = {
        "name": name,
        "image": image,
        "args": args,
        "env": [*env, {"name": "PYTHONUNBUFFERED", "value": "1"}],
    }
    for key in ("envFrom", "resources", "volumeMounts"):
        if key == "volumeMounts":
            configured_mounts = list(role_spec.get(key, ()))
            container[key] = [*mounts, *configured_mounts]
        elif key in role_spec:
            container[key] = role_spec[key]
    if "volumeMounts" not in container and mounts:
        container["volumeMounts"] = mounts
    return container


def _pod_spec(role_spec: dict[str, Any], *, volumes: list[dict[str, Any]]) -> dict[str, Any]:
    pod_spec: dict[str, Any] = {}
    for key in (
        "serviceAccountName",
        "imagePullSecrets",
        "nodeSelector",
        "affinity",
        "tolerations",
        "topologySpreadConstraints",
    ):
        if key in role_spec:
            pod_spec[key] = role_spec[key]
    configured_volumes = list(role_spec.get("volumes", ()))
    if volumes or configured_volumes:
        pod_spec["volumes"] = [*volumes, *configured_volumes]
    return pod_spec


def _volumes(name: str, spec: dict[str, Any], *, include_artifacts: bool) -> list[dict[str, Any]]:
    volumes = [_config_volume(name, spec)]
    files_ref = spec.get("files", {}).get("configMapRef")
    if isinstance(files_ref, dict):
        volumes.append({"name": "workspace", "configMap": {"name": files_ref["name"]}})
    if include_artifacts and _artifact_pvc_enabled(spec):
        volumes.append(_artifact_volume(name))
    return volumes


def _volume_mounts(name: str, spec: dict[str, Any], *, include_artifacts: bool) -> list[dict[str, Any]]:
    mounts = [{"name": "config", "mountPath": CONFIG_MOUNT, "readOnly": True}]
    files_ref = spec.get("files", {}).get("configMapRef")
    if isinstance(files_ref, dict):
        mounts.append({"name": "workspace", "mountPath": WORKSPACE_MOUNT, "readOnly": True})
    if include_artifacts and _artifact_pvc_enabled(spec):
        mounts.append(_artifact_mount())
    return mounts


def _config_volume(name: str, spec: dict[str, Any]) -> dict[str, Any]:
    config_ref = _config_ref(spec)
    if config_ref is None:
        config_ref = {"name": resource_names(name).config, "key": "veriload.yaml"}
    return {
        "name": "config",
        "configMap": {
            "name": config_ref["name"],
            "items": [{"key": config_ref.get("key", "veriload.yaml"), "path": "veriload.yaml"}],
        },
    }


def _artifact_volume(name: str) -> dict[str, Any]:
    return {"name": "artifacts", "persistentVolumeClaim": {"claimName": resource_names(name).artifacts}}


def _artifact_mount() -> dict[str, Any]:
    return {"name": "artifacts", "mountPath": REPORT_MOUNT}


def _cluster_token_env(spec: dict[str, Any], name: str) -> list[dict[str, Any]]:
    token_ref = _token_ref(spec) or {"name": resource_names(name).token, "key": "token"}
    return [
        {
            "name": "VERILOAD_CLUSTER_TOKEN",
            "valueFrom": {
                "secretKeyRef": {
                    "name": token_ref["name"],
                    "key": token_ref.get("key", "token"),
                }
            },
        }
    ]


def _report_env(name: str, *, enabled: bool) -> list[dict[str, str]]:
    if not enabled:
        return []
    base = f"{REPORT_MOUNT}/{name}"
    return [
        {"name": "VERILOAD_REPORTS__JSON", "value": f"{base}/run.json"},
        {"name": "VERILOAD_REPORTS__JUNIT", "value": f"{base}/slo.junit.xml"},
        {"name": "VERILOAD_REPORTS__TRACE", "value": f"{base}/events.jsonl"},
        {"name": "VERILOAD_REPORTS__REPLAY", "value": f"{base}/replay.json"},
    ]


def _image(spec: dict[str, Any]) -> str:
    image = spec.get("image")
    if not image:
        raise ValueError("spec.image is required")
    return str(image)


def _workers(spec: dict[str, Any]) -> int:
    workers = int(spec.get("execution", {}).get("workers", 1))
    if workers <= 0:
        raise ValueError("spec.execution.workers must be greater than 0")
    return workers


def _controller_port(spec: dict[str, Any]) -> int:
    return int(spec.get("execution", {}).get("controllerPort", 5557))


def _artifacts_enabled(spec: dict[str, Any]) -> bool:
    return bool(spec.get("artifacts", {}).get("enabled", True))


def _artifact_pvc_enabled(spec: dict[str, Any]) -> bool:
    artifacts = spec.get("artifacts", {})
    if not bool(artifacts.get("enabled", True)):
        return False
    return artifacts.get("storage", {}).get("mode", "pvc") == "pvc"


def _config_ref(spec: dict[str, Any]) -> dict[str, Any] | None:
    value = spec.get("config", {}).get("configMapRef")
    return value if isinstance(value, dict) else None


def _token_ref(spec: dict[str, Any]) -> dict[str, Any] | None:
    value = spec.get("clusterTokenSecretRef")
    return value if isinstance(value, dict) else None
