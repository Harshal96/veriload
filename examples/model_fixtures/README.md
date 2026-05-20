# Model Fixture Examples

These examples show how to use `self.fixtures` to create persona-backed test
state and delete it in `on_stop()`. They are intentionally small and are not
part of the default runnable examples because SQLModel, Django, and your target
OpenAPI service are optional app-specific dependencies.

## SQLModel

Use [sqlmodel_scenario.py](sqlmodel_scenario.py) when the load test process can
open a direct SQLModel/SQLAlchemy session.

```python
self.fixtures = self.fixtures.with_connectors(
    SQLModelConnector(session_factory=session_factory)
)
self.customer = await self.fixtures.create(Customer)
```

## Django ORM

Use [django_scenario.py](django_scenario.py) inside an environment where Django
is already configured. The connector uses the model's default manager.

```python
self.fixtures = self.fixtures.with_connectors(DjangoORMConnector())
self.customer = await self.fixtures.create(Customer)
```

## OpenAPI Resource Fixtures

Use [openapi_scenario.py](openapi_scenario.py) with an OpenAPI file like
[openapi.yaml](openapi.yaml). The connector creates a resource through `POST`,
records the returned ID, and deletes it through the matching `DELETE` operation.

```python
self.fixtures = self.fixtures.with_connectors(
    OpenAPIConnector.from_file("openapi.yaml", http=self.http, resource="Customer")
)
self.customer = await self.fixtures.create("Customer")
```

## Custom Field Names

Use [custom_field_names_scenario.py](custom_field_names_scenario.py) when your
model names do not match VeriLoad's built-in aliases. Pass explicit values with
`overrides`.

```python
self.customer = await self.fixtures.create(
    Customer,
    overrides={
        "primary_email": self.persona.contact.email,
        "legal_name": self.persona.person.name,
    },
)
```

Cleanup is always explicit:

```python
async def on_stop(self) -> None:
    await self.fixtures.cleanup()
```

## Generated DB Workloads

Use `self.model_workload(...)` when the generated database operation is the
traffic under test rather than setup data:

```python
self.customer_workload = self.model_workload(
    Customer,
    mode="sql",
    overrides={"tenant_id": "load-test"},
)
created = await self.customer_workload.insert()
result = await self.customer_workload.select(created)
```

Enable `cleanup.enabled` in `veriload.yaml` so generated rows are deleted or
rolled back after the user stops.
