from pathlib import Path


def test_readme_does_not_contain_internal_networked_design() -> None:
    readme = Path("README.md").read_text(encoding="utf-8")

    assert "Future Design: True Networked Distributed Mode" not in readme
    assert "Implementation Phases" not in readme


def test_readme_documents_model_fixture_cleanup() -> None:
    readme = Path("README.md").read_text(encoding="utf-8")

    assert "Model Fixtures" in readme
    assert "await self.fixtures.create(Customer)" in readme
    assert "await self.fixtures.cleanup()" in readme
    assert "examples/model_fixtures/" in readme
    assert "custom_field_names_scenario.py" in readme
    assert "SQLModel, Django ORM, or OpenAPI" in readme


def test_readme_documents_kubernetes_operator_path() -> None:
    readme = Path("README.md").read_text(encoding="utf-8")

    assert "Kubernetes Operator" in readme
    assert "examples/kubernetes_operator/" in readme
    assert "veriload k8s operator-manifest" in readme
    assert "kind: VeriLoadRun" in readme
    assert "veriload operator collect" in readme


def test_readme_has_productized_entry_points() -> None:
    readme = Path("README.md").read_text(encoding="utf-8")

    assert "Ship load tests that create, exercise, and clean up realistic state" in readme
    assert "Choose Your Path" in readme
    assert "Feature Map" in readme
