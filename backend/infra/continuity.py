from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .identity import new_id


@dataclass(frozen=True, slots=True)
class ContinuityCheckpoint:
    checkpoint_id: str
    project_id: str
    session_id: str
    project_directory: str
    client: str
    git_branch: str | None
    git_head_sha: str | None
    git_dirty: bool
    objective: str | None
    completed: tuple[str, ...]
    blockers: tuple[str, ...]
    next_steps: tuple[str, ...]
    modified_files: tuple[str, ...]
    verification_status: str | None
    created_at: datetime


class ContinuityStore:
    def __init__(self, database_path: str | Path) -> None:
        self._database_path = str(database_path)
        with sqlite3.connect(self._database_path) as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS continuity_checkpoints ("
                "checkpoint_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, session_id TEXT NOT NULL, "
                "project_directory TEXT NOT NULL, client TEXT NOT NULL, git_branch TEXT, git_head_sha TEXT, "
                "git_dirty INTEGER NOT NULL, objective TEXT, completed_json TEXT NOT NULL, blockers_json TEXT NOT NULL, "
                "next_steps_json TEXT NOT NULL, modified_files_json TEXT NOT NULL, verification_status TEXT, "
                "created_at TEXT NOT NULL)"
            )

    def append(self, checkpoint: ContinuityCheckpoint) -> ContinuityCheckpoint:
        with sqlite3.connect(self._database_path) as connection:
            connection.execute(
                "INSERT INTO continuity_checkpoints VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (checkpoint.checkpoint_id, checkpoint.project_id, checkpoint.session_id, checkpoint.project_directory,
                 checkpoint.client, checkpoint.git_branch, checkpoint.git_head_sha, int(checkpoint.git_dirty),
                 checkpoint.objective, json.dumps(checkpoint.completed), json.dumps(checkpoint.blockers),
                 json.dumps(checkpoint.next_steps), json.dumps(checkpoint.modified_files),
                 checkpoint.verification_status, checkpoint.created_at.isoformat()),
            )
        return checkpoint

    def create(self, **values: object) -> ContinuityCheckpoint:
        return self.append(ContinuityCheckpoint(checkpoint_id=new_id("checkpoint"), **values))

    def latest(self, project_id: str, git_branch: str | None = None) -> ContinuityCheckpoint | None:
        query = "SELECT * FROM continuity_checkpoints WHERE project_id = ?"
        parameters: list[object] = [project_id]
        if git_branch is not None:
            query += " AND git_branch = ?"
            parameters.append(git_branch)
        query += " ORDER BY created_at DESC, checkpoint_id DESC LIMIT 1"
        with sqlite3.connect(self._database_path) as connection:
            row = connection.execute(query, parameters).fetchone()
        return None if row is None else self._from_row(row)

    @staticmethod
    def _from_row(row: tuple[object, ...]) -> ContinuityCheckpoint:
        return ContinuityCheckpoint(
            str(row[0]), str(row[1]), str(row[2]), str(row[3]), str(row[4]),
            None if row[5] is None else str(row[5]), None if row[6] is None else str(row[6]), bool(row[7]),
            None if row[8] is None else str(row[8]), tuple(json.loads(str(row[9]))), tuple(json.loads(str(row[10]))),
            tuple(json.loads(str(row[11]))), tuple(json.loads(str(row[12]))),
            None if row[13] is None else str(row[13]), datetime.fromisoformat(str(row[14])),
        )
