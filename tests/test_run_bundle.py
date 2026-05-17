from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

import pytest

from veriload.run_bundle import BundleError, create_run_bundle, extract_run_bundle


def test_create_run_bundle_includes_config_and_scenario(tmp_path: Path) -> None:
    config_path = tmp_path / "veriload.yaml"
    scenario_path = tmp_path / "scenario.py"
    config_path.write_text("scenario:\n  path: scenario.py\n", encoding="utf-8")
    scenario_path.write_text("class User:\n    pass\n", encoding="utf-8")

    bundle = create_run_bundle(
        config_path=config_path,
        scenario_path=scenario_path,
        config_dir=tmp_path,
    )
    extracted = extract_run_bundle(
        bundle.archive_bytes,
        tmp_path / "worker",
        expected_sha256=bundle.sha256,
    )

    assert extracted.bundle_dir.joinpath("veriload.yaml").read_text(encoding="utf-8").startswith("scenario:")
    assert extracted.bundle_dir.joinpath("scenario.py").read_text(encoding="utf-8").startswith("class User")
    assert extracted.manifest["scenario_path"] == "scenario.py"


def test_extract_run_bundle_rejects_parent_directory_escape(tmp_path: Path) -> None:
    archive_path = tmp_path / "bad.zip"
    with ZipFile(archive_path, "w", ZIP_DEFLATED) as archive:
        archive.writestr("../escape.py", "x = 1\n")

    with pytest.raises(BundleError, match="unsafe path"):
        extract_run_bundle(archive_path.read_bytes(), tmp_path / "worker")


def test_extract_run_bundle_rejects_symlink_entries(tmp_path: Path) -> None:
    archive_path = tmp_path / "bad.zip"
    info = ZipInfo("scenario.py")
    info.external_attr = 0o120777 << 16
    with ZipFile(archive_path, "w", ZIP_DEFLATED) as archive:
        archive.writestr(info, "target")

    with pytest.raises(BundleError, match="symlink"):
        extract_run_bundle(archive_path.read_bytes(), tmp_path / "worker")
