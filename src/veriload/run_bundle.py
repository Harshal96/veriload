"""Run-bundle packaging and safe extraction for networked workers."""

from __future__ import annotations

import hashlib
import io
import json
import stat
import time
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from zipfile import ZIP_DEFLATED, BadZipFile, ZipFile, ZipInfo


class BundleError(RuntimeError):
    """Raised when a run bundle is unsafe or invalid."""


@dataclass(frozen=True)
class RunBundle:
    """Content-addressed archive plus its manifest."""

    archive_bytes: bytes
    sha256: str
    manifest: dict[str, object]


@dataclass(frozen=True)
class ExtractedBundle:
    """Extracted bundle metadata."""

    bundle_dir: Path
    manifest: dict[str, object]


def create_run_bundle(
    *,
    config_path: Path,
    scenario_path: Path,
    config_dir: Path,
) -> RunBundle:
    """Create a small ZIP bundle containing the resolved config and scenario file."""

    config_path = config_path.resolve()
    scenario_path = scenario_path.resolve()
    config_dir = config_dir.resolve()
    config_name = "veriload.yaml"
    scenario_name = _relative_bundle_path(scenario_path, config_dir)
    files = (
        (config_name, config_path.read_bytes()),
        (scenario_name, scenario_path.read_bytes()),
    )
    manifest = {
        "created_at": time.time(),
        "config_path": config_name,
        "scenario_path": scenario_name,
        "files": [
            {
                "path": path,
                "size": len(content),
                "sha256": hashlib.sha256(content).hexdigest(),
            }
            for path, content in files
        ],
    }
    archive_io = io.BytesIO()
    with ZipFile(archive_io, "w", ZIP_DEFLATED) as archive:
        archive.writestr("bundle.json", json.dumps(manifest, sort_keys=True))
        for path, content in files:
            archive.writestr(path, content)
    archive_bytes = archive_io.getvalue()
    return RunBundle(
        archive_bytes=archive_bytes,
        sha256=hashlib.sha256(archive_bytes).hexdigest(),
        manifest=manifest,
    )


def extract_run_bundle(
    archive_bytes: bytes,
    destination: Path,
    *,
    expected_sha256: str | None = None,
) -> ExtractedBundle:
    """Safely extract a run bundle and verify its manifest."""

    if expected_sha256 is not None:
        actual_sha256 = hashlib.sha256(archive_bytes).hexdigest()
        if actual_sha256 != expected_sha256:
            raise BundleError("run bundle hash mismatch")
    destination.mkdir(parents=True, exist_ok=True)
    bundle_dir = destination / (expected_sha256 or hashlib.sha256(archive_bytes).hexdigest())
    bundle_dir.mkdir(parents=True, exist_ok=True)
    try:
        with ZipFile(io.BytesIO(archive_bytes)) as archive:
            infos = archive.infolist()
            for info in infos:
                _validate_member(info)
            for info in infos:
                target = bundle_dir / info.filename
                if info.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(archive.read(info))
    except BadZipFile as exc:
        raise BundleError("run bundle is not a valid ZIP archive") from exc
    manifest_path = bundle_dir / "bundle.json"
    if not manifest_path.exists():
        raise BundleError("run bundle missing bundle.json")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    _verify_manifest(bundle_dir, manifest)
    return ExtractedBundle(bundle_dir=bundle_dir, manifest=manifest)


def _relative_bundle_path(path: Path, root: Path) -> str:
    try:
        relative = path.relative_to(root)
    except ValueError:
        return path.name
    return relative.as_posix()


def _validate_member(info: ZipInfo) -> None:
    member_path = PurePosixPath(info.filename)
    if member_path.is_absolute() or ".." in member_path.parts:
        raise BundleError(f"unsafe path in run bundle: {info.filename}")
    mode = (info.external_attr >> 16) & 0o170000
    if mode == stat.S_IFLNK:
        raise BundleError(f"symlink entries are not allowed in run bundles: {info.filename}")


def _verify_manifest(bundle_dir: Path, manifest: object) -> None:
    if not isinstance(manifest, dict):
        raise BundleError("bundle manifest must be a JSON object")
    files = manifest.get("files")
    if not isinstance(files, list):
        raise BundleError("bundle manifest missing files")
    for file_entry in files:
        if not isinstance(file_entry, dict):
            raise BundleError("bundle file entry must be a JSON object")
        path = str(file_entry.get("path", ""))
        _validate_member(ZipInfo(path))
        target = bundle_dir / path
        if not target.exists():
            raise BundleError(f"bundle file missing after extraction: {path}")
        content = target.read_bytes()
        if len(content) != int(file_entry.get("size", -1)):
            raise BundleError(f"bundle file size mismatch: {path}")
        if hashlib.sha256(content).hexdigest() != file_entry.get("sha256"):
            raise BundleError(f"bundle file hash mismatch: {path}")
