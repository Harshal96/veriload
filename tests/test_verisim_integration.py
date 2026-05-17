import sys
import types

from veriload.config import VeriLoadConfig
from veriload.data import VerisimPersonaSource
from veriload.runtime import build_persona_pool


def test_verisim_persona_source_maps_verisim_record(monkeypatch) -> None:
    fake_module = _fake_verisim_module()
    monkeypatch.setitem(sys.modules, "verisim", fake_module)

    source = VerisimPersonaSource()
    persona = source.generate_persona(index=0, seed=123, locale="en_US")

    assert persona.person.name == "Brooke Garcia"
    assert persona.person.username == "brooke.garcia"
    assert persona.contact.email == "brooke.garcia@example.invalid"
    assert persona.contact.phone_e164 == "+14155550000"
    assert persona.job.industry == "Healthcare Technology"
    assert persona.company.id == "kindred-medical-group"


def test_build_persona_pool_uses_verisim_source_by_default(monkeypatch) -> None:
    fake_module = _fake_verisim_module()
    monkeypatch.setitem(sys.modules, "verisim", fake_module)
    config = VeriLoadConfig(
        run={
            "base_url": "https://api.example.test",
            "users": 1,
            "spawn_rate": 1,
            "max_duration_seconds": 1,
        },
        data={"pool_size": 1, "seed": 123},
        profile={"type": "soak", "target_users": 1, "duration_seconds": 1},
        safety={"allowed_hosts": ["api.example.test"]},
    )

    pool = build_persona_pool(config)

    assert pool.personas[0].contact.email.endswith("example.invalid")
    assert fake_module.created_with == [{"locale": "en_US", "seed": 123}]


def _fake_verisim_module():
    module = types.ModuleType("verisim")

    class PersonRecord:
        pass

    class Verisim:
        def __init__(self, *, locale: str, seed: int, **kwargs):
            module.created_with.append({"locale": locale, "seed": seed})

        def generate(self, model):
            assert model is PersonRecord
            return types.SimpleNamespace(
                person=types.SimpleNamespace(name="Brooke Garcia", username="brooke.garcia"),
                contact=types.SimpleNamespace(
                    email="brooke.garcia@example.invalid",
                    phone=types.SimpleNamespace(e164="+14155550000"),
                ),
                job=types.SimpleNamespace(
                    title="Product Manager",
                    industry="Healthcare Technology",
                ),
                company=types.SimpleNamespace(
                    id="kindred-medical-group",
                    name="Kindred Medical Group",
                    industry="Healthcare Technology",
                ),
            )

    module.PersonRecord = PersonRecord
    module.Verisim = Verisim
    module.created_with = []
    return module
