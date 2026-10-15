from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from collections.abc import Callable
from typing import Any


@dataclass(frozen=True, slots=True)
class Event:
    event_id: str
    project_id: str
    event_type: str
    payload: dict[str, Any]
    occurred_at: datetime
    idempotency_key: str
    schema_version: int = 1
    actor_type: str = "system"
    actor_id: str = "system"
    session_id: str | None = None
    task_id: str | None = None
    correlation_id: str | None = None
    causation_id: str | None = None
    recorded_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    sequence: int = field(default=0, compare=False)


@dataclass(frozen=True, slots=True)
class AppendReceipt:
    event_id: str
    sequence: int


class EventStore:
    def __init__(self, database_path: str | Path) -> None:
        self._database_path = str(database_path)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._database_path, timeout=30.0)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS events (
                    project_id TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    event_id TEXT NOT NULL UNIQUE,
                    event_type TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    occurred_at TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    schema_version INTEGER NOT NULL DEFAULT 1,
                    actor_type TEXT NOT NULL DEFAULT 'system',
                    actor_id TEXT NOT NULL DEFAULT 'system',
                    session_id TEXT,
                    task_id TEXT,
                    correlation_id TEXT,
                    causation_id TEXT,
                    recorded_at TEXT NOT NULL DEFAULT '',
                    PRIMARY KEY (project_id, sequence),
                    UNIQUE (project_id, idempotency_key)
                )
                """
            )
            columns = {
                "schema_version": "INTEGER NOT NULL DEFAULT 1",
                "actor_type": "TEXT NOT NULL DEFAULT 'system'",
                "actor_id": "TEXT NOT NULL DEFAULT 'system'",
                "session_id": "TEXT",
                "task_id": "TEXT",
                "correlation_id": "TEXT",
                "causation_id": "TEXT",
                "recorded_at": "TEXT NOT NULL DEFAULT ''",
            }
            existing = {
                row[1]
                for row in connection.execute("PRAGMA table_info(events)").fetchall()
            }
            for name, definition in columns.items():
                if name not in existing:
                    connection.execute(f"ALTER TABLE events ADD COLUMN {name} {definition}")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS event_subscriptions (
                    project_id TEXT NOT NULL,
                    subscriber_id TEXT NOT NULL,
                    acknowledged_sequence INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (project_id, subscriber_id)
                )
                """
            )

    def append(self, event: Event) -> AppendReceipt:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            return self.append_in_connection(connection, event)

    def append_in_connection(self, connection: sqlite3.Connection, event: Event) -> AppendReceipt:
        existing = connection.execute(
            "SELECT event_id, sequence, event_type, payload FROM events "
            "WHERE project_id = ? AND idempotency_key = ?",
            (event.project_id, event.idempotency_key),
        ).fetchone()
        if existing is not None:
            if (
                existing["event_id"] != event.event_id
                or existing["event_type"] != event.event_type
                or json.loads(existing["payload"]) != event.payload
            ):
                raise ValueError("idempotency key conflicts with an existing event")
            return AppendReceipt(event_id=event.event_id, sequence=existing["sequence"])

        row = connection.execute(
            "SELECT COALESCE(MAX(sequence), 0) + 1 AS next_sequence "
            "FROM events WHERE project_id = ?",
            (event.project_id,),
        ).fetchone()
        sequence = int(row["next_sequence"])
        connection.execute(
            "INSERT INTO events "
            "(project_id, sequence, event_id, event_type, payload, occurred_at, idempotency_key, "
            "schema_version, actor_type, actor_id, session_id, task_id, correlation_id, causation_id, recorded_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                event.project_id,
                sequence,
                event.event_id,
                event.event_type,
                json.dumps(event.payload, sort_keys=True),
                event.occurred_at.isoformat(),
                event.idempotency_key,
                event.schema_version,
                event.actor_type,
                event.actor_id,
                event.session_id,
                event.task_id,
                event.correlation_id,
                event.causation_id,
                event.recorded_at.isoformat(),
            ),
        )
        return AppendReceipt(event_id=event.event_id, sequence=sequence)

    def read(self, project_id: str, after_sequence: int = 0) -> list[Event]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT sequence, event_id, project_id, event_type, payload, occurred_at, idempotency_key, "
                "schema_version, actor_type, actor_id, session_id, task_id, correlation_id, causation_id, recorded_at "
                "FROM events WHERE project_id = ? AND sequence > ? ORDER BY sequence",
                (project_id, after_sequence),
            ).fetchall()
        return [
            Event(
                sequence=row["sequence"],
                event_id=row["event_id"],
                project_id=row["project_id"],
                event_type=row["event_type"],
                payload=json.loads(row["payload"]),
                occurred_at=datetime.fromisoformat(row["occurred_at"]),
                idempotency_key=row["idempotency_key"],
                schema_version=row["schema_version"],
                actor_type=row["actor_type"],
                actor_id=row["actor_id"],
                session_id=row["session_id"],
                task_id=row["task_id"],
                correlation_id=row["correlation_id"],
                causation_id=row["causation_id"],
                recorded_at=datetime.fromisoformat(row["recorded_at"] or row["occurred_at"]),
            )
            for row in rows
        ]

    def replay(self, project_id: str, apply: Callable[[Event], None], after_sequence: int = 0) -> None:
        for event in self.read(project_id, after_sequence):
            apply(event)

    def poll_subscription(self, project_id: str, subscriber_id: str, *, limit: int = 100) -> list[Event]:
        if limit <= 0:
            raise ValueError("subscription limit must be positive")
        with self._connect() as connection:
            row = connection.execute(
                "SELECT acknowledged_sequence FROM event_subscriptions "
                "WHERE project_id = ? AND subscriber_id = ?",
                (project_id, subscriber_id),
            ).fetchone()
            acknowledged = int(row["acknowledged_sequence"]) if row is not None else 0
        return self.read(project_id, acknowledged)[:limit]

    def ack_subscription(self, project_id: str, subscriber_id: str, sequence: int) -> None:
        if sequence < 0:
            raise ValueError("subscription sequence cannot be negative")
        with self._connect() as connection:
            latest = connection.execute(
                "SELECT COALESCE(MAX(sequence), 0) FROM events WHERE project_id = ?",
                (project_id,),
            ).fetchone()[0]
            if sequence > latest:
                raise ValueError("subscription cannot acknowledge an unknown sequence")
            current = connection.execute(
                "SELECT acknowledged_sequence FROM event_subscriptions "
                "WHERE project_id = ? AND subscriber_id = ?",
                (project_id, subscriber_id),
            ).fetchone()
            if current is not None and sequence < int(current["acknowledged_sequence"]):
                raise ValueError("subscription checkpoint cannot move backwards")
            connection.execute(
                "INSERT INTO event_subscriptions(project_id, subscriber_id, acknowledged_sequence) "
                "VALUES (?, ?, ?) ON CONFLICT(project_id, subscriber_id) DO UPDATE SET "
                "acknowledged_sequence = excluded.acknowledged_sequence",
                (project_id, subscriber_id, sequence),
            )
