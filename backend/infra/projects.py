from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from .identity import new_id


@dataclass(frozen=True, slots=True)
class Project:
    project_id: str
    name: str


class ProjectRegistry:
    def __init__(self, database_path: str | Path) -> None:
        self._database_path = str(database_path)
        with sqlite3.connect(self._database_path) as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS projects "
                "(project_id TEXT PRIMARY KEY, name TEXT NOT NULL)"
            )

    def create(self, name: str) -> Project:
        if not name.strip():
            raise ValueError("project name must not be empty")
        project = Project(new_id("project"), name)
        with sqlite3.connect(self._database_path) as connection:
            self.create_in_connection(connection, project)
        return project

    def create_in_connection(self, connection: sqlite3.Connection, project: Project) -> Project:
        connection.execute(
            "INSERT INTO projects (project_id, name) VALUES (?, ?)",
            (project.project_id, project.name),
        )
        return project

    def get(self, project_id: str) -> Project:
        with sqlite3.connect(self._database_path) as connection:
            row = connection.execute(
                "SELECT project_id, name FROM projects WHERE project_id = ?", (project_id,)
            ).fetchone()
        if row is None:
            raise KeyError(f"unknown project: {project_id}")
        return Project(project_id=row[0], name=row[1])
