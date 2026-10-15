from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from enum import StrEnum
import json
import sqlite3
from pathlib import Path


class ParkDecision(StrEnum):
    ACCEPT = "PARK_ACCEPT"
    DEFER = "PARK_DEFER"
    DENY = "PARK_DENY"


@dataclass(frozen=True, slots=True)
class SessionMeta:
    session_id: str
    project_id: str
    priority: int
    task_state: str = "RUNNABLE"
    slot_state: str = "NONE"
    yieldability: str = "after_turn"
    lease_started: int | None = None
    park_requested: bool = False
    family_parent: str | None = None
    fencing_token: int = 0
    queued_at: int = 0
    park_count: int = 0
    cooldown_until: int = 0


class Scheduler:
    @classmethod
    def from_connection(cls, connection):
        connection.execute("CREATE TABLE IF NOT EXISTS scheduler_state "
                           "(id INTEGER PRIMARY KEY CHECK (id=1), state_json TEXT NOT NULL)")
        row = connection.execute("SELECT state_json FROM scheduler_state WHERE id=1").fetchone()
        state = json.loads(row[0]) if row else {}
        scheduler = cls(capacity=state.get("capacity", 2))
        scheduler._sessions = {key: SessionMeta(**value) for key, value in state.get("sessions", {}).items()}
        scheduler._queue = list(state.get("queue", []))
        scheduler.priority_adjustments = list(state.get("priority_adjustments", []))
        return scheduler

    def save_in_connection(self, connection):
        state = {"capacity": self.capacity, "sessions": {
            key: asdict(value) for key, value in self._sessions.items()},
            "queue": self._queue, "priority_adjustments": self.priority_adjustments}
        connection.execute("INSERT INTO scheduler_state VALUES(1,?) ON CONFLICT(id) "
                           "DO UPDATE SET state_json=excluded.state_json", (json.dumps(state),))

    def __init__(
        self,
        capacity: int = 2,
        lease_ttl_seconds: int = 300,
        aging_interval_seconds: int = 60,
        thrash_limit: int = 3,
        thrash_cooldown_seconds: int = 30,
        database_path: str | Path | None = None,
    ) -> None:
        if capacity < 1:
            raise ValueError("scheduler capacity must be positive")
        self.capacity = capacity
        self.lease_ttl_seconds = lease_ttl_seconds
        self.aging_interval_seconds = aging_interval_seconds
        self.thrash_limit = thrash_limit
        self.thrash_cooldown_seconds = thrash_cooldown_seconds
        self._database_path = str(database_path) if database_path is not None else None
        self._sessions: dict[str, SessionMeta] = {}
        self._queue: list[str] = []
        self.priority_adjustments: list[dict[str, object]] = []
        if self._database_path is not None:
            self._initialize_persistence()
            self._load()

    def enqueue(self, session: SessionMeta, *, now: int = 0) -> None:
        if session.session_id in self._sessions:
            current = self._sessions[session.session_id]
            if current.slot_state in {"PARKED", "NONE"}:
                if session.session_id not in self._queue:
                    self._queue.append(session.session_id)
                self._sessions[session.session_id] = replace(
                    session,
                    slot_state="QUEUED",
                    fencing_token=current.fencing_token,
                    queued_at=now,
                    park_count=current.park_count,
                    cooldown_until=current.cooldown_until,
                )
            else:
                self._sessions[session.session_id] = replace(
                    session,
                    slot_state=current.slot_state,
                    fencing_token=current.fencing_token,
                    queued_at=current.queued_at,
                    park_count=current.park_count,
                    cooldown_until=current.cooldown_until,
                )
            self._persist()
            return
        self._sessions[session.session_id] = replace(session, slot_state="QUEUED", queued_at=now)
        self._queue.append(session.session_id)
        self._persist()

    def dispatch(self, now: int = 0) -> SessionMeta | None:
        if self._active_count() >= self.capacity:
            return None
        candidates = [
            session
            for session in self._sessions.values()
            if self._eligible(session.session_id, now)
            and (session.session_id in self._queue or session.slot_state == "RESERVED_FAMILY")
        ]
        if not candidates:
            return None
        family_candidates = [session for session in candidates if session.slot_state == "RESERVED_FAMILY"]
        selected = max(
            family_candidates or candidates,
            key=lambda session: (
                session.priority + max(0, now - session.queued_at) // self.aging_interval_seconds,
                -self._queue.index(session.session_id) if session.session_id in self._queue else 0,
            ),
        )
        if selected.session_id in self._queue:
            self._queue.remove(selected.session_id)
        selected = replace(
            selected,
            slot_state="ACTIVE",
            lease_started=now,
            fencing_token=selected.fencing_token + 1,
        )
        self._sessions[selected.session_id] = selected
        self._persist()
        return selected

    def turn_end(self, session_id: str) -> None:
        session = self._get(session_id)
        if session.park_requested:
            self._sessions[session_id] = replace(session, slot_state="PARKED", lease_started=None, park_requested=False)
            self._persist()

    def park(
        self, session_id: str, decision: ParkDecision | str, reason: str, *, now: int = 0
    ) -> None:
        try:
            decision = ParkDecision(decision)
        except ValueError as error:
            raise ValueError(f"unknown park decision: {decision}") from error
        session = self._get(session_id)
        if decision is ParkDecision.ACCEPT:
            park_count = session.park_count + 1
            cooldown_until = (
                now + self.thrash_cooldown_seconds
                if park_count >= self.thrash_limit
                else session.cooldown_until
            )
            self._sessions[session_id] = replace(
                session,
                slot_state="PARKED",
                lease_started=None,
                park_count=park_count,
                cooldown_until=cooldown_until,
            )
        elif decision is ParkDecision.DEFER:
            self._sessions[session_id] = replace(session, park_requested=True)
        elif decision is ParkDecision.DENY:
            self._sessions[session_id] = replace(session, park_requested=False)
        self._persist()

    def finish(self, session_id: str, *, task_state: str = "COMPLETED") -> None:
        session = self._get(session_id)
        self._sessions[session_id] = replace(
            session,
            task_state=task_state,
            slot_state="NONE",
            lease_started=None,
            park_requested=False,
        )
        self._persist()

    def adjust_priority(
        self, session_id: str, priority: int, *, actor: str, reason: str
    ) -> None:
        if not actor or not reason:
            raise ValueError("priority adjustment requires actor and reason")
        session = self._get(session_id)
        self._sessions[session_id] = replace(session, priority=priority)
        self.priority_adjustments.append({
            "session_id": session_id,
            "old_priority": session.priority,
            "new_priority": priority,
            "actor": actor,
            "reason": reason,
        })
        self._persist()

    @staticmethod
    def adjust_priority_in_connection(connection, project_id, session_id, priority, *, actor, reason):
        if type(priority) is not int or not -100 <= priority <= 100 or not actor or not reason:
            raise ValueError("priority must be an integer between -100 and 100 with actor and reason")
        row = connection.execute("SELECT state_json FROM scheduler_state WHERE id=1").fetchone()
        state = json.loads(row[0]) if row else {"sessions": {}}
        session = state["sessions"].get(session_id)
        if session is None:
            raise KeyError("session is not scheduled")
        if session["project_id"] != project_id:
            raise PermissionError("session crosses project boundary")
        old_priority = session["priority"]
        session["priority"] = priority
        state.setdefault("priority_adjustments", []).append({
            "session_id": session_id, "old_priority": old_priority,
            "new_priority": priority, "actor": actor, "reason": reason})
        connection.execute("UPDATE scheduler_state SET state_json=? WHERE id=1",
                           (json.dumps(state, sort_keys=True),))
        return SessionMeta(**session)

    def complete_child(self, child_session_id: str, parent_session_id: str) -> None:
        parent = self._get(parent_session_id)
        self._sessions[parent_session_id] = replace(parent, slot_state="RESERVED_FAMILY", family_parent=parent_session_id)
        if child_session_id in self._sessions:
            child = self._sessions[child_session_id]
            self._sessions[child_session_id] = replace(child, slot_state="PARKED", lease_started=None)
        self._persist()

    def watchdog(self, now: int) -> None:
        for session_id, session in tuple(self._sessions.items()):
            if session.slot_state == "ACTIVE" and session.lease_started is not None:
                if now - session.lease_started > self.lease_ttl_seconds:
                    self._sessions[session_id] = replace(session, slot_state="PARKED", lease_started=None)
        self._persist()

    def task_state(self, session_id: str) -> str:
        return self._get(session_id).task_state

    def slot_state(self, session_id: str) -> str:
        return self._get(session_id).slot_state

    def get(self, session_id: str) -> SessionMeta:
        return self._get(session_id)

    def _eligible(self, session_id: str, now: int) -> bool:
        session = self._sessions[session_id]
        return (
            session.slot_state in {"QUEUED", "RESERVED_FAMILY"}
            and now >= session.cooldown_until
        )

    def _active_count(self) -> int:
        return sum(session.slot_state in {"ACTIVE", "DRAINING"} for session in self._sessions.values())

    def _get(self, session_id: str) -> SessionMeta:
        try:
            return self._sessions[session_id]
        except KeyError as error:
            raise KeyError(f"unknown session: {session_id}") from error

    def _initialize_persistence(self) -> None:
        with self._connect() as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS scheduler_state (id INTEGER PRIMARY KEY CHECK (id = 1), state_json TEXT NOT NULL)"
            )

    def _connect(self) -> sqlite3.Connection:
        if self._database_path is None:
            raise RuntimeError("scheduler persistence is not configured")
        connection = sqlite3.connect(self._database_path, timeout=30.0)
        connection.execute("PRAGMA busy_timeout = 30000")
        return connection

    def _persist(self) -> None:
        if self._database_path is None:
            return
        state = {
            "capacity": self.capacity,
            "sessions": {
                session_id: {
                    "session_id": session.session_id,
                    "project_id": session.project_id,
                    "priority": session.priority,
                    "task_state": session.task_state,
                    "slot_state": session.slot_state,
                    "yieldability": session.yieldability,
                    "lease_started": session.lease_started,
                    "park_requested": session.park_requested,
                    "family_parent": session.family_parent,
                    "fencing_token": session.fencing_token,
                    "queued_at": session.queued_at,
                    "park_count": session.park_count,
                    "cooldown_until": session.cooldown_until,
                }
                for session_id, session in self._sessions.items()
            },
            "queue": self._queue,
            "priority_adjustments": self.priority_adjustments,
        }
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO scheduler_state(id, state_json) VALUES (1, ?) "
                "ON CONFLICT(id) DO UPDATE SET state_json = excluded.state_json",
                (json.dumps(state, sort_keys=True),),
            )

    def _load(self) -> None:
        with self._connect() as connection:
            row = connection.execute("SELECT state_json FROM scheduler_state WHERE id = 1").fetchone()
        if row is None:
            return
        state = json.loads(str(row[0]))
        sessions = state.get("sessions", {})
        self._sessions = {
            session_id: SessionMeta(**dict(value))
            for session_id, value in sessions.items()
        }
        self._queue = [str(session_id) for session_id in state.get("queue", [])]
        self.priority_adjustments = [dict(item) for item in state.get("priority_adjustments", [])]
