from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from .identity import new_id


@dataclass(frozen=True, slots=True)
class Artifact:
    artifact_id: str
    project_id: str
    session_id: str
    media_type: str
    content_hash: str
    sensitivity: str = "project"


class ArtifactCorruption(RuntimeError):
    pass


class ArtifactStore:
    def __init__(self, database_path: str | Path) -> None:
        self._database_path = str(database_path)
        with sqlite3.connect(self._database_path) as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS artifacts (
                    artifact_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    media_type TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    content BLOB NOT NULL,
                    sensitivity TEXT NOT NULL DEFAULT 'project'
                );
                CREATE TABLE IF NOT EXISTS artifact_references (
                    project_id TEXT NOT NULL,
                    artifact_id TEXT NOT NULL,
                    reference TEXT NOT NULL,
                    PRIMARY KEY (artifact_id, reference)
                );
                """
            )
            columns = {row[1] for row in connection.execute("PRAGMA table_info(artifacts)")}
            if "sensitivity" not in columns:
                connection.execute(
                    "ALTER TABLE artifacts ADD COLUMN sensitivity TEXT NOT NULL DEFAULT 'project'"
                )

    def put(
        self, project_id: str, session_id: str, media_type: str, content: bytes,
        *, sensitivity: str = "project",
    ) -> Artifact:
        if sensitivity not in {"public", "project", "private"}:
            raise ValueError(f"unsupported artifact sensitivity: {sensitivity}")
        content_hash = hashlib.sha256(content).hexdigest()
        with sqlite3.connect(self._database_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT artifact_id, project_id, session_id, media_type, content_hash, sensitivity "
                "FROM artifacts WHERE project_id = ? AND session_id = ? AND media_type = ? "
                "AND content_hash = ? AND sensitivity = ? ORDER BY artifact_id LIMIT 1",
                (project_id, session_id, media_type, content_hash, sensitivity),
            ).fetchone()
            if existing is not None:
                return Artifact(*existing)
            artifact = Artifact(
                new_id("artifact"), project_id, session_id, media_type, content_hash, sensitivity
            )
            connection.execute(
                "INSERT INTO artifacts "
                "(artifact_id, project_id, session_id, media_type, content_hash, content, sensitivity) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    artifact.artifact_id, artifact.project_id, artifact.session_id,
                    artifact.media_type, artifact.content_hash, content, artifact.sensitivity,
                ),
            )
        return artifact

    def get_for_project(self, project_id: str, artifact_id: str) -> Artifact:
        with sqlite3.connect(self._database_path) as connection:
            row = connection.execute(
                "SELECT artifact_id, project_id, session_id, media_type, content_hash, sensitivity "
                "FROM artifacts WHERE artifact_id = ?", (artifact_id,)
            ).fetchone()
        if row is None:
            raise KeyError(f"unknown artifact: {artifact_id}")
        if row[1] != project_id:
            raise PermissionError("artifact access crosses project boundary")
        return Artifact(*row)

    def read_content(self, project_id: str, artifact_id: str) -> bytes:
        self.get_for_project(project_id, artifact_id)
        with sqlite3.connect(self._database_path) as connection:
            row = connection.execute(
                "SELECT content, content_hash FROM artifacts WHERE artifact_id = ?",
                (artifact_id,),
            ).fetchone()
        if row is None:
            raise KeyError(f"unknown artifact: {artifact_id}")
        content = bytes(row[0])
        if hashlib.sha256(content).hexdigest() != row[1]:
            raise ArtifactCorruption(f"artifact checksum mismatch: {artifact_id}")
        return content

    def add_reference(self, project_id: str, artifact_id: str, reference: str) -> None:
        self.get_for_project(project_id, artifact_id)
        with sqlite3.connect(self._database_path) as connection:
            connection.execute(
                "INSERT OR IGNORE INTO artifact_references(project_id, artifact_id, reference) "
                "VALUES (?, ?, ?)",
                (project_id, artifact_id, reference),
            )

    def release_reference(self, project_id: str, artifact_id: str, reference: str) -> None:
        self.get_for_project(project_id, artifact_id)
        with sqlite3.connect(self._database_path) as connection:
            connection.execute(
                "DELETE FROM artifact_references WHERE project_id = ? AND artifact_id = ? AND reference = ?",
                (project_id, artifact_id, reference),
            )

    def garbage_collect(self, project_id: str) -> tuple[str, ...]:
        with sqlite3.connect(self._database_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                "SELECT artifact_id FROM artifacts WHERE project_id = ? AND NOT EXISTS ("
                "SELECT 1 FROM artifact_references refs WHERE refs.artifact_id = artifacts.artifact_id"
                ") ORDER BY artifact_id",
                (project_id,),
            ).fetchall()
            artifact_ids = tuple(str(row[0]) for row in rows)
            connection.executemany(
                "DELETE FROM artifacts WHERE project_id = ? AND artifact_id = ?",
                [(project_id, artifact_id) for artifact_id in artifact_ids],
            )
        return artifact_ids
