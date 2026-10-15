from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path


class BackupError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class RestoreResult:
    manifest_path: Path
    files: int


_SENSITIVE_NAMES = {".env", ".env.local", ".env.production", "auth.json"}
_SENSITIVE_MARKERS = ("/auths/", "/.git/", "/node_modules/")


def create_backup(
    destination: Path,
    *,
    databases: dict[str, Path],
    artifact_dirs: dict[str, Path],
) -> Path:
    if destination.exists():
        raise BackupError(f"backup destination already exists: {destination}")
    if not databases and not artifact_dirs:
        raise BackupError("backup has no sources")

    database_root = destination / "databases"
    artifact_root = destination / "artifacts"
    entries: list[dict[str, str]] = []
    file_count = 0
    try:
        database_root.mkdir(parents=True)
        for name, source in sorted(databases.items()):
            _validate_name(name)
            if not source.is_file():
                raise BackupError(f"database does not exist: {source}")
            target = database_root / f"{name}.sqlite"
            with sqlite3.connect(source) as source_connection, sqlite3.connect(target) as target_connection:
                source_connection.backup(target_connection)
            entries.append(_entry(target, destination, "database"))
            file_count += 1

        for name, source in sorted(artifact_dirs.items()):
            _validate_name(name)
            if not source.is_dir():
                raise BackupError(f"artifact directory does not exist: {source}")
            target_root = artifact_root / name
            for source_file in sorted(path for path in source.rglob("*") if path.is_file()):
                relative = source_file.relative_to(source)
                _validate_path(relative)
                target = target_root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source_file, target)
                entries.append(_entry(target, destination, "artifact"))
                file_count += 1

        manifest = {
            "format": "mrlc-backup-v1",
            "created_at": datetime.now(UTC).isoformat(),
            "files": entries,
        }
        (destination / "manifest.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n"
        )
        return destination
    except Exception:
        shutil.rmtree(destination, ignore_errors=True)
        raise


def restore_backup(backup: Path, destination: Path) -> RestoreResult:
    manifest_path = backup / "manifest.json"
    if not manifest_path.is_file():
        raise BackupError(f"backup manifest is missing: {manifest_path}")
    if destination.exists():
        raise BackupError(f"restore destination already exists: {destination}")
    try:
        manifest = json.loads(manifest_path.read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise BackupError(f"invalid backup manifest: {error}") from error
    if manifest.get("format") != "mrlc-backup-v1" or not isinstance(manifest.get("files"), list):
        raise BackupError("unsupported backup manifest")

    count = 0
    try:
        for item in manifest["files"]:
            if not isinstance(item, dict) or not isinstance(item.get("path"), str):
                raise BackupError("invalid backup file entry")
            relative = Path(item["path"])
            _validate_path(relative)
            source = backup / relative
            target = destination / relative
            if not source.is_file():
                raise BackupError(f"backup file is missing: {relative}")
            if item.get("sha256") != _sha256(source):
                raise BackupError(f"backup checksum mismatch: {relative}")
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            count += 1
        destination_manifest = destination / "manifest.json"
        destination_manifest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(manifest_path, destination_manifest)
        return RestoreResult(destination_manifest, count)
    except Exception:
        shutil.rmtree(destination, ignore_errors=True)
        raise


def _entry(path: Path, root: Path, kind: str) -> dict[str, str]:
    return {"kind": kind, "path": str(path.relative_to(root)), "sha256": _sha256(path)}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_name(name: str) -> None:
    if not name or Path(name).name != name or name in {".", ".."}:
        raise BackupError(f"invalid backup name: {name!r}")


def _validate_path(path: Path) -> None:
    if path.is_absolute() or ".." in path.parts:
        raise BackupError(f"unsafe backup path: {path}")
    if path.name in _SENSITIVE_NAMES or any(marker in f"/{path.as_posix()}" for marker in _SENSITIVE_MARKERS):
        raise BackupError(f"sensitive path is not backup eligible: {path}")
