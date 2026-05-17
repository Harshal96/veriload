import builtins
import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from veriload.k8s_operator.collector import collect_report_summary
from veriload.k8s_operator.handlers import (
    build_initial_status,
    cleanup_resource_names,
    plan_reconcile,
    register_handlers,
    terminal_cleanup_resource_names,
)
from veriload.k8s_operator.manifest import generate_operator_install_manifest
from veriload.k8s_operator.resources import (
    FINALIZER,
    build_artifact_pvc,
    build_collector_job,
    build_config_map,
    build_controller_job,
    build_controller_service,
    build_owned_resources,
    build_token_secret,
    build_worker_job,
    detect_spec_drift,
    resource_names,
)


def test_operator_manifest_includes_crd_rbac_and_deployment() -> None:
    manifest = generate_operator_install_manifest(
        name="veriload-operator",
        namespace="veriload-system",
        image="ghcr.io/acme/veriload-operator:latest",
    )

    docs = [doc for doc in yaml.safe_load_all(manifest) if doc]
    kinds = [doc["kind"] for doc in docs]

    assert kinds == [
        "CustomResourceDefinition",
        "ServiceAccount",
        "ClusterRole",
        "ClusterRoleBinding",
        "Deployment",
    ]
    crd = docs[0]
    assert crd["metadata"]["name"] == "veriloadruns.veriload.io"
    assert crd["spec"]["group"] == "veriload.io"
    assert crd["spec"]["versions"][0]["name"] == "v1alpha1"
    assert crd["spec"]["versions"][0]["subresources"] == {"status": {}}
    assert crd["spec"]["versions"][0]["additionalPrinterColumns"][0]["name"] == "Phase"
    spec_properties = crd["spec"]["versions"][0]["schema"]["openAPIV3Schema"]["properties"]["spec"]["properties"]
    assert "cleanupPolicy" in spec_properties
    cluster_role = docs[2]
    resource_rules = {tuple(rule["resources"]) for rule in cluster_role["rules"] if "resources" in rule}
    assert ("veriloadruns",) in resource_rules
    assert ("veriloadruns/status",) in resource_rules
    assert ("jobs",) in resource_rules
    assert ("pods/log",) in resource_rules


def test_resource_builders_use_deterministic_names_and_distributed_args() -> None:
    names = resource_names("checkout-load")
    owner = {
        "apiVersion": "veriload.io/v1alpha1",
        "kind": "VeriLoadRun",
        "name": "checkout-load",
        "uid": "uid-1",
    }
    spec = {
        "image": "ghcr.io/acme/veriload:latest",
        "config": {"inline": "run:\n  users: 10\n"},
        "execution": {"workers": 3, "controllerPort": 6000, "readyTimeoutSeconds": 90},
        "artifacts": {"enabled": True, "storage": {"mode": "pvc", "size": "2Gi"}},
        "controller": {
            "resources": {"requests": {"cpu": "500m"}},
            "env": [{"name": "EXTRA", "value": "1"}],
        },
        "worker": {"nodeSelector": {"load": "true"}},
    }

    config_map = build_config_map(name="checkout-load", namespace="loads", spec=spec, owner=owner)
    token = build_token_secret(name="checkout-load", namespace="loads", owner=owner)
    service = build_controller_service(name="checkout-load", namespace="loads", spec=spec, owner=owner)
    pvc = build_artifact_pvc(name="checkout-load", namespace="loads", spec=spec, owner=owner)
    controller = build_controller_job(name="checkout-load", namespace="loads", spec=spec, owner=owner)
    worker = build_worker_job(name="checkout-load", namespace="loads", spec=spec, owner=owner)

    assert names.config == "checkout-load-config"
    assert names.token == "checkout-load-token"
    assert names.controller_job == "checkout-load-controller"
    assert names.worker_job == "checkout-load-worker"
    assert names.artifacts == "checkout-load-artifacts"
    assert config_map["data"]["veriload.yaml"] == "run:\n  users: 10\n"
    assert token["stringData"]["token"]
    assert service["spec"]["ports"][0]["port"] == 6000
    assert pvc["spec"]["resources"]["requests"]["storage"] == "2Gi"

    controller_container = controller["spec"]["template"]["spec"]["containers"][0]
    worker_container = worker["spec"]["template"]["spec"]["containers"][0]
    assert controller_container["args"] == [
        "distributed",
        "controller",
        "--config",
        "/etc/veriload/veriload.yaml",
        "--bind-host",
        "0.0.0.0",
        "--bind-port",
        "6000",
        "--expect-workers",
        "3",
        "--cluster-token-env",
        "VERILOAD_CLUSTER_TOKEN",
        "--ready-timeout-seconds",
        "90",
    ]
    assert {"name": "VERILOAD_REPORTS__JSON", "value": "/reports/checkout-load/run.json"} in controller_container["env"]
    assert {"name": "EXTRA", "value": "1"} in controller_container["env"]
    assert worker_container["args"][0:2] == ["distributed", "worker"]
    assert worker["spec"]["parallelism"] == 3
    assert worker["spec"]["template"]["spec"]["nodeSelector"] == {"load": "true"}


def test_owned_resources_skip_generated_config_and_secret_when_refs_are_supplied() -> None:
    spec = {
        "image": "ghcr.io/acme/veriload:latest",
        "config": {"configMapRef": {"name": "existing-config", "key": "custom.yaml"}},
        "clusterTokenSecretRef": {"name": "existing-token", "key": "token"},
        "execution": {"workers": 2},
        "artifacts": {"enabled": False},
    }

    resources = build_owned_resources(name="checkout-load", namespace="loads", spec=spec, owner=None)
    names = [resource["metadata"]["name"] for resource in resources]
    kinds = [resource["kind"] for resource in resources]
    controller_env = resources[1]["spec"]["template"]["spec"]["containers"][0]["env"]

    assert kinds == ["Service", "Job", "Job"]
    assert "checkout-load-config" not in names
    assert "checkout-load-token" not in names
    assert {
        "name": "VERILOAD_CLUSTER_TOKEN",
        "valueFrom": {"secretKeyRef": {"name": "existing-token", "key": "token"}},
    } in controller_env


def test_spec_drift_detects_immutable_field_changes_after_start() -> None:
    old = {
        "image": "old",
        "config": {"inline": "a"},
        "execution": {"workers": 1},
        "cleanupPolicy": "Retain",
    }
    new = {
        "image": "new",
        "config": {"inline": "a"},
        "execution": {"workers": 1},
        "cleanupPolicy": "DeleteAlways",
    }
    allowed = {**old, "cleanupPolicy": "DeleteAlways"}

    assert detect_spec_drift(old, new)
    assert not detect_spec_drift(old, allowed)


def test_collect_report_summary_extracts_operator_status_summary(tmp_path: Path) -> None:
    report_path = tmp_path / "run.json"
    report_path.write_text(
        json.dumps(
            {
                "total_requests": 100,
                "total_failures": 2,
                "error_rate": 0.02,
                "latency_ms": {"p95": 123.4},
            }
        ),
        encoding="utf-8",
    )

    assert collect_report_summary(report_path) == {
        "totalRequests": 100,
        "totalFailures": 2,
        "errorRate": 0.02,
        "p95Ms": 123.4,
    }


def test_reconcile_plan_status_and_cleanup_policies() -> None:
    spec = {
        "image": "ghcr.io/acme/veriload:latest",
        "config": {"inline": "run:\n  users: 10\n"},
        "execution": {"workers": 2},
        "artifacts": {"enabled": True, "storage": {"mode": "pvc"}},
    }

    status = build_initial_status(name="checkout-load", generation=4, spec=spec)
    resources = plan_reconcile(name="checkout-load", namespace="loads", spec=spec, uid="uid-1")
    delete_on_success = terminal_cleanup_resource_names(
        name="checkout-load",
        spec={**spec, "cleanupPolicy": "DeleteOnSuccess"},
        phase="Succeeded",
    )
    retain_on_failure = terminal_cleanup_resource_names(
        name="checkout-load",
        spec={**spec, "cleanupPolicy": "DeleteOnSuccess"},
        phase="Failed",
    )
    delete_always = terminal_cleanup_resource_names(
        name="checkout-load",
        spec={**spec, "cleanupPolicy": "DeleteAlways"},
        phase="Failed",
    )
    delete_cr = cleanup_resource_names(name="checkout-load", spec=spec)

    assert status["phase"] == "Preparing"
    assert status["observedGeneration"] == 4
    assert status["expectedWorkers"] == 2
    assert status["resultRef"]["name"] == "checkout-load-artifacts"
    assert {condition["type"] for condition in status["conditions"]} >= {"ConfigReady", "WorkersReady", "Succeeded"}
    assert [resource["kind"] for resource in resources] == [
        "ConfigMap",
        "Secret",
        "Service",
        "PersistentVolumeClaim",
        "Job",
        "Job",
    ]
    assert "checkout-load-controller" in delete_on_success
    assert retain_on_failure == []
    assert "checkout-load-worker" in delete_always
    assert "checkout-load-artifacts" not in delete_cr


def test_helm_chart_installs_operator_surface() -> None:
    chart_path = Path("charts/veriload-operator/Chart.yaml")
    values_path = Path("charts/veriload-operator/values.yaml")
    template_path = Path("charts/veriload-operator/templates/operator.yaml")
    helpers_path = Path("charts/veriload-operator/templates/_helpers.tpl")
    crd_path = Path("charts/veriload-operator/crds/veriloadruns.veriload.io.yaml")

    assert chart_path.exists()
    assert "veriload-operator" in chart_path.read_text(encoding="utf-8")
    assert "repository:" in values_path.read_text(encoding="utf-8")
    template = template_path.read_text(encoding="utf-8")
    assert "kind: Deployment" in template
    assert "kind: ClusterRole" in template
    assert 'resources: ["pods/log"]' in template
    assert 'args: ["operator", "run"]' in template
    assert "veriload.k8sOperator.manifest" in helpers_path.read_text(encoding="utf-8")

    crd = yaml.safe_load(crd_path.read_text(encoding="utf-8"))
    assert crd["kind"] == "CustomResourceDefinition"
    assert crd["metadata"]["name"] == "veriloadruns.veriload.io"


def test_collector_job_mounts_artifacts_and_reads_run_report() -> None:
    spec = {
        "image": "ghcr.io/acme/veriload:latest",
        "config": {"inline": "run:\n  users: 10\n"},
        "execution": {"workers": 2},
        "artifacts": {"enabled": True, "storage": {"mode": "pvc"}},
    }

    collector = build_collector_job(name="checkout-load", namespace="loads", spec=spec, owner=None)
    container = collector["spec"]["template"]["spec"]["containers"][0]

    assert collector["metadata"]["name"] == "checkout-load-collector"
    assert container["args"] == [
        "operator",
        "collect",
        "--report-json",
        "/reports/checkout-load/run.json",
    ]
    assert {"name": "artifacts", "mountPath": "/reports"} in container["volumeMounts"]
    assert collector["spec"]["template"]["spec"]["volumes"] == [
        {"name": "artifacts", "persistentVolumeClaim": {"claimName": "checkout-load-artifacts"}}
    ]


def test_register_handlers_applies_status_resources_drift_and_delete() -> None:
    registry = _FakeKopf()
    client = _FakeClient()
    register_handlers(registry, client)
    spec = {
        "image": "ghcr.io/acme/veriload:latest",
        "config": {"inline": "run:\n  users: 10\n"},
        "execution": {"workers": 2},
        "artifacts": {"enabled": True, "storage": {"mode": "pvc"}},
    }

    patch: dict[str, Any] = {}
    registry.handlers["create"](
        spec=spec,
        name="checkout-load",
        namespace="loads",
        meta={"uid": "uid-1", "generation": 3},
        patch=patch,
    )

    assert patch["metadata"]["finalizers"] == [FINALIZER]
    assert patch["status"]["phase"] == "Preparing"
    assert patch["status"]["observedGeneration"] == 3
    assert [call["name"] for call in client.patch_calls] == [
        "checkout-load-config",
        "checkout-load-token",
        "checkout-load-controller",
        "checkout-load-artifacts",
        "checkout-load-controller",
        "checkout-load-worker",
    ]

    drift_patch: dict[str, Any] = {}
    registry.handlers["update"](
        old={"spec": spec},
        new={"spec": {**spec, "image": "ghcr.io/acme/veriload:new"}},
        status={"phase": "Running"},
        patch=drift_patch,
    )
    assert drift_patch["status"]["conditions"][0]["type"] == "SpecDrifted"

    registry.handlers["delete"](
        spec={**spec, "artifacts": {"enabled": True, "storage": {"mode": "pvc"}, "retain": False}},
        name="checkout-load",
        namespace="loads",
    )
    assert "checkout-load-artifacts" in [call["name"] for call in client.delete_calls]


def test_operator_runtime_reports_missing_optional_dependencies(monkeypatch: pytest.MonkeyPatch) -> None:
    from veriload.k8s_operator.runtime import run_operator

    real_import = builtins.__import__

    def fake_import(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "kopf":
            raise ImportError("missing kopf")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    with pytest.raises(RuntimeError, match="optional operator dependencies"):
        run_operator()


class _FakeKopfOn:
    def __init__(self) -> None:
        self.handlers: dict[str, Any] = {}

    def create(self, *_: Any) -> Any:
        return self._decorator("create")

    def resume(self, *_: Any) -> Any:
        return self._decorator("resume")

    def update(self, *_: Any) -> Any:
        return self._decorator("update")

    def delete(self, *_: Any) -> Any:
        return self._decorator("delete")

    def _decorator(self, name: str) -> Any:
        def register(func: Any) -> Any:
            self.handlers[name] = func
            return func

        return register


class _FakeKopf:
    def __init__(self) -> None:
        self.on = _FakeKopfOn()

    @property
    def handlers(self) -> dict[str, Any]:
        return self.on.handlers


class _FakeApi:
    def __init__(self, client: "_FakeClient") -> None:
        self.client = client

    def patch(self, *, namespace: str, name: str, body: dict[str, Any], **_: Any) -> None:
        self.client.patch_calls.append({"namespace": namespace, "name": name, "body": body})

    def delete(self, *, namespace: str, name: str) -> None:
        self.client.delete_calls.append({"namespace": namespace, "name": name})


class _FakeResources:
    def __init__(self, client: "_FakeClient") -> None:
        self.client = client

    def get(self, **_: Any) -> _FakeApi:
        return _FakeApi(self.client)


class _FakeDynamicClient:
    def __init__(self, client: "_FakeClient") -> None:
        self.resources = _FakeResources(client)


class _FakeClient:
    def __init__(self) -> None:
        self.patch_calls: list[dict[str, Any]] = []
        self.delete_calls: list[dict[str, Any]] = []

    def ApiClient(self) -> object:
        return object()

    def DynamicClient(self, _: object) -> _FakeDynamicClient:
        return _FakeDynamicClient(self)
