from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass(frozen=True, slots=True)
class HistoryEntry:
    project_id: str
    session_id: str
    actor: str
    content: str
    occurred_at: datetime


class HistoryStore:
    def __init__(self, database_path: str | Path) -> None:
        self._database_path = str(database_path)
        with sqlite3.connect(self._database_path) as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS history "
                "(project_id TEXT NOT NULL, session_id TEXT NOT NULL, actor TEXT NOT NULL, "
                "content TEXT NOT NULL, occurred_at TEXT NOT NULL)"
            )

    def append(self, entry: HistoryEntry) -> None:
        with sqlite3.connect(self._database_path) as connection:
            connection.execute(
                "INSERT INTO history (project_id, session_id, actor, content, occurred_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (entry.project_id, entry.session_id, entry.actor, entry.content, entry.occurred_at.isoformat()),
            )

    def query(
        self, project_id: str, session_id: str, *, limit: int | None = None, offset: int = 0
    ) -> list[HistoryEntry]:
        if limit is not None and limit <= 0:
            raise ValueError("history limit must be positive")
        if offset < 0:
            raise ValueError("history offset cannot be negative")
        pagination = "" if limit is None else " LIMIT ? OFFSET ?"
        parameters: tuple[object, ...] = (project_id, session_id)
        if limit is not None:
            parameters += (limit, offset)
        with sqlite3.connect(self._database_path) as connection:
            rows = connection.execute(
                "SELECT project_id, session_id, actor, content, occurred_at FROM history "
                "WHERE project_id = ? AND session_id = ? ORDER BY occurred_at, rowid" + pagination,
                parameters,
            ).fetchall()
        return [HistoryEntry(row[0], row[1], row[2], row[3], datetime.fromisoformat(row[4])) for row in rows]

    def enforce_retention(self, project_id: str, *, before: datetime) -> int:
        with sqlite3.connect(self._database_path) as connection:
            cursor = connection.execute(
                "DELETE FROM history WHERE project_id = ? AND occurred_at < ?",
                (project_id, before.isoformat()),
            )
        return cursor.rowcount
