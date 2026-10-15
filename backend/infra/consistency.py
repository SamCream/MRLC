from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class StaleVersion(ValueError):
    """The aggregate changed since the caller read it."""


class LeaseConflict(ValueError):
    """A lease is held by another live owner."""


class StaleFence(ValueError):
    """A write uses an obsolete fencing token."""


@dataclass(frozen=True, slots=True)
class AggregateReceipt:
    aggregate_id: str
    state: str
    version: int


@dataclass(frozen=True, slots=True)
class OutboxMessage:
    message_id: str
    project_id: str
    aggregate_type: str
    aggregate_id: str
    aggregate_version: int
    event_type: str
    payload: dict[str, Any]
    idempotency_key: str
    attempt_count: int
    published_at: float | None
    dead_letter_at: float | None = None
    dead_letter_reason: str | None = None


@dataclass(frozen=True, slots=True)
class Lease:
    resource_id: str
    holder_id: str
    fencing_token: int
    expires_at: float


@dataclass(frozen=True, slots=True)
class Saga:
    saga_id: str
    saga_type: str
    state: str
    payload: dict[str, Any]


class ConsistencyStore:
    """SQLite-backed transactional primitives shared by runtime aggregates."""

    _SAGA_TRANSITIONS = {
        "REQUESTED": {"PREPARED", "FAILED", "UNKNOWN"},
        "PREPARED": {"DISPATCHED", "FAILED", "COMPENSATED", "UNKNOWN"},
        "DISPATCHED": {"RUNNING", "FAILED", "UNKNOWN"},
        "RUNNING": {"SUCCEEDED", "FAILED", "UNKNOWN"},
        "SUCCEEDED": {"INTEGRATED", "COMPENSATED"},
        "FAILED": {"COMPENSATED", "MANUAL_RECONCILIATION"},
        "UNKNOWN": {"RUNNING", "SUCCEEDED", "FAILED", "MANUAL_RECONCILIATION"},
        "INTEGRATED": set(),
        "COMPENSATED": set(),
        "MANUAL_RECONCILIATION": set(),
    }

    def __init__(self, database_path: str | Path) -> None:
        self._database_path = str(database_path)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._database_path, timeout=30.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 30000")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS consistency_aggregates (
                    aggregate_type TEXT NOT NULL,
                    aggregate_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    state TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS consistency_operations (
                    operation_id TEXT PRIMARY KEY,
                    aggregate_id TEXT NOT NULL,
                    state TEXT NOT NULL,
                    version INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS consistency_outbox (
                    message_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    aggregate_type TEXT NOT NULL,
                    aggregate_id TEXT NOT NULL,
                    aggregate_version INTEGER NOT NULL,
                    event_type TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    next_attempt_at REAL NOT NULL DEFAULT 0,
                    claimed_by TEXT,
                    claim_until REAL,
                    published_at REAL,
                    last_error TEXT,
                    dead_letter_at REAL,
                    dead_letter_reason TEXT
                );
                CREATE TABLE IF NOT EXISTS consistency_inbox (
                    consumer_id TEXT NOT NULL,
                    message_id TEXT NOT NULL,
                    receipt TEXT NOT NULL,
                    processed_at REAL NOT NULL,
                    PRIMARY KEY (consumer_id, message_id)
                );
                CREATE TABLE IF NOT EXISTS consistency_leases (
                    resource_id TEXT PRIMARY KEY,
                    holder_id TEXT NOT NULL,
                    fencing_token INTEGER NOT NULL,
                    expires_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS consistency_sagas (
                    saga_id TEXT PRIMARY KEY,
                    saga_type TEXT NOT NULL,
                    state TEXT NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS consistency_saga_operations (
                    operation_id TEXT PRIMARY KEY,
                    saga_id TEXT NOT NULL,
                    state TEXT NOT NULL
                );
                """
            )
            existing = {
                row[1]
                for row in connection.execute("PRAGMA table_info(consistency_outbox)").fetchall()
            }
            for name, definition in {
                "dead_letter_at": "REAL",
                "dead_letter_reason": "TEXT",
            }.items():
                if name not in existing:
                    connection.execute(
                        f"ALTER TABLE consistency_outbox ADD COLUMN {name} {definition}"
                    )

    def create_aggregate(
        self,
        aggregate_type: str,
        aggregate_id: str,
        project_id: str,
        state: str,
        payload: dict[str, Any],
    ) -> AggregateReceipt:
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO consistency_aggregates "
                "(aggregate_type, aggregate_id, project_id, state, version, payload) VALUES (?, ?, ?, ?, 0, ?)",
                (aggregate_type, aggregate_id, project_id, state, json.dumps(payload, sort_keys=True)),
            )
        return AggregateReceipt(aggregate_id, state, 0)

    def get_aggregate(self, aggregate_id: str) -> AggregateReceipt:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT aggregate_id, state, version FROM consistency_aggregates WHERE aggregate_id = ?",
                (aggregate_id,),
            ).fetchone()
        if row is None:
            raise KeyError(f"unknown aggregate: {aggregate_id}")
        return AggregateReceipt(row["aggregate_id"], row["state"], row["version"])

    def transition(
        self,
        aggregate_type: str,
        aggregate_id: str,
        expected_version: int,
        expected_state: str,
        target_state: str,
        operation_id: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> AggregateReceipt:
        message_id = f"out_{operation_id}"
        with self._connect() as connection:
            prior = connection.execute(
                "SELECT aggregate_id, state, version FROM consistency_operations WHERE operation_id = ?",
                (operation_id,),
            ).fetchone()
            if prior is not None:
                return AggregateReceipt(prior["aggregate_id"], prior["state"], prior["version"])

            row = connection.execute(
                "SELECT project_id, state, version FROM consistency_aggregates "
                "WHERE aggregate_type = ? AND aggregate_id = ?",
                (aggregate_type, aggregate_id),
            ).fetchone()
            if row is None:
                raise KeyError(f"unknown aggregate: {aggregate_id}")
            if row["version"] != expected_version or row["state"] != expected_state:
                raise StaleVersion(
                    f"aggregate {aggregate_id} expected {expected_state}@{expected_version}, "
                    f"found {row['state']}@{row['version']}"
                )

            version = expected_version + 1
            connection.execute(
                "UPDATE consistency_aggregates SET state = ?, version = ? WHERE aggregate_id = ? "
                "AND version = ? AND state = ?",
                (target_state, version, aggregate_id, expected_version, expected_state),
            )
            if connection.total_changes != 1:
                raise StaleVersion(f"aggregate {aggregate_id} changed during transition")
            connection.execute(
                "INSERT INTO consistency_operations(operation_id, aggregate_id, state, version) VALUES (?, ?, ?, ?)",
                (operation_id, aggregate_id, target_state, version),
            )
            connection.execute(
                "INSERT INTO consistency_outbox "
                "(message_id, project_id, aggregate_type, aggregate_id, aggregate_version, event_type, payload, idempotency_key) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    message_id,
                    row["project_id"],
                    aggregate_type,
                    aggregate_id,
                    version,
                    event_type,
                    json.dumps(payload, sort_keys=True),
                    operation_id,
                ),
            )
        return AggregateReceipt(aggregate_id, target_state, version)

    def list_outbox(self) -> list[OutboxMessage]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM consistency_outbox ORDER BY rowid"
            ).fetchall()
        return [
            OutboxMessage(
                row["message_id"], row["project_id"], row["aggregate_type"], row["aggregate_id"],
                row["aggregate_version"], row["event_type"], json.loads(row["payload"]),
                row["idempotency_key"], row["attempt_count"], row["published_at"],
                row["dead_letter_at"], row["dead_letter_reason"],
            )
            for row in rows
        ]

    def claim_outbox(self, publisher_id: str, now: float, lease_seconds: float, limit: int = 100) -> list[OutboxMessage]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT message_id FROM consistency_outbox "
                "WHERE published_at IS NULL AND dead_letter_at IS NULL AND next_attempt_at <= ? "
                "AND (claim_until IS NULL OR claim_until <= ?) ORDER BY rowid LIMIT ?",
                (now, now, limit),
            ).fetchall()
            messages: list[OutboxMessage] = []
            for row in rows:
                connection.execute(
                    "UPDATE consistency_outbox SET claimed_by = ?, claim_until = ?, attempt_count = attempt_count + 1 "
                    "WHERE message_id = ? AND published_at IS NULL "
                    "AND (claim_until IS NULL OR claim_until <= ?)",
                    (publisher_id, now + lease_seconds, row["message_id"], now),
                )
                message = connection.execute(
                    "SELECT * FROM consistency_outbox WHERE message_id = ?", (row["message_id"],)
                ).fetchone()
                messages.append(
                    OutboxMessage(
                        message["message_id"], message["project_id"], message["aggregate_type"],
                        message["aggregate_id"], message["aggregate_version"], message["event_type"],
                        json.loads(message["payload"]), message["idempotency_key"],
                        message["attempt_count"], message["published_at"],
                        message["dead_letter_at"], message["dead_letter_reason"],
                    )
                )
        return messages

    def mark_published(self, message_id: str, publisher_id: str, now: float) -> None:
        with self._connect() as connection:
            changed = connection.execute(
                "UPDATE consistency_outbox SET published_at = ?, claimed_by = NULL, claim_until = NULL "
                "WHERE message_id = ? AND claimed_by = ? AND published_at IS NULL",
                (now, message_id, publisher_id),
            ).rowcount
            if changed != 1:
                raise ValueError(f"outbox message is not claimed by {publisher_id}: {message_id}")

    def fail_outbox(self, message_id: str, publisher_id: str, now: float, error: str) -> None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT attempt_count FROM consistency_outbox WHERE message_id = ? AND claimed_by = ?",
                (message_id, publisher_id),
            ).fetchone()
            if row is None:
                raise ValueError(f"outbox message is not claimed by {publisher_id}: {message_id}")
            delay = min(3600, 2 ** max(0, row["attempt_count"] - 1))
            connection.execute(
                "UPDATE consistency_outbox SET claimed_by = NULL, claim_until = NULL, next_attempt_at = ?, last_error = ? "
                "WHERE message_id = ? AND claimed_by = ? AND published_at IS NULL",
                (now + delay, error, message_id, publisher_id),
            )

    def dead_letter_outbox(self, message_id: str, publisher_id: str, now: float, error: str) -> None:
        with self._connect() as connection:
            changed = connection.execute(
                "UPDATE consistency_outbox SET dead_letter_at = ?, dead_letter_reason = ?, "
                "claimed_by = NULL, claim_until = NULL "
                "WHERE message_id = ? AND claimed_by = ? AND published_at IS NULL "
                "AND dead_letter_at IS NULL",
                (now, error, message_id, publisher_id),
            ).rowcount
            if changed != 1:
                raise ValueError(f"outbox message is not claimable by {publisher_id}: {message_id}")

    def consume(
        self,
        consumer_id: str,
        message_id: str,
        payload: dict[str, Any],
        apply: Callable[[sqlite3.Connection, dict[str, Any]], dict[str, Any]],
    ) -> dict[str, Any]:
        with self._connect() as connection:
            prior = connection.execute(
                "SELECT receipt FROM consistency_inbox WHERE consumer_id = ? AND message_id = ?",
                (consumer_id, message_id),
            ).fetchone()
            if prior is not None:
                return json.loads(prior["receipt"])
            receipt = apply(connection, payload)
            connection.execute(
                "INSERT INTO consistency_inbox(consumer_id, message_id, receipt, processed_at) "
                "VALUES (?, ?, ?, strftime('%s', 'now'))",
                (consumer_id, message_id, json.dumps(receipt, sort_keys=True)),
            )
        return receipt

    def acquire_lease(self, resource_id: str, holder_id: str, now: float, ttl: float) -> Lease:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT holder_id, fencing_token, expires_at FROM consistency_leases WHERE resource_id = ?",
                (resource_id,),
            ).fetchone()
            if row is not None and row["expires_at"] > now and row["holder_id"] != holder_id:
                raise LeaseConflict(f"resource {resource_id} is held by {row['holder_id']}")
            token = (row["fencing_token"] if row is not None else 0) + 1
            expires_at = now + ttl
            connection.execute(
                "INSERT INTO consistency_leases(resource_id, holder_id, fencing_token, expires_at) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(resource_id) DO UPDATE SET holder_id = excluded.holder_id, "
                "fencing_token = excluded.fencing_token, expires_at = excluded.expires_at",
                (resource_id, holder_id, token, expires_at),
            )
        return Lease(resource_id, holder_id, token, expires_at)

    def release_lease(self, resource_id: str, holder_id: str, fencing_token: int) -> None:
        with self._connect() as connection:
            self._check_fence(connection, resource_id, holder_id, fencing_token, None)
            connection.execute(
                "UPDATE consistency_leases SET holder_id = '', expires_at = 0 WHERE resource_id = ?",
                (resource_id,),
            )

    def renew_lease(
        self, resource_id: str, holder_id: str, fencing_token: int, *, now: float, ttl: float
    ) -> Lease:
        if ttl <= 0:
            raise ValueError("lease ttl must be positive")
        with self._connect() as connection:
            self._check_fence(connection, resource_id, holder_id, fencing_token, now)
            expires_at = now + ttl
            connection.execute(
                "UPDATE consistency_leases SET expires_at = ? WHERE resource_id = ?",
                (expires_at, resource_id),
            )
        return Lease(resource_id, holder_id, fencing_token, expires_at)

    def expire_leases(self, *, now: float) -> list[str]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT resource_id FROM consistency_leases "
                "WHERE holder_id != '' AND expires_at <= ? ORDER BY resource_id",
                (now,),
            ).fetchall()
            resource_ids = [str(row["resource_id"]) for row in rows]
            connection.executemany(
                "UPDATE consistency_leases SET holder_id = '', expires_at = 0 WHERE resource_id = ?",
                [(resource_id,) for resource_id in resource_ids],
            )
        return resource_ids

    def assert_fence(self, resource_id: str, holder_id: str, fencing_token: int, now: float) -> None:
        with self._connect() as connection:
            self._check_fence(connection, resource_id, holder_id, fencing_token, now)

    @staticmethod
    def _check_fence(
        connection: sqlite3.Connection,
        resource_id: str,
        holder_id: str,
        fencing_token: int,
        now: float | None,
    ) -> None:
        row = connection.execute(
            "SELECT holder_id, fencing_token, expires_at FROM consistency_leases WHERE resource_id = ?",
            (resource_id,),
        ).fetchone()
        if row is None or row["holder_id"] != holder_id or row["fencing_token"] != fencing_token:
            raise StaleFence(f"stale fence for resource {resource_id}")
        if now is not None and row["expires_at"] <= now:
            raise StaleFence(f"expired fence for resource {resource_id}")

    def create_saga(self, saga_id: str, saga_type: str, payload: dict[str, Any]) -> Saga:
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO consistency_sagas(saga_id, saga_type, state, payload) VALUES (?, ?, 'REQUESTED', ?)",
                (saga_id, saga_type, json.dumps(payload, sort_keys=True)),
            )
        return Saga(saga_id, saga_type, "REQUESTED", payload)

    def get_saga(self, saga_id: str) -> Saga:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM consistency_sagas WHERE saga_id = ?", (saga_id,)
            ).fetchone()
        if row is None:
            raise KeyError(f"unknown saga: {saga_id}")
        return Saga(row["saga_id"], row["saga_type"], row["state"], json.loads(row["payload"]))

    def transition_saga(self, saga_id: str, expected_state: str, target_state: str, operation_id: str) -> Saga:
        with self._connect() as connection:
            prior = connection.execute(
                "SELECT state FROM consistency_saga_operations WHERE operation_id = ?", (operation_id,)
            ).fetchone()
            if prior is not None:
                return self.get_saga(saga_id)
            row = connection.execute(
                "SELECT * FROM consistency_sagas WHERE saga_id = ?", (saga_id,)
            ).fetchone()
            if row is None:
                raise KeyError(f"unknown saga: {saga_id}")
            if row["state"] != expected_state:
                raise ValueError(f"saga {saga_id} expected {expected_state}, found {row['state']}")
            if target_state not in self._SAGA_TRANSITIONS[expected_state]:
                raise ValueError(f"invalid saga transition: {expected_state} -> {target_state}")
            connection.execute("UPDATE consistency_sagas SET state = ? WHERE saga_id = ?", (target_state, saga_id))
            connection.execute(
                "INSERT INTO consistency_saga_operations(operation_id, saga_id, state) VALUES (?, ?, ?)",
                (operation_id, saga_id, target_state),
            )
            return Saga(row["saga_id"], row["saga_type"], target_state, json.loads(row["payload"]))
