import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import typer
from typer.testing import CliRunner

from veriload.cli import app

runner = CliRunner()


def test_cli_exposes_typer_app() -> None:
    assert isinstance(app, typer.Typer)


def test_cli_exposes_distributed_commands() -> None:
    result = runner.invoke(app, ["distributed", "--help"])

    assert result.exit_code == 0
    assert "controller" in result.output
    assert "worker" in result.output


def test_validate_command_prints_rich_success_output(tmp_path: Path) -> None:
    config_path = tmp_path / "veriload.yaml"
    config_path.write_text(
        """
run:
  base_url: "https://api.example.test"
  users: 1
  spawn_rate: 1
  max_duration_seconds: 1
data:
  pool_size: 1
  seed: 42
  source: memory
profile:
  type: soak
  target_users: 1
  duration_seconds: 1
safety:
  allowed_hosts:
    - api.example.test
""",
        encoding="utf-8",
    )

    result = runner.invoke(app, ["validate", "--config", str(config_path)])

    assert result.exit_code == 0
    assert "Configuration valid" in result.output
    assert "api.example.test" in result.output
    assert '"seed": 42' in result.output


def test_validate_command_can_preview_safe_personas(tmp_path: Path) -> None:
    config_path = tmp_path / "veriload.yaml"
    config_path.write_text(
        """
run:
  base_url: "https://api.example.test"
  users: 1
  spawn_rate: 1
  max_duration_seconds: 1
data:
  pool_size: 3
  seed: 42
  source: memory
profile:
  type: soak
  target_users: 1
  duration_seconds: 1
safety:
  allowed_hosts:
    - api.example.test
""",
        encoding="utf-8",
    )

    result = runner.invoke(app, ["validate", "--config", str(config_path), "--show-personas", "2"])

    assert result.exit_code == 0
    assert "Persona Preview" in result.output
    assert "example.invalid" in result.output


def test_validate_command_prints_rich_error_output(tmp_path: Path) -> None:
    config_path = tmp_path / "veriload.yaml"
    config_path.write_text("[]", encoding="utf-8")

    result = runner.invoke(app, ["validate", "--config", str(config_path)])

    assert result.exit_code == 1
    assert "Configuration invalid" in result.output
    assert "YAML mapping" in result.output


def test_dataset_export_and_explain_commands_use_configured_personas(tmp_path: Path) -> None:
    config_path = tmp_path / "veriload.yaml"
    export_path = tmp_path / "personas.jsonl"
    config_path.write_text(
        """
run:
  base_url: "https://api.example.test"
  users: 1
  spawn_rate: 1
  max_duration_seconds: 1
data:
  pool_size: 2
  seed: 42
  source: memory
profile:
  type: soak
  target_users: 1
  duration_seconds: 1
safety:
  allowed_hosts:
    - api.example.test
""",
        encoding="utf-8",
    )

    export_result = runner.invoke(
        app,
        ["dataset", "export", "--config", str(config_path), "--output", str(export_path)],
    )
    explain_result = runner.invoke(
        app,
        ["dataset", "explain", "contact.email", "--config", str(config_path)],
    )

    assert export_result.exit_code == 0
    assert export_path.exists()
    assert "Exported 2 personas" in export_result.output
    assert explain_result.exit_code == 0
    assert "contact.email" in explain_result.output
    assert "example.invalid" in explain_result.output


def test_dataset_preview_and_validate_commands_report_dataset_details(tmp_path: Path) -> None:
    config_path = tmp_path / "veriload.yaml"
    config_path.write_text(
        """
run:
  base_url: "https://api.example.test"
  users: 1
  spawn_rate: 1
  max_duration_seconds: 1
data:
  pool_size: 2
  seed: 42
  source: memory
profile:
  type: soak
  target_users: 1
  duration_seconds: 1
safety:
  allowed_hosts:
    - api.example.test
""",
        encoding="utf-8",
    )

    preview_result = runner.invoke(
        app,
        ["dataset", "preview", "--config", str(config_path), "--limit", "1"],
    )
    validate_result = runner.invoke(
        app,
        ["dataset", "validate", "--config", str(config_path)],
    )

    assert preview_result.exit_code == 0
    assert "Persona Preview" in preview_result.output
    assert validate_result.exit_code == 0
    assert "Dataset valid" in validate_result.output
    assert "Unique Emails" in validate_result.output


def test_import_openapi_command_writes_scenario_file(tmp_path: Path) -> None:
    spec_path = tmp_path / "openapi.json"
    output_path = tmp_path / "generated_scenario.py"
    spec_path.write_text(
        json.dumps(
            {
                "openapi": "3.1.0",
                "paths": {
                    "/users": {
                        "get": {"operationId": "listUsers"},
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        [
            "import",
            "openapi",
            str(spec_path),
            "--output",
            str(output_path),
            "--class-name",
            "GeneratedUser",
        ],
    )

    assert result.exit_code == 0
    assert "Generated scenario" in result.output
    assert "class GeneratedUser(VeriUser)" in output_path.read_text(encoding="utf-8")


def test_import_curl_har_and_postman_commands_write_scenario_files(tmp_path: Path) -> None:
    curl_output = tmp_path / "curl_scenario.py"
    har_path = tmp_path / "flow.har"
    har_output = tmp_path / "har_scenario.py"
    postman_path = tmp_path / "collection.json"
    postman_output = tmp_path / "postman_scenario.py"
    har_path.write_text(
        json.dumps(
            {
                "log": {
                    "entries": [
                        {"request": {"method": "GET", "url": "https://api.example.test/a"}}
                    ]
                }
            }
        ),
        encoding="utf-8",
    )
    postman_path.write_text(
        json.dumps(
            {
                "item": [
                    {
                        "name": "List",
                        "request": {
                            "method": "GET",
                            "url": {"raw": "https://api.example.test/users"},
                        },
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    curl_result = runner.invoke(
        app,
        [
            "import",
            "curl",
            "curl https://api.example.test/health",
            "--output",
            str(curl_output),
        ],
    )
    har_result = runner.invoke(
        app,
        ["import", "har", str(har_path), "--output", str(har_output)],
    )
    postman_result = runner.invoke(
        app,
        ["import", "postman", str(postman_path), "--output", str(postman_output)],
    )

    assert curl_result.exit_code == 0
    assert har_result.exit_code == 0
    assert postman_result.exit_code == 0
    assert '"/health"' in curl_output.read_text(encoding="utf-8")
    assert '"/a"' in har_output.read_text(encoding="utf-8")
    assert '"/users"' in postman_output.read_text(encoding="utf-8")


def test_template_auth_lifecycle_command_writes_cli_first_example(tmp_path: Path) -> None:
    output_dir = tmp_path / "auth-template"

    result = runner.invoke(
        app,
        [
            "template",
            "auth-lifecycle",
            "--output-dir",
            str(output_dir),
            "--base-url",
            "https://api.example.test",
            "--user-class",
            "CheckoutAuthUser",
        ],
    )

    assert result.exit_code == 0
    assert "Auth lifecycle template written" in result.output
    assert "class CheckoutAuthUser(VeriUser)" in (output_dir / "scenario.py").read_text(
        encoding="utf-8"
    )
    assert "api.example.test" in (output_dir / "veriload.yaml").read_text(encoding="utf-8")


def test_k8s_manifest_command_writes_manifest(tmp_path: Path) -> None:
    config_path = tmp_path / "veriload.yaml"
    output_path = tmp_path / "veriload-k8s.yaml"
    config_path.write_text("run:\n  users: 1\n", encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "k8s",
            "manifest",
            "--config",
            str(config_path),
            "--output",
            str(output_path),
            "--name",
            "checkout-load",
            "--image",
            "ghcr.io/acme/veriload:latest",
            "--workers",
            "2",
        ],
    )

    assert result.exit_code == 0
    assert "Kubernetes manifest written" in result.output
    text = output_path.read_text(encoding="utf-8")
    assert "checkout-load-worker" in text
    assert "parallelism: 2" in text


def test_k8s_operator_manifest_command_writes_install_manifest(tmp_path: Path) -> None:
    output_path = tmp_path / "veriload-operator.yaml"

    result = runner.invoke(
        app,
        [
            "k8s",
            "operator-manifest",
            "--output",
            str(output_path),
            "--name",
            "veriload-operator",
            "--namespace",
            "veriload-system",
            "--image",
            "ghcr.io/acme/veriload-operator:latest",
        ],
    )

    assert result.exit_code == 0
    assert "Kubernetes operator manifest written" in result.output
    text = output_path.read_text(encoding="utf-8")
    assert "kind: CustomResourceDefinition" in text
    assert "veriloadruns.veriload.io" in text
    assert "ghcr.io/acme/veriload-operator:latest" in text


def test_operator_collect_command_prints_summary(tmp_path: Path) -> None:
    report_path = tmp_path / "run.json"
    report_path.write_text(
        json.dumps(
            {
                "total_requests": 10,
                "total_failures": 1,
                "error_rate": 0.1,
                "latency_ms": {"p95": 50},
            }
        ),
        encoding="utf-8",
    )

    result = runner.invoke(app, ["operator", "collect", "--report-json", str(report_path)])

    assert result.exit_code == 0
    assert json.loads(result.output) == {
        "errorRate": 0.1,
        "p95Ms": 50.0,
        "totalFailures": 1,
        "totalRequests": 10,
    }


def test_run_command_executes_scenario_against_http_target(tmp_path: Path) -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _OkHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        scenario_path = tmp_path / "scenario.py"
        scenario_path.write_text(
            """
from veriload import VeriUser, task

class SmokeUser(VeriUser):
    @task(weight=1)
    async def ping(self):
        await self.http.get("/ok", name="GET /ok")
        self.stop()
""",
            encoding="utf-8",
        )
        config_path = tmp_path / "veriload.yaml"
        config_path.write_text(
            f"""
scenario:
  path: "{scenario_path}"
  user_class: SmokeUser
run:
  base_url: "http://127.0.0.1:{server.server_port}"
  users: 2
  spawn_rate: 2
  max_duration_seconds: 1
data:
  pool_size: 2
  seed: 42
  source: memory
profile:
  type: soak
  target_users: 2
  duration_seconds: 1
  tick_seconds: 0
safety:
  allowed_hosts:
    - 127.0.0.1
""",
            encoding="utf-8",
        )

        result = runner.invoke(app, ["run", "--config", str(config_path)])
    finally:
        server.shutdown()
        thread.join(timeout=2)

    assert result.exit_code == 0
    assert "Run complete" in result.output
    assert "Total Requests" in result.output
    assert "2" in result.output


def test_run_command_executes_database_scenario(tmp_path: Path) -> None:
    scenario_path = tmp_path / "scenario.py"
    scenario_path.write_text(
        """
from veriload import VeriUser, task

class DatabaseSmokeUser(VeriUser):
    async def on_start(self):
        await self.db.execute(
            "CREATE TABLE users (email TEXT NOT NULL)",
            name="create users table",
        )
        await self.db.execute(
            "INSERT INTO users (email) VALUES (?)",
            parameters=(self.persona.contact.email,),
            name="insert synthetic user",
        )

    @task(weight=1)
    async def lookup_user(self):
        result = await self.db.query(
            "SELECT email FROM users WHERE email = ?",
            parameters=(self.persona.contact.email,),
            name="select synthetic user",
        )
        assert result.rows == ((self.persona.contact.email,),)
        self.stop()
""",
        encoding="utf-8",
    )
    config_path = tmp_path / "veriload.yaml"
    config_path.write_text(
        f"""
scenario:
  path: "{scenario_path}"
  user_class: DatabaseSmokeUser
run:
  database:
    driver: sqlite
    dsn: ":memory:"
  users: 1
  spawn_rate: 1
  max_duration_seconds: 1
data:
  pool_size: 1
  seed: 42
  source: memory
profile:
  type: soak
  target_users: 1
  duration_seconds: 1
  tick_seconds: 0
reports:
  json: summary.json
safety:
  allowed_hosts: []
""",
        encoding="utf-8",
    )

    result = runner.invoke(app, ["run", "--config", str(config_path)])

    assert result.exit_code == 0
    report = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    assert report["total_requests"] == 3
    assert report["endpoints"]["select synthetic user"]["total_requests"] == 1


def test_run_command_accepts_worker_count(tmp_path: Path) -> None:
    scenario_path = tmp_path / "scenario.py"
    scenario_path.write_text(
        """
from veriload import VeriUser, task
from veriload.metrics import RequestFinished

class CountUser(VeriUser):
    @task(weight=1)
    async def count(self):
        self.events.emit(RequestFinished(
            name="count",
            method="INTERNAL",
            status_code=200,
            latency_ms=1,
            segment=self.persona_segment,
        ))
        self.stop()
""",
        encoding="utf-8",
    )
    config_path = tmp_path / "veriload.yaml"
    config_path.write_text(
        f"""
scenario:
  path: "{scenario_path}"
  user_class: CountUser
run:
  base_url: "https://api.example.test"
  users: 4
  spawn_rate: 4
  max_duration_seconds: 1
data:
  pool_size: 4
  seed: 42
  source: memory
profile:
  type: soak
  target_users: 4
  duration_seconds: 1
  tick_seconds: 0
safety:
  allowed_hosts:
    - api.example.test
""",
        encoding="utf-8",
    )

    result = runner.invoke(app, ["run", "--config", str(config_path), "--workers", "2"])

    assert result.exit_code == 0
    assert "Workers" in result.output
    assert "2" in result.output


def test_run_command_writes_reports_and_fails_on_slo_breach(tmp_path: Path) -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _FailingHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        scenario_path = tmp_path / "scenario.py"
        scenario_path.write_text(
            """
from veriload import VeriUser, task

class SmokeUser(VeriUser):
    @task(weight=1)
    async def ping(self):
        await self.http.get("/fail", name="GET /fail")
        self.stop()
""",
            encoding="utf-8",
        )
        config_path = tmp_path / "veriload.yaml"
        config_path.write_text(
            f"""
scenario:
  path: "{scenario_path}"
  user_class: SmokeUser
run:
  base_url: "http://127.0.0.1:{server.server_port}"
  users: 1
  spawn_rate: 1
  max_duration_seconds: 1
data:
  pool_size: 1
  seed: 42
  source: memory
profile:
  type: soak
  target_users: 1
  duration_seconds: 1
  tick_seconds: 0
slo:
  global:
    max_error_rate: 0.01
reports:
  json: summary.json
  junit: junit.xml
safety:
  allowed_hosts:
    - 127.0.0.1
""",
            encoding="utf-8",
        )

        result = runner.invoke(app, ["run", "--config", str(config_path)])
    finally:
        server.shutdown()
        thread.join(timeout=2)

    assert result.exit_code == 1
    assert "SLO breached" in result.output
    assert (tmp_path / "summary.json").exists()
    assert (tmp_path / "junit.xml").exists()


def test_run_command_writes_trace_and_replay_reports(tmp_path: Path) -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _FailingHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        scenario_path = tmp_path / "scenario.py"
        scenario_path.write_text(
            """
from veriload import VeriUser, task

class SmokeUser(VeriUser):
    @task(weight=1)
    async def ping(self):
        await self.http.get("/fail", name="GET /fail")
        self.stop()
""",
            encoding="utf-8",
        )
        config_path = tmp_path / "veriload.yaml"
        config_path.write_text(
            f"""
scenario:
  path: "{scenario_path}"
  user_class: SmokeUser
run:
  base_url: "http://127.0.0.1:{server.server_port}"
  users: 1
  spawn_rate: 1
  max_duration_seconds: 1
data:
  pool_size: 1
  seed: 42
  source: memory
profile:
  type: soak
  target_users: 1
  duration_seconds: 1
  tick_seconds: 0
reports:
  json: summary.json
  trace: trace.jsonl
  replay: replay.json
safety:
  allowed_hosts:
    - 127.0.0.1
""",
            encoding="utf-8",
        )

        result = runner.invoke(app, ["run", "--config", str(config_path)])
    finally:
        server.shutdown()
        thread.join(timeout=2)

    assert result.exit_code == 0
    report = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    trace_text = (tmp_path / "trace.jsonl").read_text(encoding="utf-8")
    replay = json.loads((tmp_path / "replay.json").read_text(encoding="utf-8"))
    assert report["workers"] == []
    assert '"persona_id": "p0"' in trace_text
    assert replay["failed_personas"] == ["p0"]


def test_run_command_writes_worker_metadata_for_distributed_json_report(tmp_path: Path) -> None:
    scenario_path = tmp_path / "scenario.py"
    scenario_path.write_text(
        """
from veriload import VeriUser, task
from veriload.metrics import RequestFinished

class CountUser(VeriUser):
    @task(weight=1)
    async def count(self):
        self.events.emit(RequestFinished(
            name="count",
            method="INTERNAL",
            status_code=200,
            latency_ms=1,
            segment=self.persona_segment,
        ))
        self.stop()
""",
        encoding="utf-8",
    )
    config_path = tmp_path / "veriload.yaml"
    config_path.write_text(
        f"""
scenario:
  path: "{scenario_path}"
  user_class: CountUser
run:
  base_url: "https://api.example.test"
  users: 5
  spawn_rate: 5
  max_duration_seconds: 1
data:
  pool_size: 5
  seed: 42
  source: memory
profile:
  type: soak
  target_users: 5
  duration_seconds: 1
  tick_seconds: 0
reports:
  json: summary.json
safety:
  allowed_hosts:
    - api.example.test
""",
        encoding="utf-8",
    )

    result = runner.invoke(app, ["run", "--config", str(config_path), "--workers", "2"])

    assert result.exit_code == 0
    report = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    assert [worker["target_users"] for worker in report["workers"]] == [3, 2]
    assert [worker["summary"]["total_requests"] for worker in report["workers"]] == [3, 2]


def test_replay_command_prints_persona_and_request_trace(tmp_path: Path) -> None:
    replay_path = tmp_path / "replay.json"
    replay_path.write_text(
        json.dumps(
            {
                "seed": 42,
                "pool_size": 1,
                "locales": [{"locale": "en_US", "weight": 1.0}],
                "base_url": "https://api.example.test",
                "scenario": {"path": "scenario.py", "user_class": "SmokeUser"},
                "workers": 1,
                "events": [
                    {
                        "name": "GET /fail",
                        "method": "GET",
                        "error": "HTTP 500",
                        "latency_ms": 12,
                        "segment": "en_US:Retail",
                        "persona_id": "p0",
                    }
                ],
                "failed_personas": ["p0"],
            }
        ),
        encoding="utf-8",
    )

    result = runner.invoke(app, ["replay", str(replay_path), "--persona-id", "p0"])

    assert result.exit_code == 0
    assert "Replay plan" in result.output
    assert "example.invalid" in result.output
    assert "GET /fail" in result.output


def test_compare_command_fails_on_regression(tmp_path: Path) -> None:
    baseline = tmp_path / "baseline.json"
    current = tmp_path / "current.json"
    baseline.write_text(
        json.dumps({"latency_ms": {"p95": 100}, "error_rate": 0.01, "endpoints": {}}),
        encoding="utf-8",
    )
    current.write_text(
        json.dumps({"latency_ms": {"p95": 150}, "error_rate": 0.05, "endpoints": {}}),
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        [
            "compare",
            str(baseline),
            str(current),
            "--max-p95-regression-ms",
            "10",
            "--max-error-rate-regression",
            "0.01",
        ],
    )

    assert result.exit_code == 1
    assert "Regression detected" in result.output
    assert "2 regression(s) across 1 scope(s)" in result.output
    assert "p95_ms" in result.output


class _OkHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"ok": true}')

    def log_message(self, format: str, *args: object) -> None:
        return


class _FailingHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        self.send_response(503)
        self.send_header("content-type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"error": "unavailable"}')

    def log_message(self, format: str, *args: object) -> None:
        return
