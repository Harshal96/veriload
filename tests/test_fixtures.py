from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from veriload.fixtures import (
    DjangoORMConnector,
    ModelFixtures,
    OpenAPIConnector,
    SQLModelConnector,
)
from veriload.fixtures.core import FixtureContext, FixtureError, FixtureRecord, ModelConnector
from veriload.fixtures.sqlmodel import (
    _coerce_identity as _coerce_sqlmodel_identity,
)
from veriload.fixtures.sqlmodel import (
    _identity_from_obj as _sqlmodel_identity_from_obj,
)
from veriload.fixtures.sqlmodel import (
    _model_columns as _sqlmodel_model_columns,
)
from veriload.fixtures.sqlmodel import (
    _python_type as _sqlmodel_python_type,
)
from veriload.fixtures.sqlmodel import (
    _session_scope as _sqlmodel_session_scope,
)


class RecordingConnector(ModelConnector):
    def __init__(self) -> None:
        self.created: list[dict[str, Any]] = []
        self.deleted: list[str] = []
        self._next_id = 1

    def supports(self, model: object) -> bool:
        return model in {FakeModel, FakeChild}

    async def create(self, model: object, context: FixtureContext) -> FixtureRecord:
        values = context.values_for(
            ("email", "company_name", "external_id"),
            overrides=context.overrides,
        )
        created = {**values, "id": f"fixture-{self._next_id}"}
        self._next_id += 1
        self.created.append(created)
        return FixtureRecord(
            connector=self,
            target=model,
            identity=str(created["id"]),
            obj=created,
        )

    async def delete(self, record: FixtureRecord, context: FixtureContext) -> None:
        self.deleted.append(record.identity)


class FakeModel:
    pass


class FakeChild:
    pass


@pytest.mark.asyncio
async def test_model_fixtures_generates_persona_values_and_cleans_reverse_order(
    sample_persona,
) -> None:
    connector = RecordingConnector()
    fixtures = ModelFixtures(
        persona=sample_persona,
        run_id="run-1",
        worker_index=2,
        connectors=(connector,),
    )

    first = await fixtures.create(FakeModel, overrides={"external_id": "manual-id"})
    second = await fixtures.create(FakeChild)
    await fixtures.cleanup()
    await fixtures.cleanup()

    assert first["email"] == "ada@example.invalid"
    assert first["company_name"] == "Analytical Engines"
    assert first["external_id"] == "manual-id"
    assert second["id"] == "fixture-2"
    assert connector.deleted == ["fixture-2", "fixture-1"]


def test_model_fixtures_can_return_copy_with_additional_connectors(sample_persona) -> None:
    first = RecordingConnector()
    second = RecordingConnector()
    fixtures = ModelFixtures(
        persona=sample_persona,
        run_id="run-1",
        worker_index=2,
        connectors=(first,),
    )

    updated = fixtures.with_connectors(second)

    assert updated.persona is sample_persona
    assert updated.run_id == "run-1"
    assert updated.worker_index == 2
    assert updated.records == ()


def test_model_fixtures_raise_when_no_connector_supports_model(sample_persona) -> None:
    fixtures = ModelFixtures(
        persona=sample_persona,
        run_id="run-1",
        connectors=(RecordingConnector(),),
    )

    with pytest.raises(FixtureError, match="No fixture connector supports"):
        fixtures._connector_for(object())


def test_fixture_context_maps_common_persona_aliases_and_typed_fallbacks(sample_persona) -> None:
    context = FixtureContext(
        persona=sample_persona,
        run_id="run-1",
        worker_index=7,
        overrides={},
    )

    values = context.values_for(
        (
            "contact_email",
            "username",
            "phone_number",
            "company_id",
            "job_title",
            "industry",
            "locale",
            "veriload_run_id",
            "veriload_worker_index",
            "score",
            "ratio",
            "enabled",
            "blob",
            "unmapped",
        ),
        type_hints={
            "score": int,
            "ratio": float,
            "enabled": bool,
            "blob": bytes,
        },
    )

    expected_score = sum(ord(char) for char in "run-1:p1:score") + 7
    expected_ratio = float(sum(ord(char) for char in "run-1:p1:ratio") + 7)
    assert values == {
        "contact_email": "ada@example.invalid",
        "username": "ada",
        "phone_number": "+15550000001",
        "company_id": "company-1",
        "job_title": "Principal Engineer",
        "industry": "Computing",
        "locale": "en_US",
        "veriload_run_id": "run-1",
        "veriload_worker_index": 7,
        "score": expected_score,
        "ratio": expected_ratio,
        "enabled": True,
        "blob": b"run-1:p1:blob",
        "unmapped": "run-1-p1-unmapped",
    }


class FakeSqlColumn:
    def __init__(
        self,
        name: str,
        *,
        primary_key: bool = False,
        default: object | None = None,
        nullable: bool = False,
        python_type: type = str,
    ) -> None:
        self.name = name
        self.key = name
        self.primary_key = primary_key
        self.default = default
        self.nullable = nullable
        self.type = FakeSqlType(python_type)


class FakeSqlType:
    def __init__(self, python_type: type) -> None:
        self.python_type = python_type


class FakeSqlTable:
    def __init__(self, columns: tuple[FakeSqlColumn, ...]) -> None:
        self.columns = columns
        self.primary_key = FakeSqlPrimaryKey(tuple(column for column in columns if column.primary_key))


class FakeSqlPrimaryKey:
    def __init__(self, columns: tuple[FakeSqlColumn, ...]) -> None:
        self.columns = columns


class FakeSqlModel:
    __table__ = FakeSqlTable(
        (
            FakeSqlColumn("id", primary_key=True, python_type=int),
            FakeSqlColumn("email"),
            FakeSqlColumn("name"),
            FakeSqlColumn("company_name"),
            FakeSqlColumn("run_id"),
        )
    )

    def __init__(self, **kwargs: Any) -> None:
        for key, value in kwargs.items():
            setattr(self, key, value)
        self.id = kwargs.get("id")


class FakeSqlModelSession:
    def __init__(self) -> None:
        self.added: list[FakeSqlModel] = []
        self.commits = 0
        self.deleted_ids: list[int] = []
        self._objects: dict[int, FakeSqlModel] = {}

    def __enter__(self) -> FakeSqlModelSession:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def add(self, obj: FakeSqlModel) -> None:
        self.added.append(obj)

    def commit(self) -> None:
        self.commits += 1

    def refresh(self, obj: FakeSqlModel) -> None:
        obj.id = 101
        self._objects[101] = obj

    def get(self, model: object, identity: object) -> FakeSqlModel | None:
        return self._objects.get(int(identity))

    def delete(self, obj: FakeSqlModel) -> None:
        self.deleted_ids.append(obj.id)


class FakeSqlModelSessionWithoutRefresh:
    def __init__(self) -> None:
        self.added: list[object] = []
        self.deleted: list[object] = []
        self.commits = 0
        self.closed = False

    def add(self, obj: object) -> None:
        self.added.append(obj)

    def commit(self) -> None:
        self.commits += 1

    def delete(self, obj: object) -> None:
        self.deleted.append(obj)

    def close(self) -> None:
        self.closed = True


class FakeJsonSchemaField:
    def __init__(
        self,
        *,
        default: object | None = None,
        annotation: type | None = None,
        primary_key: bool = False,
    ) -> None:
        self.default = default
        self.annotation = annotation
        self.json_schema_extra = {"primary_key": primary_key}


class FakePydanticStyleSqlModel:
    model_fields = {
        "id": FakeJsonSchemaField(annotation=str, primary_key=True),
        "email_address": FakeJsonSchemaField(annotation=str),
        "age": FakeJsonSchemaField(default=18, annotation=int),
    }

    def __init__(self, **kwargs: Any) -> None:
        self.id = kwargs.get("id", "pydantic-id")
        for key, value in kwargs.items():
            setattr(self, key, value)


class FakeNoPrimaryKeySqlModel:
    __table__ = FakeSqlTable(
        (
            FakeSqlColumn("id", python_type=str),
            FakeSqlColumn("email", default="from-db"),
        )
    )

    def __init__(self, **kwargs: Any) -> None:
        for key, value in kwargs.items():
            setattr(self, key, value)
        self.id = kwargs.get("id", "fallback-id")


@pytest.mark.asyncio
async def test_sqlmodel_connector_uses_session_add_commit_refresh_and_cleanup(
    sample_persona,
) -> None:
    session = FakeSqlModelSession()
    connector = SQLModelConnector(session_factory=lambda: session)
    fixtures = ModelFixtures(
        persona=sample_persona,
        run_id="run-1",
        worker_index=0,
        connectors=(connector,),
    )

    created = await fixtures.create(FakeSqlModel)
    await fixtures.cleanup()

    assert created.email == "ada@example.invalid"
    assert created.name == "Ada Lovelace"
    assert created.company_name == "Analytical Engines"
    assert created.run_id == "run-1"
    assert created.id == 101
    assert session.deleted_ids == [101]
    assert session.commits == 2


@pytest.mark.asyncio
async def test_sqlmodel_connector_supports_model_fields_and_sessions_without_context(
    sample_persona,
) -> None:
    session = FakeSqlModelSessionWithoutRefresh()
    connector = SQLModelConnector(session_factory=lambda: session)
    fixtures = ModelFixtures(
        persona=sample_persona,
        run_id="run-1",
        worker_index=0,
        connectors=(connector,),
    )

    created = await fixtures.create(FakePydanticStyleSqlModel)
    await fixtures.cleanup()

    assert connector.supports(FakePydanticStyleSqlModel)
    assert created.email_address == "ada@example.invalid"
    assert not hasattr(created, "age")
    assert created.id == "pydantic-id"
    assert session.deleted == [created]
    assert session.commits == 2
    assert session.closed


@pytest.mark.asyncio
async def test_sqlmodel_connector_skips_cleanup_when_row_is_missing(sample_persona) -> None:
    session = FakeSqlModelSession()
    connector = SQLModelConnector(session_factory=lambda: session)
    record = FixtureRecord(
        connector=connector,
        target=FakeSqlModel,
        identity="404",
        obj=FakeSqlModel(id=404),
        metadata={"primary_keys": ("id",)},
    )
    context = FixtureContext(
        persona=sample_persona,
        run_id="run-1",
        worker_index=0,
        overrides={},
    )

    await connector.delete(record, context)

    assert session.deleted_ids == []
    assert session.commits == 0


@pytest.mark.asyncio
async def test_sqlmodel_connector_uses_id_when_model_has_no_primary_key_metadata(
    sample_persona,
) -> None:
    session = FakeSqlModelSessionWithoutRefresh()
    connector = SQLModelConnector(session_factory=lambda: session)
    fixtures = ModelFixtures(
        persona=sample_persona,
        run_id="run-1",
        connectors=(connector,),
    )

    created = await fixtures.create(FakeNoPrimaryKeySqlModel)

    assert created.id == "run-1-p1-id"
    assert fixtures.records[0].identity == "run-1-p1-id"


def test_sqlmodel_helper_edges_are_deterministic() -> None:
    class NoModelFields:
        model_fields = ("not", "a", "dict")

    class NoColumnType:
        pass

    class BrokenPythonType:
        @property
        def python_type(self) -> type:
            raise RuntimeError("unreadable")

    class BrokenTypeColumn:
        type = BrokenPythonType()

    class NonTypePythonType:
        python_type = "str"

    class NonTypeColumn:
        type = NonTypePythonType()

    obj = type("SqlObject", (), {"id": "abc"})()
    session = object()
    scope = _sqlmodel_session_scope(session)

    assert _sqlmodel_model_columns(NoModelFields) == ()
    assert _sqlmodel_python_type(NoColumnType()) is None
    assert _sqlmodel_python_type(BrokenTypeColumn()) is None
    assert _sqlmodel_python_type(NonTypeColumn()) is None
    assert _sqlmodel_identity_from_obj(obj, ()) == "abc"
    assert _coerce_sqlmodel_identity("abc") == "abc"
    assert scope.__enter__() is session
    assert scope.__exit__(None, None, None) is None


class FakeDjangoField:
    def __init__(
        self,
        name: str,
        *,
        primary_key: bool = False,
        auto_created: bool = False,
        concrete: bool = True,
        default: object = None,
    ) -> None:
        self.name = name
        self.attname = name
        self.primary_key = primary_key
        self.auto_created = auto_created
        self.concrete = concrete
        self.default = default


class FakeDjangoMeta:
    def __init__(self, fields: tuple[FakeDjangoField, ...], pk: FakeDjangoField) -> None:
        self.fields = fields
        self.pk = pk


@dataclass
class FakeDjangoModel:
    id: int | None = None
    email: str = ""
    company_name: str = ""
    run_id: str = ""


FakeDjangoModel._meta = FakeDjangoMeta(
    fields=(
        FakeDjangoField("id", primary_key=True, auto_created=True),
        FakeDjangoField("email"),
        FakeDjangoField("company_name"),
        FakeDjangoField("run_id"),
    ),
    pk=FakeDjangoField("id", primary_key=True, auto_created=True),
)


class FakeDjangoQuerySet:
    def __init__(self, manager: FakeDjangoManager, ids: tuple[int, ...]) -> None:
        self.manager = manager
        self.ids = ids

    def delete(self) -> None:
        self.manager.deleted_ids.extend(self.ids)


class FakeDjangoManager:
    def __init__(self) -> None:
        self.created: list[FakeDjangoModel] = []
        self.deleted_ids: list[int] = []

    def create(self, **kwargs: Any) -> FakeDjangoModel:
        obj = FakeDjangoModel(id=202, **kwargs)
        self.created.append(obj)
        return obj

    def filter(self, **kwargs: Any) -> FakeDjangoQuerySet:
        return FakeDjangoQuerySet(self, tuple(kwargs["pk__in"]))


class FakeDjangoStringManager(FakeDjangoManager):
    def create(self, **kwargs: Any) -> object:
        obj = type("CreatedDjangoObject", (), {})()
        for key, value in kwargs.items():
            setattr(obj, key, value)
        obj.id = "customer-alpha"
        self.created.append(obj)
        return obj


@pytest.mark.asyncio
async def test_django_connector_uses_manager_create_and_queryset_delete(
    sample_persona,
) -> None:
    manager = FakeDjangoManager()
    FakeDjangoModel.objects = manager
    connector = DjangoORMConnector()
    fixtures = ModelFixtures(
        persona=sample_persona,
        run_id="run-1",
        worker_index=0,
        connectors=(connector,),
    )

    created = await fixtures.create(FakeDjangoModel)
    await fixtures.cleanup()

    assert created.email == "ada@example.invalid"
    assert created.company_name == "Analytical Engines"
    assert created.run_id == "run-1"
    assert manager.deleted_ids == [202]


@pytest.mark.asyncio
async def test_django_connector_filters_non_create_fields_and_preserves_string_ids(
    sample_persona,
) -> None:
    class AppSpecificModel(FakeDjangoModel):
        pass

    AppSpecificModel._meta = FakeDjangoMeta(
        fields=(
            FakeDjangoField("id", primary_key=True),
            FakeDjangoField("computed", concrete=False),
            FakeDjangoField("created_at", auto_created=True),
            FakeDjangoField("email_address"),
        ),
        pk=FakeDjangoField("id", primary_key=True),
    )
    manager = FakeDjangoStringManager()
    AppSpecificModel.objects = manager
    connector = DjangoORMConnector()
    fixtures = ModelFixtures(
        persona=sample_persona,
        run_id="run-1",
        connectors=(connector,),
    )

    created = await fixtures.create(AppSpecificModel)
    await fixtures.cleanup()

    assert created.email_address == "ada@example.invalid"
    assert not hasattr(manager.created[0], "computed")
    assert not hasattr(manager.created[0], "created_at")
    assert manager.created[0].id == "customer-alpha"
    assert manager.deleted_ids == ["customer-alpha"]


class RecordingHttpClient:
    def __init__(self, *, post_response: dict[str, object]) -> None:
        self.post_response = post_response
        self.requests: list[dict[str, object]] = []

    async def request(self, method: str, path: str, **kwargs: Any) -> object:
        request = {"method": method, "path": path, **kwargs}
        self.requests.append(request)
        return FakeHttpResponse(self.post_response)


class DictResponseHttpClient(RecordingHttpClient):
    async def request(self, method: str, path: str, **kwargs: Any) -> object:
        request = {"method": method, "path": path, **kwargs}
        self.requests.append(request)
        return self.post_response


class EmptyResponseHttpClient(RecordingHttpClient):
    async def request(self, method: str, path: str, **kwargs: Any) -> object:
        request = {"method": method, "path": path, **kwargs}
        self.requests.append(request)
        return object()


class FakeHttpResponse:
    def __init__(self, body: dict[str, object]) -> None:
        self._body = body

    def json(self) -> dict[str, object]:
        return self._body


OPENAPI_SPEC = {
    "openapi": "3.1.0",
    "paths": {
        "/customers": {
            "post": {
                "operationId": "createCustomer",
                "requestBody": {
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "required": ["email"],
                                "properties": {
                                    "email": {"type": "string"},
                                    "name": {"type": "string"},
                                    "company_name": {"type": "string"},
                                },
                            }
                        }
                    }
                },
                "responses": {"201": {"description": "Created"}},
            }
        },
        "/customers/{customerId}": {
            "delete": {
                "operationId": "deleteCustomer",
                "parameters": [
                    {
                        "name": "customerId",
                        "in": "path",
                        "required": True,
                        "schema": {"type": "string"},
                    }
                ],
                "responses": {"204": {"description": "Deleted"}},
            }
        },
    },
}

OPENAPI_SPEC_WITH_CAMEL_ID = {
    "openapi": "3.1.0",
    "paths": {
        "/customers": {
            "post": {
                "operationId": "createCustomer",
                "requestBody": {
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "properties": {
                                    "email": {"type": "string"},
                                    "company_name": {"type": "string"},
                                },
                            }
                        }
                    }
                },
                "responses": {"201": {"description": "Created"}},
            }
        },
        "/customers/current": {
            "delete": {
                "operationId": "deleteCustomer",
                "responses": {"204": {"description": "Deleted"}},
            }
        },
    },
}

OPENAPI_SPEC_WITH_UNRELATED_FIRST_PATH = {
    "openapi": "3.1.0",
    "paths": {
        "/widgets": {
            "post": {
                "operationId": "createWidget",
                "responses": {"201": {"description": "Created"}},
            }
        },
        **OPENAPI_SPEC["paths"],
    },
}


@pytest.mark.asyncio
async def test_openapi_connector_posts_payload_and_deletes_created_resource(
    sample_persona,
) -> None:
    http = RecordingHttpClient(post_response={"id": "cust-1"})
    connector = OpenAPIConnector.from_dict(OPENAPI_SPEC, http=http, resource="Customer")
    fixtures = ModelFixtures(
        persona=sample_persona,
        run_id="run-1",
        worker_index=0,
        connectors=(connector,),
    )

    created = await fixtures.create("Customer")
    await fixtures.cleanup()

    assert created == {"id": "cust-1"}
    assert http.requests[0]["method"] == "POST"
    assert http.requests[0]["path"] == "/customers"
    assert http.requests[0]["json"]["email"] == "ada@example.invalid"
    assert http.requests[0]["json"]["name"] == "Ada Lovelace"
    assert http.requests[1] == {
        "method": "DELETE",
        "path": "/customers/cust-1",
        "name": "DELETE /customers/{customerId}",
    }


@pytest.mark.asyncio
async def test_openapi_connector_loads_spec_file_and_uses_resource_specific_id(
    sample_persona,
    tmp_path,
) -> None:
    spec_path = tmp_path / "openapi.yaml"
    spec_path.write_text(
        """
openapi: 3.1.0
paths:
  /customers:
    post:
      operationId: createCustomer
      requestBody:
        content:
          application/json:
            schema:
              type: object
              properties:
                email:
                  type: string
      responses:
        "201":
          description: Created
  /customers/{customerId}:
    delete:
      operationId: deleteCustomer
      responses:
        "204":
          description: Deleted
""",
        encoding="utf-8",
    )
    http = RecordingHttpClient(post_response={"customerId": "cust-2"})
    connector = OpenAPIConnector.from_file(spec_path, http=http, resource="Customer")
    fixtures = ModelFixtures(
        persona=sample_persona,
        run_id="run-1",
        connectors=(connector,),
    )

    created = await fixtures.create("Customer")
    await fixtures.cleanup()

    assert connector.supports(type("Customer", (), {}))
    assert created == {"customerId": "cust-2"}
    assert http.requests[1]["path"] == "/customers/cust-2"


def test_openapi_connector_skips_unrelated_paths_before_matching_resource() -> None:
    connector = OpenAPIConnector.from_dict(
        OPENAPI_SPEC_WITH_UNRELATED_FIRST_PATH,
        http=RecordingHttpClient(post_response={"id": "cust-1"}),
        resource="Customer",
    )

    assert connector.supports("Customer")


@pytest.mark.asyncio
async def test_openapi_connector_accepts_dict_responses_and_static_delete_paths(
    sample_persona,
) -> None:
    http = DictResponseHttpClient(post_response={"customerId": "cust-3"})
    connector = OpenAPIConnector.from_dict(
        OPENAPI_SPEC_WITH_CAMEL_ID,
        http=http,
        resource="Customer",
    )
    fixtures = ModelFixtures(
        persona=sample_persona,
        run_id="run-1",
        connectors=(connector,),
    )

    created = await fixtures.create("Customer")
    await fixtures.cleanup()

    assert created == {"customerId": "cust-3"}
    assert http.requests[0]["json"] == {
        "email": "ada@example.invalid",
        "company_name": "Analytical Engines",
    }
    assert http.requests[1]["path"] == "/customers/current"


@pytest.mark.asyncio
async def test_openapi_connector_treats_non_json_responses_as_empty_body(
    sample_persona,
) -> None:
    http = EmptyResponseHttpClient(post_response={})
    connector = OpenAPIConnector.from_dict(
        OPENAPI_SPEC_WITH_CAMEL_ID,
        http=http,
        resource="Customer",
    )
    fixtures = ModelFixtures(
        persona=sample_persona,
        run_id="run-1",
        connectors=(connector,),
    )

    created = await fixtures.create("Customer")
    await fixtures.cleanup()

    assert created == {}
    assert http.requests[1]["path"] == "/customers/current"


def test_openapi_connector_rejects_specs_without_cleanup_safe_lifecycle() -> None:
    with pytest.raises(ValueError, match="no POST create operation"):
        OpenAPIConnector.from_dict({"paths": {}}, http=object(), resource="Customer")

    with pytest.raises(ValueError, match="no DELETE cleanup operation"):
        OpenAPIConnector.from_dict(
            {
                "paths": {
                    "/customers": {
                        "post": {
                            "operationId": "createCustomer",
                            "responses": {"201": {"description": "Created"}},
                        }
                    }
                }
            },
            http=object(),
            resource="Customer",
        )

    with pytest.raises(ValueError, match="no POST create operation"):
        OpenAPIConnector.from_dict({"paths": []}, http=object(), resource="Customer")


@pytest.mark.asyncio
async def test_openapi_connector_ignores_incomplete_request_body_shapes(sample_persona) -> None:
    incomplete_operations = (
        {},
        {"requestBody": {}},
        {"requestBody": {"content": {}}},
        {"requestBody": {"content": {"application/json": {}}}},
        {"requestBody": {"content": {"application/json": {"schema": []}}}},
        {"requestBody": {"content": {"application/json": {"schema": {"type": "object"}}}}},
    )

    for operation in incomplete_operations:
        spec = {
            "paths": {
                "/customers": {
                    "post": {
                        "operationId": "createCustomer",
                        **operation,
                    }
                },
                "/customers/{customerId}": {
                    "delete": {
                        "operationId": "deleteCustomer",
                    }
                },
            }
        }
        http = RecordingHttpClient(post_response={"id": "cust-1"})
        connector = OpenAPIConnector.from_dict(
            spec,
            http=http,
            resource="Customer",
        )
        fixtures = ModelFixtures(
            persona=sample_persona,
            run_id="run-1",
            connectors=(connector,),
        )

        await fixtures.create("Customer")

        assert http.requests[0]["json"] == {}


def test_fixture_connectors_are_publicly_importable() -> None:
    assert ModelFixtures is not None
    assert SQLModelConnector is not None
    assert DjangoORMConnector is not None
    assert OpenAPIConnector is not None


def test_fixture_facade_is_available_from_top_level_package() -> None:
    from veriload import ModelFixtures as PublicModelFixtures

    assert PublicModelFixtures is ModelFixtures
