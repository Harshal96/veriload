"""Django ORM fixture connector."""

from __future__ import annotations

from typing import Any, cast

from veriload.fixtures.core import FixtureContext, FixtureRecord, ModelConnector


class DjangoORMConnector(ModelConnector):
    """Create and delete Django ORM rows."""

    def supports(self, model: object) -> bool:
        return hasattr(model, "_meta") and hasattr(model, "objects")

    async def create(self, model: object, context: FixtureContext) -> FixtureRecord:
        model_any = cast(Any, model)
        meta = model_any._meta
        fields = tuple(getattr(meta, "fields", ()))
        field_names = tuple(
            _field_attname(field)
            for field in fields
            if _is_create_field(field)
        )
        obj = model_any.objects.create(**context.values_for(field_names))
        identity = str(getattr(obj, _field_attname(meta.pk)))
        return FixtureRecord(
            connector=self,
            target=model,
            identity=identity,
            obj=obj,
            metadata={"pk_name": _field_attname(meta.pk)},
        )

    async def delete(self, record: FixtureRecord, context: FixtureContext) -> None:
        target = cast(Any, record.target)
        target.objects.filter(pk__in=(_coerce_identity(record.identity),)).delete()


def _is_create_field(field: object) -> bool:
    if not bool(getattr(field, "concrete", True)):
        return False
    if bool(getattr(field, "primary_key", False)):
        return False
    if bool(getattr(field, "auto_created", False)):
        return False
    return True


def _field_attname(field: object) -> str:
    field_any = cast(Any, field)
    return str(getattr(field_any, "attname", None) or field_any.name)


def _coerce_identity(identity: str) -> object:
    try:
        return int(identity)
    except ValueError:
        return identity
