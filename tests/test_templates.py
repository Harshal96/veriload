from pathlib import Path

from veriload.templates import render_auth_lifecycle_template, write_auth_lifecycle_template


def test_render_auth_lifecycle_template_includes_config_and_scenario() -> None:
    template = render_auth_lifecycle_template(
        base_url="https://api.example.test",
        user_class="CheckoutAuthUser",
    )

    assert set(template) == {"README.md", "scenario.py", "veriload.yaml"}
    assert "class CheckoutAuthUser(VeriUser)" in template["scenario.py"]
    assert "register synthetic user" in template["scenario.py"]
    assert 'base_url: "https://api.example.test"' in template["veriload.yaml"]
    assert "allowed_hosts:" in template["veriload.yaml"]
    assert "api.example.test" in template["veriload.yaml"]


def test_write_auth_lifecycle_template_creates_files_without_overwriting(tmp_path: Path) -> None:
    output_dir = tmp_path / "auth"
    write_auth_lifecycle_template(output_dir, base_url="https://api.example.test")
    scenario_path = output_dir / "scenario.py"

    scenario_path.write_text("custom", encoding="utf-8")
    write_auth_lifecycle_template(output_dir, base_url="https://api.example.test")

    assert scenario_path.read_text(encoding="utf-8") == "custom"
    assert (output_dir / "veriload.yaml").exists()
