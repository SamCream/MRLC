from __future__ import annotations

import json
import os
import signal
import sqlite3
from dataclasses import asdict
from pathlib import Path

from runtime.worker_contract import (
    AdapterResult,
    AttachResult,
    CancelResult,
    ParkResult,
    ReturnPackage,
    StandardWorkerEvent,
    TestResult,
    WorkerHealth,
    WorkerSessionSpec,
)
from infra.audit.redaction import redact_value


class FencingViolation(RuntimeError):
    pass


class CursorGap(RuntimeError):
    pass


class WorkerRunStore:
    """Infra-owned formal metadata for worker runs.

    A Worker's ephemeral process state stays in its adapter, but the formal, replayable
    metadata (`worker_runs` / `worker_events`: adapter, session, task, fencing token, status,
    result package, process identity, checkpoint ref) is Infra state. It lives here — not in
    `adapters/` — so the authoritative store has exactly one owner and C5/C6 can revoke adapter
    write access without losing provenance (G10 R-030).
    """
    def __init__(self, database: str | Path) -> None:
        self._database = Path(database)
        self._database.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS worker_runs (
                    handle TEXT PRIMARY KEY,
                    adapter TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    task_id TEXT NOT NULL,
                    fencing_token INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    result_json TEXT NOT NULL,
                    pid INTEGER,
                    process_start_ticks INTEGER,
                    checkpoint_ref TEXT,
                    metadata_json TEXT NOT NULL DEFAULT '{}'
                );
                CREATE TABLE IF NOT EXISTS worker_events (
                    handle TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    event_id TEXT NOT NULL UNIQUE,
                    event_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    fencing_token INTEGER NOT NULL,
                    PRIMARY KEY (handle, sequence),
                    FOREIGN KEY (handle) REFERENCES worker_runs(handle) ON DELETE CASCADE
                );
                """
            )
            self._ensure_column(connection, "worker_runs", "pid", "INTEGER")
            self._ensure_column(connection, "worker_runs", "process_start_ticks", "INTEGER")
            self._ensure_column(connection, "worker_runs", "checkpoint_ref", "TEXT")
            self._ensure_column(
                connection, "worker_runs", "metadata_json", "TEXT NOT NULL DEFAULT '{}'"
            )

    def register_process(
        self, adapter: str, spec: WorkerSessionSpec, handle: str, pid: int,
        metadata: dict[str, object] | None = None,
    ) -> None:
        package = ReturnPackage("RUNNING", "worker process is active", (), (), "")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO worker_runs(
                    handle, adapter, session_id, task_id, fencing_token, status, result_json,
                    pid, process_start_ticks, checkpoint_ref, metadata_json
                ) VALUES (?, ?, ?, ?, ?, 'RUNNING', ?, ?, ?, NULL, ?)
                ON CONFLICT(handle) DO UPDATE SET
                    adapter = excluded.adapter, session_id = excluded.session_id,
                    task_id = excluded.task_id, fencing_token = excluded.fencing_token,
                    status = excluded.status, result_json = excluded.result_json,
                    pid = excluded.pid, process_start_ticks = excluded.process_start_ticks,
                    checkpoint_ref = NULL, metadata_json = excluded.metadata_json
                """,
                (
                    handle, adapter, spec.session_id, spec.task_id, spec.lease_token,
                    json.dumps(asdict(package), sort_keys=True), pid, self._process_start_ticks(pid),
                    json.dumps(metadata or {}, sort_keys=True),
                ),
            )

    def register_session(
        self, adapter: str, spec: WorkerSessionSpec, handle: str, metadata: dict[str, object]
    ) -> None:
        package = ReturnPackage("IDLE", "worker session is ready", (), (), "")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO worker_runs(
                    handle, adapter, session_id, task_id, fencing_token, status, result_json,
                    pid, process_start_ticks, checkpoint_ref, metadata_json
                ) VALUES (?, ?, ?, ?, ?, 'IDLE', ?, NULL, NULL, NULL, ?)
                ON CONFLICT(handle) DO UPDATE SET
                    adapter = excluded.adapter, session_id = excluded.session_id,
                    task_id = excluded.task_id, fencing_token = excluded.fencing_token,
                    status = 'IDLE', result_json = excluded.result_json,
                    pid = NULL, process_start_ticks = NULL, checkpoint_ref = NULL,
                    metadata_json = excluded.metadata_json
                """,
                (
                    handle, adapter, spec.session_id, spec.task_id, spec.lease_token,
                    json.dumps(asdict(package), sort_keys=True),
                    json.dumps(metadata, sort_keys=True),
                ),
            )

    def metadata(self, handle: str, fencing_token: int) -> dict[str, object]:
        row = self._run(handle, fencing_token)
        value = json.loads(str(row["metadata_json"]))
        if not isinstance(value, dict):
            raise TypeError(f"worker metadata for {handle} is not an object")
        return value

    def has_event_type_after(
        self, handle: str, fencing_token: int, event_type: str, cursor: int
    ) -> bool:
        self._run(handle, fencing_token)
        with self._connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM worker_events WHERE handle = ? AND event_type = ? AND sequence > ? LIMIT 1",
                (handle, event_type, cursor),
            ).fetchone()
        return row is not None

    def native_handle(self, handle: str, fencing_token: int) -> str | None:
        self._run(handle, fencing_token)
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT payload_json FROM worker_events
                WHERE handle = ? AND event_type = 'STARTED'
                ORDER BY sequence DESC LIMIT 1
                """,
                (handle,),
            ).fetchone()
        if row is None:
            return None
        payload = json.loads(str(row[0]))
        native = payload.get("handle") if isinstance(payload, dict) else None
        return native if isinstance(native, str) and native else None

    def mark_checkpoint(
        self, handle: str, fencing_token: int, checkpoint_ref: str
    ) -> None:
        self.observe_checkpoint(
            handle, fencing_token, checkpoint_ref,
            event_id=f"{handle}:checkpoint:{checkpoint_ref}",
        )

    def observe_checkpoint(
        self, handle: str, fencing_token: int, checkpoint_ref: str, *, event_id: str
    ) -> None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._run_in(connection, handle, fencing_token)
            connection.execute(
                "UPDATE worker_runs SET checkpoint_ref = ? WHERE handle = ?",
                (checkpoint_ref, handle),
            )
            self._append_event(
                connection, handle, fencing_token, "CHECKPOINTED",
                {"checkpoint_ref": checkpoint_ref}, event_id=event_id,
            )

    def observe_event(
        self, handle: str, fencing_token: int, event_id: str, event_type: str,
        payload: dict[str, object],
    ) -> None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._run_in(connection, handle, fencing_token)
            self._append_event(
                connection, handle, fencing_token, event_type, payload, event_id=event_id
            )

    def finalize(
        self, handle: str, fencing_token: int, package: ReturnPackage,
        terminal_payload: dict[str, object],
    ) -> None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = self._run_in(connection, handle, fencing_token)
            if str(row["status"]) == "CANCELLED":
                return
            connection.execute(
                "UPDATE worker_runs SET status = ?, result_json = ? WHERE handle = ?",
                (package.status, json.dumps(redact_value(asdict(package)), sort_keys=True), handle),
            )
            latest = connection.execute(
                "SELECT event_type FROM worker_events WHERE handle = ? ORDER BY sequence DESC LIMIT 1",
                (handle,),
            ).fetchone()
            if latest is None or str(latest[0]) != package.status:
                self._append_event(
                    connection, handle, fencing_token, package.status, terminal_payload,
                    event_id=f"{handle}:terminal:{package.status}",
                )

    def record(
        self,
        adapter: str,
        spec: WorkerSessionSpec,
        result: AdapterResult,
    ) -> None:
        if not result.handle:
            return
        expected_sequences = list(
            range(spec.event_cursor + 1, spec.event_cursor + len(result.events) + 1)
        )
        if [event.sequence for event in result.events] != expected_sequences:
            raise CursorGap(f"non-contiguous event sequence for {result.handle}")
        if any(event.fencing_token != spec.lease_token for event in result.events):
            raise FencingViolation(f"event fencing token mismatch for {result.handle}")
        package_json = json.dumps(redact_value(asdict(result.return_package)), sort_keys=True)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT fencing_token, status FROM worker_runs WHERE handle = ?",
                (result.handle,),
            ).fetchone()
            if existing is not None and int(existing[0]) > spec.lease_token:
                raise FencingViolation(f"stale worker result for {result.handle}")
            if existing is not None and str(existing[1]) == "CANCELLED":
                return
            connection.execute(
                """
                INSERT INTO worker_runs(handle, adapter, session_id, task_id, fencing_token, status, result_json)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(handle) DO UPDATE SET
                    adapter = excluded.adapter,
                    session_id = excluded.session_id,
                    task_id = excluded.task_id,
                    fencing_token = excluded.fencing_token,
                    status = excluded.status,
                    result_json = excluded.result_json
                """,
                (
                    result.handle,
                    adapter,
                    spec.session_id,
                    spec.task_id,
                    spec.lease_token,
                    result.return_package.status,
                    package_json,
                ),
            )
            for event in result.events:
                payload_json = json.dumps(redact_value(event.payload), sort_keys=True)
                stored = connection.execute(
                    """
                    SELECT event_id, event_type, payload_json, fencing_token
                    FROM worker_events WHERE handle = ? AND sequence = ?
                    """,
                    (result.handle, event.sequence),
                ).fetchone()
                expected = (event.event_id, event.event_type, payload_json, event.fencing_token)
                if stored is not None:
                    if tuple(stored) != expected:
                        raise CursorGap(
                            f"conflicting event at sequence {event.sequence} for {result.handle}"
                        )
                    continue
                try:
                    connection.execute(
                        """
                        INSERT INTO worker_events(
                            handle, sequence, event_id, event_type, payload_json, fencing_token
                        ) VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (result.handle, event.sequence, *expected),
                    )
                except sqlite3.IntegrityError as error:
                    raise CursorGap(f"duplicate event id {event.event_id}") from error

    def attach(self, handle: str, fencing_token: int) -> AttachResult:
        row = self._run(handle, fencing_token)
        last_sequence = self._last_sequence(handle)
        return AttachResult(handle, str(row["status"]), last_sequence, fencing_token)

    def events(
        self,
        handle: str,
        cursor: int,
        fencing_token: int,
    ) -> tuple[StandardWorkerEvent, ...]:
        self._run(handle, fencing_token)
        last_sequence = self._last_sequence(handle)
        if cursor < 0 or cursor > last_sequence:
            raise CursorGap(f"cursor {cursor} outside 0..{last_sequence} for {handle}")
        with self._connect() as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                "SELECT * FROM worker_events WHERE handle = ? AND sequence > ? ORDER BY sequence",
                (handle, cursor),
            ).fetchall()
        return tuple(
            StandardWorkerEvent(
                str(row["event_id"]),
                int(row["sequence"]),
                str(row["event_type"]),
                json.loads(str(row["payload_json"])),
                int(row["fencing_token"]),
            )
            for row in rows
        )

    def collect_result(self, handle: str, fencing_token: int) -> ReturnPackage:
        row = self._run(handle, fencing_token)
        payload = json.loads(str(row["result_json"]))
        return ReturnPackage(
            str(payload["status"]),
            str(payload["summary"]),
            tuple(payload["changed_files"]),
            tuple(TestResult(**item) for item in payload["tests"]),
            str(payload["log_ref"]),
            tuple(payload.get("patch_refs", ())),
            tuple(payload.get("commit_refs", ())),
            tuple(payload.get("artifact_refs", ())),
        )

    def health(self, handle: str, fencing_token: int) -> WorkerHealth:
        row = self._run(handle, fencing_token)
        pid = row["pid"]
        alive = pid is not None and self._process_alive(
            int(pid),
            None if row["process_start_ticks"] is None else int(row["process_start_ticks"]),
        )
        return WorkerHealth(
            handle, str(row["status"]), alive, self._last_sequence(handle)
        )

    def request_park(
        self, handle: str, deadline_seconds: int, fencing_token: int
    ) -> ParkResult:
        row = self._run(handle, fencing_token)
        if str(row["status"]) != "RUNNING":
            return ParkResult("DENY", reason=f"worker is {str(row['status'])}")
        checkpoint_ref = row["checkpoint_ref"]
        if checkpoint_ref is None:
            return ParkResult(
                "DEFER", until_event="CHECKPOINTED",
                reason="worker has not reached a safe checkpoint",
                estimated_duration_seconds=max(0, deadline_seconds),
            )
        self._signal_process(row, signal.SIGSTOP)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._run_in(connection, handle, fencing_token)
            connection.execute(
                "UPDATE worker_runs SET status = 'PARKED' WHERE handle = ?", (handle,)
            )
            self._append_event(
                connection, handle, fencing_token, "PARK_READY",
                {"checkpoint_ref": str(checkpoint_ref)},
            )
        return ParkResult("READY", checkpoint_ref=str(checkpoint_ref))

    def resume_parked(self, handle: str, fencing_token: int) -> WorkerHealth:
        row = self._run(handle, fencing_token)
        if str(row["status"]) != "PARKED":
            raise RuntimeError(f"worker {handle} is not parked")
        self._signal_process(row, signal.SIGCONT)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._run_in(connection, handle, fencing_token)
            connection.execute(
                "UPDATE worker_runs SET status = 'RUNNING' WHERE handle = ?", (handle,)
            )
            self._append_event(
                connection, handle, fencing_token, "PROGRESS", {"state": "resumed"}
            )
        return self.health(handle, fencing_token)

    def cancel(self, handle: str, reason: str, fencing_token: int) -> CancelResult:
        row = self._run(handle, fencing_token)
        if str(row["status"]) in {"CANCELLED", "FAILED", "READY_FOR_REVIEW", "COMPLETED"}:
            return CancelResult(str(row["status"]), reason)
        if row["pid"] is not None and self._process_alive(
            int(row["pid"]),
            None if row["process_start_ticks"] is None else int(row["process_start_ticks"]),
        ):
            self._signal_process(row, signal.SIGTERM)
        package = ReturnPackage("CANCELLED", reason, (), (), "")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._run_in(connection, handle, fencing_token)
            connection.execute(
                "UPDATE worker_runs SET status = 'CANCELLED', result_json = ? WHERE handle = ?",
                (json.dumps(asdict(package), sort_keys=True), handle),
            )
            self._append_event(
                connection, handle, fencing_token, "CANCELLED", {"reason": reason}
            )
        return CancelResult("CANCELLED", reason)

    def advance_fence(self, handle: str, fencing_token: int) -> None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT fencing_token FROM worker_runs WHERE handle = ?",
                (handle,),
            ).fetchone()
            if row is None:
                raise KeyError(handle)
            current = int(row[0])
            if fencing_token <= current:
                raise FencingViolation(
                    f"fencing token {fencing_token} must exceed {current} for {handle}"
                )
            connection.execute(
                "UPDATE worker_runs SET fencing_token = ? WHERE handle = ?",
                (fencing_token, handle),
            )

    def _run(self, handle: str, fencing_token: int) -> sqlite3.Row:
        with self._connect() as connection:
            connection.row_factory = sqlite3.Row
            row = self._run_in(connection, handle, fencing_token)
        return row

    @staticmethod
    def _run_in(
        connection: sqlite3.Connection, handle: str, fencing_token: int
    ) -> sqlite3.Row:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            "SELECT * FROM worker_runs WHERE handle = ?",
            (handle,),
        ).fetchone()
        if row is None:
            raise KeyError(handle)
        current = int(row["fencing_token"])
        if current != fencing_token:
            raise FencingViolation(
                f"fencing token {fencing_token} is stale; current token is {current}"
            )
        return row

    def _append_event(
        self, connection: sqlite3.Connection, handle: str, fencing_token: int,
        event_type: str, payload: dict[str, object], *, event_id: str | None = None,
    ) -> None:
        if event_id is not None:
            existing = connection.execute(
                "SELECT 1 FROM worker_events WHERE event_id = ?", (event_id,)
            ).fetchone()
            if existing is not None:
                return
        row = connection.execute(
            "SELECT COALESCE(MAX(sequence), 0) FROM worker_events WHERE handle = ?",
            (handle,),
        ).fetchone()
        sequence = int(row[0]) + 1
        connection.execute(
            """
            INSERT INTO worker_events(
                handle, sequence, event_id, event_type, payload_json, fencing_token
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                handle, sequence, event_id or f"{handle}:{sequence}:{event_type.lower()}", event_type,
                json.dumps(redact_value(payload), sort_keys=True), fencing_token,
            ),
        )

    @staticmethod
    def _ensure_column(
        connection: sqlite3.Connection, table: str, column: str, declaration: str
    ) -> None:
        columns = {str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})")}
        if column not in columns:
            connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")

    @staticmethod
    def _process_start_ticks(pid: int) -> int | None:
        try:
            return int(Path(f"/proc/{pid}/stat").read_text().split()[21])
        except (OSError, IndexError, ValueError):
            return None

    @staticmethod
    def _process_state(pid: int) -> str | None:
        try:
            return Path(f"/proc/{pid}/stat").read_text().split()[2]
        except (OSError, IndexError):
            return None

    @classmethod
    def _process_alive(cls, pid: int, expected_start_ticks: int | None) -> bool:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        if cls._process_state(pid) == "Z":
            return False
        current = cls._process_start_ticks(pid)
        return expected_start_ticks is None or current == expected_start_ticks

    @classmethod
    def _signal_process(cls, row: sqlite3.Row, process_signal: signal.Signals) -> None:
        pid = row["pid"]
        if pid is None:
            raise RuntimeError(f"worker {str(row['handle'])} has no process identity")
        expected = None if row["process_start_ticks"] is None else int(row["process_start_ticks"])
        if not cls._process_alive(int(pid), expected):
            raise RuntimeError(f"worker process {int(pid)} is not alive")
        process_group_id = cls._process_group_id(row)
        if process_group_id is None:
            os.kill(int(pid), process_signal)
        else:
            os.killpg(process_group_id, process_signal)

    @staticmethod
    def _process_group_id(row: sqlite3.Row) -> int | None:
        try:
            metadata = json.loads(str(row["metadata_json"]))
        except (KeyError, TypeError, json.JSONDecodeError):
            return None
        if not isinstance(metadata, dict):
            return None
        value = metadata.get("process_group_id")
        if isinstance(value, int) and value > 0:
            return value
        if isinstance(value, str) and value.isdigit() and int(value) > 0:
            return int(value)
        return None

    def _last_sequence(self, handle: str) -> int:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT COALESCE(MAX(sequence), 0) FROM worker_events WHERE handle = ?",
                (handle,),
            ).fetchone()
        return int(row[0])

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._database)
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection
