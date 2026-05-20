# VeriLoad

Ship load tests that create, exercise, and clean up realistic state.

VeriLoad is a Python-native load testing toolkit for API, database, and
lifecycle-heavy systems. It turns deterministic VeriSim personas into stateful
test traffic, then gives you reports, traces, replay plans, SLO gates,
distributed execution, and Kubernetes-native run orchestration.

Most load tests answer a narrow question: "can this endpoint handle requests?"
VeriLoad is built for the release question teams actually care about: "can this
workflow handle real-looking users, records, auth state, cleanup, and parallel
workers without regressing?"

## Why Teams Use It

- Create unique persona-backed payloads instead of hammering one shared JSON
  body.
- Test API and database workflows with one metrics model.
- Generate fixtures through SQLModel, Django ORM, or OpenAPI and clean them up
  automatically after the run.
- Generate one-row database insert/select workloads from SQLModel or Django
  models, with delete or rollback cleanup.
- Import cURL, OpenAPI, HAR, and Postman files into editable Python scenarios.
- Run locally, with in-process workers, as networked controller/worker jobs, or
  declaratively through the Kubernetes Operator.
- Gate CI on latency, error-rate, and regression thresholds with JSON and JUnit
  reports.
- Debug failures with JSONL traces and persona replay manifests.

## Feature Map

| Feature | What it does |
| --- | --- |
| Persona-backed traffic | Generates deterministic users, emails, companies, locales, and segments from `data.seed`. |
| Python scenario DSL | Uses `VeriUser`, weighted `@task` methods, ordered `@flow` helpers, retries, lifecycle hooks, and per-user state. |
| HTTP and GraphQL metrics | Records status codes, failures, latency, segments, and persona IDs through the injected `self.http` client. |
| Database testing | Injects `self.db` for configured SQLite runs and supports DB-API compatible clients in custom scenarios. |
| Model Fixtures | Creates and tracks SQLModel, Django ORM, and OpenAPI resources through `self.fixtures`. |
| Model Workloads | Generates SQL or ORM insert/select load traffic from SQLModel and Django model metadata. |
| Auto Cleanup | Tracks configured raw POST/INSERT calls and generated model workloads, then cleans them with DELETE, SQL DELETE, or DB rollback. |
| Importers | Generates scenario starters from cURL, OpenAPI, HAR, and Postman inputs. |
| Reports and gates | Writes JSON, JUnit, JSONL trace, replay artifacts, SLO results, and baseline comparisons. |
| Distributed runs | Supports local worker sharding and true networked controller/worker execution. |
| Kubernetes manifest | Emits static ConfigMap, controller Job, and worker Job YAML for distributed runs. |
| Kubernetes Operator | Adds `VeriLoadRun`, RBAC, Helm chart, artifact PVCs, collector jobs, cleanup policies, and status summaries. |

## Choose Your Path

### 1. Run the HTTP Example

```bash
uv sync --extra dev
uv run veriload validate --config examples/httpbin/veriload.yaml --show-personas 2
uv run veriload run --config examples/httpbin/veriload.yaml
```

### 2. Run a Database Smoke Test

```bash
uv run veriload validate --config examples/database/veriload.yaml --show-personas 1
uv run veriload run --config examples/database/veriload.yaml
```

### 3. Generate an Authenticated Lifecycle Scenario

```bash
uv run veriload template auth-lifecycle \
  --output-dir examples/my-auth-flow \
  --base-url https://api.example.test

uv run veriload validate --config examples/my-auth-flow/veriload.yaml --show-personas 3
uv run veriload run --config examples/my-auth-flow/veriload.yaml
```

### 4. Import an Existing API Surface

```bash
uv run veriload import openapi openapi.yaml \
  --output scenarios/openapi_user.py \
  --class-name GeneratedAPIUser
```

Importers create editable starters. They intentionally leave auth, test-data
strategy, and cleanup choices visible in Python so teams can review them.

### 5. Run Through Kubernetes

For static controller/worker Jobs:

```bash
uv run veriload k8s manifest \
  --config veriload.yaml \
  --output k8s/veriload.yaml \
  --image ghcr.io/acme/veriload-scenarios:latest \
  --workers 4
```

For declarative runs with the operator:

```bash
uv run veriload k8s operator-manifest \
  --output k8s/veriload-operator.yaml \
  --image ghcr.io/acme/veriload-operator:latest
kubectl apply -f k8s/veriload-operator.yaml
kubectl apply -f examples/kubernetes_operator/veriloadrun.yaml
```

See `examples/kubernetes_operator/` for a full `VeriLoadRun`, scenario file
ConfigMap, and Helm install path.

## Installation

From this repository:

```bash
uv sync --extra dev
uv run veriload --help
```

With the optional Kubernetes Operator runtime dependencies:

```bash
uv sync --extra dev --extra operator
uv run veriload operator run
```

From a built wheel:

```bash
uv build
python -m pip install dist/veriload-0.1.0-py3-none-any.whl
veriload --help
```

VeriLoad requires Python 3.11 or newer. The default persona source uses
VeriSim. Offline tests and examples can use the built-in deterministic source
with `data.source: memory`.

## Examples

| Path | Use it for |
| --- | --- |
| `examples/httpbin/` | Minimal HTTP scenario with persona-backed query parameters. |
| `examples/database/` | Local SQLite smoke test with setup, query, and cleanup. |
| `examples/auto_cleanup_db/` | Raw DB INSERT cleanup using generated DELETE or rollback. |
| `examples/auth_object_lifecycle/` | Generated auth/object lifecycle template output. |
| `examples/model_fixtures/` | SQLModel, Django ORM, OpenAPI, and custom field-name fixture examples. |
| `examples/model_workloads/` | Generated DB insert/select workload from model metadata. |
| `examples/kubernetes_operator/` | Declarative `VeriLoadRun` example with scenario ConfigMap and artifact PVCs. |
| `examples/reports/` | Baseline/current JSON reports for comparison demos. |

## Scenario Authoring

A scenario is a regular Python class. The runner injects a deterministic
persona, protocol clients, fixtures, and per-user state, then executes weighted
tasks until the user stops or the profile drains.

```python
from veriload import VeriUser, task


class CheckoutSmokeUser(VeriUser):
    async def on_start(self) -> None:
        response = await self.http.post(
            "/auth/login",
            name="POST /auth/login",
            json={
                "email": self.persona.contact.email,
                "external_id": self.persona.persona_id,
            },
        )
        response.raise_for_status()
        self.state = {**self.state, "token": response.json()["access_token"]}

    @task(weight=1)
    async def view_checkout(self) -> None:
        response = await self.http.get(
            "/checkout",
            name="GET /checkout",
            headers={"Authorization": f"Bearer {self.state['token']}"},
        )
        response.raise_for_status()
        self.stop()
```

Use stable request names such as `GET /checkout`. SLOs, comparisons, traces,
and replay plans are keyed by those names.

### Lifecycle APIs

| API | Use |
| --- | --- |
| `async on_start()` | Login, create session state, seed rows, or create model fixtures before tasks run. |
| `async on_stop()` | Cleanup objects, delete rows, revoke tokens, or run `await self.fixtures.cleanup()`. |
| `@task(weight=N)` | Declare a weighted task. Higher weights run more often. |
| `@flow("name")` | Label an ordered helper flow and wrap failures with flow context. |
| `@retryable(max_attempts=N, backoff_seconds=S)` | Retry transient async operations. |
| `self.stop()` | Stop the current virtual user after the current task. Useful for smoke tests. |

Keep per-user state on `self.state`. Prefer immutable updates:

```python
self.state = {**self.state, "access_token": token, "object_ids": (*old_ids, object_id)}
```

### Persona Data

Each user has `self.persona`, `self.persona_segment`, and `self.user_index`.
Useful persona paths include:

| Path | Meaning |
| --- | --- |
| `person.name` | Full generated name. |
| `person.username` | Stable username. |
| `contact.email` | Safe generated email. |
| `company.id` | Stable company identifier. |
| `company.name` | Company name. |
| `job.industry` | Industry segment used in default metrics. |

Use `self.payload()` to map dotted persona fields into request payloads:

```python
profile = self.payload(
    {
        "email": "contact.email",
        "name": "person.name",
        "company_id": "company.id",
    }
)
```

## Model Fixtures

Model fixtures make database and API state setup feel like a one-liner while
keeping cleanup explicit. Every `VeriUser` starts with `self.fixtures`. Add a
connector, call `create()`, then clean up in `on_stop()`.

```python
from myapp.database import SessionLocal
from myapp.models import Customer
from veriload import VeriUser, task
from veriload.fixtures import SQLModelConnector


class CustomerUser(VeriUser):
    async def on_start(self) -> None:
        self.fixtures = self.fixtures.with_connectors(
            SQLModelConnector(session_factory=SessionLocal)
        )
        self.customer = await self.fixtures.create(Customer)

    async def on_stop(self) -> None:
        await self.fixtures.cleanup()

    @task(weight=1)
    async def read_customer(self) -> None:
        result = await self.db.query(
            "SELECT email FROM customer WHERE id = ?",
            parameters=(self.customer.id,),
            name="DB select customer fixture",
        )
        assert result.rows
        self.stop()
```

The fixture mapper derives common fields such as `email`, `name`,
`company_name`, `external_id`, `persona_id`, `locale`, and `run_id` from the
current persona. Cleanup deletes only records created through the fixture
ledger, in reverse creation order, so parent/child records can be torn down
safely.

### Supported Fixture Connectors

| Connector | Import | Best for |
| --- | --- | --- |
| SQLModel | `SQLModelConnector(session_factory=...)` | Direct SQLModel or SQLAlchemy-backed row creation. |
| Django ORM | `DjangoORMConnector()` | Django apps where the load-test process has Django configured. |
| OpenAPI | `OpenAPIConnector.from_file("openapi.yaml", http=self.http, resource="Customer")` | Creating and deleting resources through documented HTTP operations. |

OpenAPI fixture connectors inspect the spec for a `POST` create operation and a
matching `DELETE` cleanup operation. You provide the spec file and resource
name; the connector records returned IDs and deletes those resources during
cleanup.

When your app fields do not match VeriLoad's built-in aliases, wire them
explicitly with `overrides`:

```python
self.customer = await self.fixtures.create(
    Customer,
    overrides={
        "primary_email": self.persona.contact.email,
        "legal_name": self.persona.person.name,
        "tenant_id": self.state["tenant_id"],
    },
)
```

See `examples/model_fixtures/` for:

| Example | Shows |
| --- | --- |
| `sqlmodel_scenario.py` | Direct SQLModel session fixtures. |
| `django_scenario.py` | Django ORM manager-backed fixtures. |
| `openapi_scenario.py` and `openapi.yaml` | OpenAPI resource fixtures from a spec file. |
| `custom_field_names_scenario.py` | Explicit persona wiring with `overrides` for non-standard field names. |

## Model-Generated Database Workloads

Use model workloads when the database operation itself is the load-test traffic.
VeriLoad can inspect SQLModel/SQLAlchemy or Django model metadata, generate one
persona-backed row, select it, and register cleanup automatically.

```python
class CustomerDatabaseUser(VeriUser):
    async def on_start(self) -> None:
        self.customer_workload = self.model_workload(
            Customer,
            mode="sql",
            overrides={"tenant_id": "load-test"},
        )

    @task(weight=1)
    async def insert_and_read_customer(self) -> None:
        customer = await self.customer_workload.insert()
        result = await self.customer_workload.select(customer)
        assert result.rows
        self.stop()
```

`mode="sql"` runs generated SQL through `self.db`, so insert/select operations
count in normal DB metrics. `mode="orm"` uses SQLModel sessions or Django
managers and emits `ORM insert <Model>` / `ORM select <Model>` metrics.
Fields that do not match VeriLoad's persona aliases must be supplied with
`overrides`.

## Protocol Coverage

### HTTP and GraphQL

`self.http` records metrics for HTTP status codes, request failures, latency,
segments, and persona IDs.

```python
await self.http.post(
    "/objects",
    name="POST /objects",
    headers={"Authorization": f"Bearer {self.state['access_token']}"},
    json={"owner_id": self.state["user_id"]},
)

await self.http.graphql(
    operation_name="CreateObject",
    query="mutation CreateObject($title: String!) { createObject(title: $title) { id } }",
    variables={"title": f"Object for {self.persona.person.username}"},
)
```

### Databases

For database-only smoke or load tests, configure `run.database`:

```yaml
scenario:
  path: scenario.py
  user_class: DatabaseSmokeUser
run:
  database:
    driver: sqlite
    dsn: ./load-test.sqlite
  users: 5
  spawn_rate: 2
  max_duration_seconds: 30
data:
  pool_size: 5
  seed: 42
  source: memory
profile:
  type: soak
  duration_seconds: 10
safety:
  allowed_hosts: []
```

The injected `self.db` client records SQL metrics. Pass stable names so reports
and SLOs stay low-cardinality:

```python
await self.db.execute(
    "INSERT INTO users (email) VALUES (?)",
    parameters=(self.persona.contact.email,),
    name="DB insert synthetic user",
)

result = await self.db.query(
    "SELECT email FROM users WHERE email = ?",
    parameters=(self.persona.contact.email,),
    name="DB select synthetic user",
)
```

Enable auto-cleanup explicitly when raw POST/INSERT calls or generated model
workloads create state:

```yaml
cleanup:
  enabled: true
  http:
    targets:
      - method: POST
        path: /objects
        delete_path: /objects/{id}
        id_fields: [id, data.id]
  database:
    strategy: delete  # delete or rollback
    tables:
      - users
```

HTTP cleanup uses `DELETE` from the response `Location` header or configured ID
fields. Database cleanup can either delete configured inserted rows or roll back
tracked DB inserts on the same per-user connection. Cleanup attempts and
failures are written separately in JSON reports and do not count toward SLO
metrics.

For PostgreSQL, MySQL, or another DB-API compatible driver, install the driver
in your test environment and construct a `DatabaseClient` in the scenario from
an environment variable. Never hard-code database passwords or production
connection strings in scenario files or YAML.

### WebSocket and gRPC

HTTP and configured SQLite database targets are injected automatically.
WebSocket and gRPC adapters are imported when a scenario needs them.

```python
from veriload.protocols.websocket import WebSocketClient

socket = WebSocketClient(
    base_url="wss://api.example.test",
    events=self.events,
    segment=self.persona_segment,
    persona_id=self.persona.persona_id,
)
reply = await socket.request_json(
    "/stream",
    name="WS /stream",
    payload={"persona_id": self.persona.persona_id},
)
```

```python
from veriload.protocols.grpc import GrpcClient

grpc = GrpcClient(
    events=self.events,
    segment=self.persona_segment,
    persona_id=self.persona.persona_id,
)
response = await grpc.unary(
    "Inventory/GetItem",
    stub.GetItem,
    request,
    timeout=1.0,
)
```

## CLI Reference

Run `uv run veriload --help` for the live command tree.

| Command | Purpose |
| --- | --- |
| `veriload validate` | Load config, generate personas, run safety checks, and optionally preview personas. |
| `veriload run` | Execute the configured scenario locally, with optional in-process workers. |
| `veriload compare` | Compare a current JSON report against a baseline report. |
| `veriload replay` | Print a dry-run replay plan for one persona from a replay manifest. |
| `veriload dataset` | Preview, validate, explain, or export generated personas. |
| `veriload import` | Generate editable scenario skeletons from cURL, OpenAPI, HAR, or Postman. |
| `veriload template` | Generate CLI-first scenario templates. |
| `veriload distributed` | Run networked controller and worker processes. |
| `veriload k8s` | Generate static Kubernetes run manifests or operator install manifests. |
| `veriload operator` | Run the Kopf operator or collect compact artifact summaries. |

### Validate and Dataset Tools

```bash
uv run veriload validate --config veriload.yaml --show-personas 5
uv run veriload dataset preview --config veriload.yaml --limit 10
uv run veriload dataset validate --config veriload.yaml
uv run veriload dataset explain contact.email --config veriload.yaml
uv run veriload dataset export --config veriload.yaml --output personas.jsonl
```

Use these before sending traffic. They make persona generation and safety
constraints visible enough to catch bad seeds, duplicate data, or unsafe contact
details early.

### Run and Compare

```bash
uv run veriload run --config veriload.yaml
uv run veriload run --config veriload.yaml --workers 4

uv run veriload compare baseline.json current.json \
  --max-p95-regression-ms 25 \
  --max-error-rate-regression 0.005
```

When SLOs are configured, `veriload run` exits nonzero if a gate fails. By
default, any p95 latency increase or error-rate increase is considered a
regression; pass tolerances when you want a controlled band.

### Reports and Replay

Configure report paths under `reports`:

```yaml
reports:
  json: reports/run.json
  junit: reports/slo.junit.xml
  trace: reports/events.jsonl
  replay: reports/replay.json
```

The JSON report contains the aggregate summary, SLO result, and worker
metadata. The trace report is JSONL request events. The replay manifest lets you
inspect one persona's dry-run plan:

```bash
uv run veriload replay reports/replay.json --persona-id p-0001
```

## Kubernetes

VeriLoad has two Kubernetes paths:

| Path | Use when |
| --- | --- |
| Static manifest | You want generated YAML for one controller/worker run and will apply it with your own tooling. |
| Kubernetes Operator | You want users to submit `VeriLoadRun` resources and let the operator reconcile jobs, services, tokens, artifacts, status, and cleanup. |

### Static Manifest

```bash
uv run veriload k8s manifest \
  --config veriload.yaml \
  --output k8s/veriload.yaml \
  --image ghcr.io/acme/veriload-scenarios:latest \
  --workers 4 \
  --networked
```

The generated YAML includes a ConfigMap plus controller and worker Jobs. The
image must contain VeriLoad, your scenario files, and scenario dependencies.

### Kubernetes Operator

Install the CRD, RBAC, ServiceAccount, and Deployment:

```bash
uv run veriload k8s operator-manifest \
  --output k8s/veriload-operator.yaml \
  --image ghcr.io/acme/veriload-operator:latest
kubectl apply -f k8s/veriload-operator.yaml
```

Or install the Helm chart:

```bash
helm install veriload-operator charts/veriload-operator \
  --namespace veriload-system \
  --create-namespace \
  --set image.repository=ghcr.io/acme/veriload-operator \
  --set image.tag=latest
```

Then submit a `VeriLoadRun`:

```yaml
apiVersion: veriload.io/v1alpha1
kind: VeriLoadRun
metadata:
  name: checkout-load
  namespace: loads
spec:
  image: ghcr.io/acme/veriload-scenarios:latest
  config:
    inline: |
      scenario:
        path: /workspace/scenario.py
        user_class: CheckoutOperatorUser
      run:
        base_url: https://api.example.test
        users: 100
        spawn_rate: 10
        max_duration_seconds: 300
      data:
        pool_size: 100
        seed: 42
        source: verisim
      profile:
        type: soak
        duration_seconds: 300
      safety:
        allowed_hosts:
          - api.example.test
  files:
    configMapRef:
      name: checkout-load-files
  execution:
    workers: 4
    controllerPort: 5557
    readyTimeoutSeconds: 60
  artifacts:
    enabled: true
    retain: true
    storage:
      mode: pvc
      size: 1Gi
  cleanupPolicy: DeleteOnSuccess
  ttlSecondsAfterFinished: 3600
```

Each run creates a controller Service, controller Job, worker Job, optional
inline config ConfigMap, cluster token Secret, optional artifact PVC, and
collector Job. Controller reports are written under `/reports/<run-name>/`.
The collector command reads the JSON report and patches a compact status
summary:

```bash
uv run veriload operator collect --report-json /reports/<run-name>/run.json
```

The generated Deployment runs `veriload operator run`. The operator uses owner
references for ordinary garbage collection and cleanup policies for terminal
runs:

| Policy | Behavior |
| --- | --- |
| `DeleteOnSuccess` | Delete workloads after success, retain them after failure. |
| `DeleteAlways` | Delete workloads after any terminal phase. |
| `Retain` | Keep workloads until the `VeriLoadRun` is deleted. |

Artifact PVCs are retained by default unless `artifacts.retain: false`.

## Distributed Runs

For quick local sharding, use in-process workers:

```bash
uv run veriload run --config veriload.yaml --workers 4
```

For true networked runs, start one controller and one or more workers. Workers
authenticate with a shared token, download the run bundle from the controller,
receive deterministic persona partitions, and stream results back for one
aggregate report.

```bash
export VERILOAD_CLUSTER_TOKEN="$(openssl rand -hex 32)"

uv run veriload distributed controller \
  --config veriload.yaml \
  --bind-host 0.0.0.0 \
  --bind-port 5557 \
  --expect-workers 4 \
  --cluster-token-env VERILOAD_CLUSTER_TOKEN

uv run veriload distributed worker \
  --controller-host controller.example.internal \
  --controller-port 5557 \
  --work-dir .veriload/worker \
  --cluster-token-env VERILOAD_CLUSTER_TOKEN
```

## Configuration Reference

The default config file is `veriload.yaml`. Unknown keys are rejected so typos
do not silently change a run.

```yaml
scenario:
  path: scenario.py
  user_class: HttpBinSmokeUser
run:
  base_url: "https://api.example.test"
  users: 10
  spawn_rate: 2
  max_duration_seconds: 60
data:
  pool_size: 20
  seed: 42
  source: memory
  locales:
    - locale: en_US
      weight: 1
profile:
  type: soak
  duration_seconds: 30
  tick_seconds: 1
safety:
  allowed_hosts:
    - api.example.test
  max_users: 25
  max_rps: 10
  require_non_routable_contacts: true
slo:
  global:
    max_p95_ms: 750
    max_error_rate: 0.01
  endpoints:
    - name: GET /health
      max_p95_ms: 100
      max_error_rate: 0
reports:
  json: reports/run.json
  junit: reports/slo.junit.xml
  trace: reports/events.jsonl
  replay: reports/replay.json
```

### Required Sections

| Section | Key fields |
| --- | --- |
| `scenario` | `path`, `user_class` for runnable scenarios. Dataset-only commands can omit it. |
| `run` | `base_url` or `database`, plus `users`, `spawn_rate`, and `max_duration_seconds`. |
| `data` | `pool_size`, `seed`, optional `source`, `locales`, and `conflict_mode`. |
| `profile` | `type`, `duration_seconds`, optional `tick_seconds`, `hold_seconds`, and `target_users`. |
| `safety` | `allowed_hosts`, optional caps, stop file, and non-routable contact validation. |
| `slo` | Optional global and endpoint latency/error-rate gates. |
| `reports` | Optional JSON, JUnit, trace, and replay output paths. |

Runtime semantics:

- Configure either `run.base_url`, `run.database`, or both.
- `run.users` is the source of truth for target concurrency.
- `profile.target_users` is a legacy mirror; if supplied, it must match
  `run.users`.
- `run.spawn_rate` gates user starts for profiles with `tick_seconds > 0`.
- `tick_seconds: 0` is useful for smoke tests because it starts target users
  immediately and exits after the first profile tick.

Environment variables with the `VERILOAD_` prefix override config keys. Use
double underscores for nesting:

```bash
VERILOAD_RUN__USERS=25 \
VERILOAD_RUN__SPAWN_RATE=5 \
uv run veriload validate --config veriload.yaml
```

## Safety Checklist

- Target staging, test, or ephemeral environments.
- Keep `safety.allowed_hosts` tight.
- Use persona-backed IDs, emails, companies, and segments instead of shared
  constants.
- Name requests by logical operation, not high-cardinality literal values.
- Call `raise_for_status()` when HTTP failures should fail the task.
- Cleanup created state in `on_stop()` or through fixture cleanup.
- Keep generated contacts non-routable unless you intentionally relax the
  safety setting.
- Validate and preview personas before the first real run.

## Development Gates

Run the same checks used by CI before opening a pull request:

```bash
uv sync --extra dev --frozen
uv run ruff check .
uv run mypy
uv run pytest --cov=veriload --cov-report=term-missing
uv build
```

Coverage is enforced at 80% minimum through `pyproject.toml`. Tagged releases
(`v*`) build source and wheel artifacts through GitHub Actions.
