from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from .identity import new_id


@dataclass(frozen=True, slots=True)
class TaskSpec:
    project_id: str
    title: str


@dataclass(frozen=True, slots=True)
class Task:
    task_id: str
    project_id: str
    title: str
    state: str
    block_reason: str | None = None


class TaskRegistry:
    def __init__(self, database_path: str | Path) -> None:
        self._database_path = str(database_path)
        with sqlite3.connect(self._database_path) as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS tasks (
                    task_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, title TEXT NOT NULL,
                    state TEXT NOT NULL, block_reason TEXT, parent_task_id TEXT
                );
                CREATE TABLE IF NOT EXISTS task_dependencies (
                    task_id TEXT NOT NULL, dependency_task_id TEXT NOT NULL,
                    PRIMARY KEY (task_id, dependency_task_id)
                );
                """
            )
            columns = {row[1] for row in connection.execute("PRAGMA table_info(tasks)")}
            if "block_reason" not in columns:
                connection.execute("ALTER TABLE tasks ADD COLUMN block_reason TEXT")
            if "parent_task_id" not in columns:
                connection.execute("ALTER TABLE tasks ADD COLUMN parent_task_id TEXT")

    def create(self, spec: TaskSpec) -> Task:
        if not spec.title.strip():
            raise ValueError("task title must not be empty")
        task = Task(new_id("task"), spec.project_id, spec.title, "CREATED")
        with sqlite3.connect(self._database_path) as connection:
            self.create_in_connection(connection, task)
        return task

    def create_in_connection(self, connection: sqlite3.Connection, task: Task) -> Task:
        connection.execute(
            "INSERT INTO tasks (task_id, project_id, title, state, block_reason) VALUES (?, ?, ?, ?, ?)",
            (task.task_id, task.project_id, task.title, task.state, task.block_reason),
        )
        return task

    def transition(self, task_id: str, expected_state: str, target_state: str) -> Task:
        with sqlite3.connect(self._database_path) as connection:
            return self.transition_in_connection(connection, task_id, expected_state, target_state)

    def list_project(self, project_id: str) -> list[Task]:
        with sqlite3.connect(self._database_path) as connection:
            rows = connection.execute(
                "SELECT task_id, project_id, title, state, block_reason "
                "FROM tasks WHERE project_id = ? ORDER BY rowid",
                (project_id,),
            ).fetchall()
        return [Task(*row) for row in rows]

    def transition_in_connection(
        self,
        connection: sqlite3.Connection,
        task_id: str,
        expected_state: str,
        target_state: str,
    ) -> Task:
        row = connection.execute(
            "SELECT task_id, project_id, title, state, block_reason FROM tasks WHERE task_id = ?", (task_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"unknown task: {task_id}")
        if row[3] != expected_state:
            raise ValueError(f"expected state {expected_state}, found {row[3]}")
        if target_state == "RUNNABLE":
            pending = connection.execute(
                "SELECT 1 FROM task_dependencies deps JOIN tasks dep ON dep.task_id = deps.dependency_task_id "
                "WHERE deps.task_id = ? AND dep.state != 'COMPLETED' LIMIT 1",
                (task_id,),
            ).fetchone()
            if pending is not None:
                raise ValueError("task dependency is not completed")
        connection.execute(
            "UPDATE tasks SET state = ?, block_reason = ? WHERE task_id = ?",
            (target_state, None if target_state != "BLOCKED" else row[4], task_id),
        )
        return Task(row[0], row[1], row[2], target_state, row[4] if target_state == "BLOCKED" else None)

    def set_parent(self, task_id: str, parent_task_id: str) -> None:
        with sqlite3.connect(self._database_path) as connection:
            self.set_parent_in_connection(connection, task_id, parent_task_id)

    def set_parent_in_connection(
        self, connection: sqlite3.Connection, task_id: str, parent_task_id: str
    ) -> None:
        """Lineage only: the parent is not a scheduling dependency."""
        if task_id == parent_task_id:
            raise ValueError("task cannot be its own parent")
        for name, identifier in (("task", task_id), ("parent task", parent_task_id)):
            if connection.execute("SELECT 1 FROM tasks WHERE task_id = ?", (identifier,)).fetchone() is None:
                raise KeyError(f"unknown {name}")
        connection.execute("UPDATE tasks SET parent_task_id = ? WHERE task_id = ?", (parent_task_id, task_id))

    def parent_of(self, task_id: str) -> str | None:
        with sqlite3.connect(self._database_path) as connection:
            row = connection.execute("SELECT parent_task_id FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
        if row is None:
            raise KeyError(f"unknown task: {task_id}")
        return row[0]

    def add_dependency(self, task_id: str, dependency_task_id: str) -> None:
        with sqlite3.connect(self._database_path) as connection:
            self.add_dependency_in_connection(connection, task_id, dependency_task_id)

    def add_dependency_in_connection(
        self, connection: sqlite3.Connection, task_id: str, dependency_task_id: str
    ) -> None:
        task = connection.execute("SELECT project_id FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
        dependency = connection.execute("SELECT project_id FROM tasks WHERE task_id = ?", (dependency_task_id,)).fetchone()
        if task is None or dependency is None:
            raise KeyError("unknown task dependency")
        if task[0] != dependency[0]:
            raise PermissionError("task dependency crosses project boundary")
        if task_id == dependency_task_id:
            raise ValueError("task cannot depend on itself")
        connection.execute(
            "INSERT OR IGNORE INTO task_dependencies(task_id, dependency_task_id) VALUES (?, ?)",
            (task_id, dependency_task_id),
        )

    def block(self, task_id: str, *, expected_state: str, reason: str) -> Task:
        with sqlite3.connect(self._database_path) as connection:
            row = connection.execute(
                "SELECT task_id, project_id, title, state, block_reason FROM tasks WHERE task_id = ?",
                (task_id,),
            ).fetchone()
            if row is None:
                raise KeyError(f"unknown task: {task_id}")
            if row[3] != expected_state:
                raise ValueError(f"expected state {expected_state}, found {row[3]}")
            connection.execute("UPDATE tasks SET state = 'BLOCKED', block_reason = ? WHERE task_id = ?", (reason, task_id))
            return Task(row[0], row[1], row[2], "BLOCKED", reason)

    def unblock(self, task_id: str, *, expected_state: str) -> Task:
        with sqlite3.connect(self._database_path) as connection:
            row = connection.execute(
                "SELECT task_id, project_id, title, state, block_reason FROM tasks WHERE task_id = ?",
                (task_id,),
            ).fetchone()
            if row is None:
                raise KeyError(f"unknown task: {task_id}")
            if row[3] != expected_state:
                raise ValueError(f"expected state {expected_state}, found {row[3]}")
            connection.execute("UPDATE tasks SET state = 'RUNNABLE', block_reason = NULL WHERE task_id = ?", (task_id,))
            return Task(row[0], row[1], row[2], "RUNNABLE", None)
