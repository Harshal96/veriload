import json
import py_compile
from pathlib import Path

import pytest
import yaml

from veriload.compare import compare_run_reports
from veriload.config import load_config
from veriload.runtime import execute_config
from veriload.scenarios import load_user_class
from veriload.users import VeriUser


def test_httpbin_example_config_and_scenario_load() -> None:
    example_dir = Path("examples/httpbin")
    config = load_config(example_dir / "veriload.yaml")
    user_class = load_user_class(example_dir / config.scenario.path, config.scenario.user_class)

    assert config.run.base_url == "https://httpbin.org"
    assert "httpbin.org" in config.safety.allowed_hosts
    assert issubclass(user_class, VeriUser)


@pytest.mark.asyncio
async def test_database_example_config_scenario_and_run() -> None:
    example_dir = Path("examples/database")
    config = load_config(example_dir / "veriload.yaml")
    user_class = load_user_class(example_dir / config.scenario.path, config.scenario.user_class)

    assert config.run.base_url is None
    assert config.run.database is not None
    assert config.run.database.driver == "sqlite"
    assert config.run.database.dsn == ":memory:"
    assert issubclass(user_class, VeriUser)

    execution = await execute_config(config, config_dir=example_dir)

    assert execution.summary.total_requests == 4
    assert execution.summary.total_failures == 0
    assert execution.summary.endpoints["DB select synthetic user"].total_requests == 1


def test_report_comparison_example_detects_checkout_regression() -> None:
    example_dir = Path("examples/reports")
    baseline = json.loads((example_dir / "baseline.json").read_text(encoding="utf-8"))
    current = json.loads((example_dir / "current-regression.json").read_text(encoding="utf-8"))

    result = compare_run_reports(
        baseline,
        current,
        max_p95_regression_ms=25,
        max_error_rate_regression=0.01,
    )

    assert not result.passed
    assert "endpoint:POST /checkout" in result.affected_scopes


def test_model_fixture_examples_compile_and_cover_connectors() -> None:
    example_dir = Path("examples/model_fixtures")
    scenario_paths = (
        example_dir / "sqlmodel_scenario.py",
        example_dir / "django_scenario.py",
        example_dir / "openapi_scenario.py",
        example_dir / "custom_field_names_scenario.py",
    )

    for scenario_path in scenario_paths:
        py_compile.compile(str(scenario_path), doraise=True)

    readme = (example_dir / "README.md").read_text(encoding="utf-8")
    assert "SQLModelConnector" in readme
    assert "DjangoORMConnector" in readme
    assert "OpenAPIConnector.from_file" in readme
    assert "overrides" in readme

    openapi_spec = (example_dir / "openapi.yaml").read_text(encoding="utf-8")
    assert "operationId: createCustomer" in openapi_spec
    assert "operationId: deleteCustomer" in openapi_spec


def test_kubernetes_operator_example_loads_and_builds_resources() -> None:
    from veriload.k8s_operator.resources import build_collector_job, build_owned_resources

    example_dir = Path("examples/kubernetes_operator")
    run_path = example_dir / "veriloadrun.yaml"
    files_path = example_dir / "scenario-files-configmap.yaml"
    scenario_path = example_dir / "scenario.py"

    py_compile.compile(str(scenario_path), doraise=True)
    run = yaml.safe_load(run_path.read_text(encoding="utf-8"))
    files_config = yaml.safe_load(files_path.read_text(encoding="utf-8"))

    spec = run["spec"]
    resources = build_owned_resources(
        name=run["metadata"]["name"],
        namespace=run["metadata"]["namespace"],
        spec=spec,
        owner={
            "apiVersion": run["apiVersion"],
            "kind": run["kind"],
            "name": run["metadata"]["name"],
            "uid": "example-uid",
        },
    )
    collector = build_collector_job(
        name=run["metadata"]["name"],
        namespace=run["metadata"]["namespace"],
        spec=spec,
        owner=None,
    )

    assert run["kind"] == "VeriLoadRun"
    assert files_config["metadata"]["name"] == spec["files"]["configMapRef"]["name"]
    assert files_config["data"]["scenario.py"] == scenario_path.read_text(encoding="utf-8")
    assert [resource["kind"] for resource in resources] == [
        "ConfigMap",
        "Secret",
        "Service",
        "PersistentVolumeClaim",
        "Job",
        "Job",
    ]

    controller = resources[-2]
    worker = resources[-1]
    controller_container = controller["spec"]["template"]["spec"]["containers"][0]
    worker_container = worker["spec"]["template"]["spec"]["containers"][0]

    assert controller_container["image"] == "ghcr.io/acme/veriload-scenarios:latest"
    assert {"name": "workspace", "configMap": {"name": "checkout-load-files"}} in controller["spec"][
        "template"
    ]["spec"]["volumes"]
    assert {"name": "workspace", "mountPath": "/workspace", "readOnly": True} in controller_container["volumeMounts"]
    assert worker["spec"]["parallelism"] == 4
    assert worker_container["args"] == [
        "distributed",
        "worker",
        "--controller-host",
        "checkout-load-controller",
        "--controller-port",
        "5557",
        "--cluster-token-env",
        "VERILOAD_CLUSTER_TOKEN",
    ]
    assert collector["metadata"]["name"] == "checkout-load-collector"
