from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from infra.persistence.backup import BackupError, create_backup, restore_backup


def test_backup_restores_sqlite_and_artifacts_with_manifest(tmp_path: Path) -> None:
    source = tmp_path / "source"
    database = source / "infra.sqlite"
    artifacts = source / "artifacts"
    artifacts.mkdir(parents=True)
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE facts (value TEXT NOT NULL)")
        connection.execute("INSERT INTO facts VALUES ('before-backup')")
        connection.commit()
    (artifacts / "result.txt").write_text("artifact-v1\n")

    backup = create_backup(
        tmp_path / "backup",
        databases={"infra": database},
        artifact_dirs={"artifacts": artifacts},
    )

    destination = tmp_path / "restored"
    restored = restore_backup(backup, destination)

    assert restored.manifest_path == destination / "manifest.json"
    with sqlite3.connect(destination / "databases" / "infra.sqlite") as connection:
        assert connection.execute("SELECT value FROM facts").fetchone() == ("before-backup",)
    assert (destination / "artifacts" / "artifacts" / "result.txt").read_text() == "artifact-v1\n"
    assert restored.files == 2


def test_backup_rejects_sensitive_files(tmp_path: Path) -> None:
    database = tmp_path / "infra.sqlite"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE facts (value TEXT)")
        connection.commit()
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    (artifacts / ".env").write_text("TOKEN=secret\n")

    with pytest.raises(BackupError, match="sensitive"):
        create_backup(
            tmp_path / "backup",
            databases={"infra": database},
            artifact_dirs={"artifacts": artifacts},
        )
