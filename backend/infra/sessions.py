from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .identity import new_id


@dataclass(frozen=True, slots=True)
class SessionSpec:
    project_id: str
    application_id: str
    role_id: str


@dataclass(frozen=True, slots=True)
class Session:
    session_id: str
    project_id: str
    application_id: str
    role_id: str
    runtime_adapter: str | None = None
    model_binding: str | None = None
    snapshot: dict[str, Any] | None = None
    lifecycle: str = "ACTIVE"
    version: int = 0
    park_reason: str | None = None


class SessionRegistry:
    def __init__(self, database_path: str | Path) -> None:
        self._database_path = str(database_path)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._database_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    session_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    application_id TEXT NOT NULL,
                    role_id TEXT NOT NULL,
                    runtime_adapter TEXT,
                    model_binding TEXT,
                    snapshot TEXT,
                    lifecycle TEXT NOT NULL DEFAULT 'ACTIVE',
                    version INTEGER NOT NULL DEFAULT 0,
                    park_reason TEXT
                )
                """
            )
            columns = {row[1] for row in connection.execute("PRAGMA table_info(sessions)")}
            if "lifecycle" not in columns:
                connection.execute(
                    "ALTER TABLE sessions ADD COLUMN lifecycle TEXT NOT NULL DEFAULT 'ACTIVE'"
                )
            if "version" not in columns:
                connection.execute(
                    "ALTER TABLE sessions ADD COLUMN version INTEGER NOT NULL DEFAULT 0"
                )
            if "park_reason" not in columns:
                connection.execute("ALTER TABLE sessions ADD COLUMN park_reason TEXT")

    def create(self, spec: SessionSpec) -> Session:
        session = Session(
            session_id=new_id("session"),
            project_id=spec.project_id,
            application_id=spec.application_id,
            role_id=spec.role_id,
        )
        with self._connect() as connection:
            self.create_in_connection(connection, session)
        return session

    def create_in_connection(self, connection: sqlite3.Connection, session: Session) -> Session:
        connection.execute(
            "INSERT INTO sessions "
            "(session_id, project_id, application_id, role_id) VALUES (?, ?, ?, ?)",
            (session.session_id, session.project_id, session.application_id, session.role_id),
        )
        return session

    def bind_runtime(self, session_id: str, runtime_adapter: str, model_binding: str) -> Session:
        with self._connect() as connection:
            return self.bind_runtime_in_connection(connection, session_id, runtime_adapter, model_binding)

    def bind_runtime_in_connection(
        self,
        connection: sqlite3.Connection,
        session_id: str,
        runtime_adapter: str,
        model_binding: str,
    ) -> Session:
        current = connection.execute(
            "SELECT lifecycle FROM sessions WHERE session_id = ?", (session_id,)
        ).fetchone()
        if current is None:
            raise KeyError(f"unknown session: {session_id}")
        if current["lifecycle"] == "ARCHIVED":
            raise ValueError("archived session cannot bind runtime")
        connection.execute(
            "UPDATE sessions SET runtime_adapter = ?, model_binding = ? WHERE session_id = ?",
            (runtime_adapter, model_binding, session_id),
        )
        row = connection.execute("SELECT * FROM sessions WHERE session_id = ?", (session_id,)).fetchone()
        if row is None:
            raise KeyError(f"unknown session: {session_id}")
        return self._session_from_row(row)

    def snapshot(self, session_id: str, snapshot: dict[str, Any]) -> Session:
        with self._connect() as connection:
            current = connection.execute(
                "SELECT lifecycle FROM sessions WHERE session_id = ?", (session_id,)
            ).fetchone()
            if current is None:
                raise KeyError(f"unknown session: {session_id}")
            if current["lifecycle"] == "ARCHIVED":
                raise ValueError("archived session cannot snapshot")
            connection.execute(
                "UPDATE sessions SET snapshot = ? WHERE session_id = ?",
                (json.dumps(snapshot, sort_keys=True), session_id),
            )
        return self.get(session_id)

    def park(self, session_id: str, *, expected_version: int, reason: str) -> Session:
        return self._transition_lifecycle(
            session_id, expected_version, allowed=("ACTIVE",), target="PARKED",
            park_reason=reason,
        )

    def resume(self, session_id: str, *, expected_version: int) -> Session:
        return self._transition_lifecycle(
            session_id, expected_version, allowed=("PARKED",), target="ACTIVE",
            park_reason=None,
        )

    def archive(self, session_id: str, *, expected_version: int) -> Session:
        return self._transition_lifecycle(
            session_id, expected_version, allowed=("ACTIVE", "PARKED"),
            target="ARCHIVED", park_reason=None,
        )

    def _transition_lifecycle(
        self, session_id: str, expected_version: int, *, allowed: tuple[str, ...],
        target: str, park_reason: str | None,
    ) -> Session:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT lifecycle, version FROM sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            if row is None:
                raise KeyError(f"unknown session: {session_id}")
            if int(row["version"]) != expected_version:
                raise ValueError(
                    f"session version mismatch: expected {expected_version}, current {row['version']}"
                )
            if row["lifecycle"] not in allowed:
                raise ValueError(
                    f"session lifecycle {row['lifecycle']} cannot transition to {target}"
                )
            connection.execute(
                "UPDATE sessions SET lifecycle = ?, version = version + 1, park_reason = ? "
                "WHERE session_id = ? AND version = ?",
                (target, park_reason, session_id, expected_version),
            )
            updated = connection.execute(
                "SELECT * FROM sessions WHERE session_id = ?", (session_id,)
            ).fetchone()
        assert updated is not None
        return self._session_from_row(updated)

    def get(self, session_id: str) -> Session:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM sessions WHERE session_id = ?", (session_id,)
            ).fetchone()
        if row is None:
            raise KeyError(f"unknown session: {session_id}")
        return self._session_from_row(row)

    def get_for_project(self, project_id: str, session_id: str) -> Session:
        session = self.get(session_id)
        if session.project_id != project_id:
            raise PermissionError("session access crosses project boundary")
        return session

    def list_project(self, project_id: str) -> list[Session]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM sessions WHERE project_id = ? ORDER BY rowid", (project_id,)
            ).fetchall()
        return [self._session_from_row(row) for row in rows]

    @staticmethod
    def _session_from_row(row: sqlite3.Row) -> Session:
        return Session(
            session_id=row["session_id"],
            project_id=row["project_id"],
            application_id=row["application_id"],
            role_id=row["role_id"],
            runtime_adapter=row["runtime_adapter"],
            model_binding=row["model_binding"],
            snapshot=json.loads(row["snapshot"]) if row["snapshot"] else None,
            lifecycle=row["lifecycle"],
            version=int(row["version"]),
            park_reason=row["park_reason"],
        )
