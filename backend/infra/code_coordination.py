from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, replace
from pathlib import Path
from uuid import uuid4


@dataclass(frozen=True, slots=True)
class SpawnContract:
    parent_session_id: str
    parent_task_id: str
    child_role: str
    task_spec: str
    dependency: str
    return_contract: str
    context_policy: str = "task_scoped"
    resource_policy: str = "shared_pool"
    write_scope: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Child:
    child_id: str
    contract: SpawnContract
    state: str = "CREATED"
    result: dict[str, object] | None = None

    @property
    def context_policy(self) -> str:
        return self.contract.context_policy


@dataclass(frozen=True, slots=True)
class WriteIntent:
    change_id: str
    task_id: str
    actor_session_id: str
    target_refs: tuple[str, ...]
    intent: str
    base_revision: str


@dataclass(frozen=True, slots=True)
class ChangeRecord:
    change_id: str
    task_id: str
    actor_session_id: str
    worktree: str
    branch: str
    base_revision: str
    intent: str
    target_refs: tuple[str, ...] = ()
    implements: tuple[str, ...] = ()
    resolves: tuple[str, ...] = ()
    assumptions: tuple[str, ...] = ()
    tests: tuple[str, ...] = ()
    commit_sha: str | None = None
    integrator_session_id: str | None = None
    status: str = "planned"


@dataclass(frozen=True, slots=True)
class ConflictPackage:
    conflict_id: str
    left_change_id: str
    right_change_id: str
    semantic_summary: str
    preferred_strategy: str
    original_agent_available: bool


class Coordination:
    def __init__(self, database_path: str | Path | None = None) -> None:
        self._children: dict[str, Child] = {}
        self._intents: dict[str, WriteIntent] = {}
        self._changes: dict[str, ChangeRecord] = {}
        self._database_path = Path(database_path) if database_path is not None else None
        if self._database_path is not None:
            self._database_path.parent.mkdir(parents=True, exist_ok=True)
            self._initialize_database()
            self._load_persisted_state()

    def spawn(self, contract: SpawnContract) -> Child:
        if not contract.return_contract.strip():
            raise ValueError("child return contract is required")
        child = Child(f"child_{uuid4().hex}", contract)
        self._children[child.child_id] = child
        return child

    def complete(self, child_id: str, result: dict[str, object]) -> Child:
        child = self._get_child(child_id)
        if not result.get("artifact_refs") or not result.get("tests"):
            raise ValueError("child result requires artifacts and tests")
        updated = replace(child, state="WAIT_PARENT_REVIEW", result=result)
        self._children[child_id] = updated
        return updated

    def review(self, child_id: str, reviewer_session_id: str, decision: str) -> Child:
        child = self._get_child(child_id)
        if reviewer_session_id != child.contract.parent_session_id:
            raise PermissionError("only parent can review child")
        if child.state != "WAIT_PARENT_REVIEW":
            raise ValueError("child is not waiting for review")
        state = {"ACCEPT": "ACCEPTED", "REWORK": "REWORK", "REJECT": "REJECTED"}.get(decision)
        if state is None:
            raise ValueError(f"unsupported review decision: {decision}")
        updated = replace(child, state=state)
        self._children[child_id] = updated
        return updated

    def get(self, child_id: str) -> Child:
        return self._get_child(child_id)

    def register_intent(self, intent: WriteIntent) -> None:
        self._intents[intent.change_id] = intent
        self._persist("write_intents", intent.change_id, intent)

    def start_change(self, record: ChangeRecord) -> ChangeRecord:
        intent = self._intents.get(record.change_id)
        if intent is None or intent.base_revision != record.base_revision:
            raise ValueError("change provenance does not match write intent")
        if record.target_refs and record.target_refs != intent.target_refs:
            raise ValueError("change target refs do not match write intent")
        active = replace(record, status="active")
        self._changes[record.change_id] = active
        self._persist("change_records", record.change_id, active)
        return active

    def integrate_change(
        self,
        change_id: str,
        integrator_session_id: str,
        commit_sha: str,
        tests: tuple[str, ...],
    ) -> ChangeRecord:
        change = self._changes[change_id]
        if change.status != "active":
            raise ValueError("only active changes can be integrated")
        if change.integrator_session_id != integrator_session_id:
            raise PermissionError("only the declared integrator can enter mainline")
        if not commit_sha.strip():
            raise ValueError("integrated change requires commit sha")
        if not tests:
            raise ValueError("integrated change requires tests")
        integrated = replace(change, status="integrated", commit_sha=commit_sha, tests=tests)
        self._changes[change_id] = integrated
        self._persist("change_records", change_id, integrated)
        return integrated

    def get_change(self, change_id: str) -> ChangeRecord:
        try:
            return self._changes[change_id]
        except KeyError as error:
            raise KeyError(f"unknown change: {change_id}") from error

    def check_target(self, change_id: str, current_revision: str) -> None:
        change = self._changes[change_id]
        if current_revision != change.base_revision:
            raise RuntimeError("stale target revision")

    @staticmethod
    def resolve_conflict(conflict: ConflictPackage, original_agent_response: str) -> str:
        if "unavailable" in original_agent_response.lower():
            return "5.2"
        return "5.1"

    def _get_child(self, child_id: str) -> Child:
        try:
            return self._children[child_id]
        except KeyError as error:
            raise KeyError(f"unknown child: {child_id}") from error

    def _initialize_database(self) -> None:
        assert self._database_path is not None
        with sqlite3.connect(self._database_path) as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS write_intents (
                    change_id TEXT PRIMARY KEY,
                    payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS change_records (
                    change_id TEXT PRIMARY KEY,
                    payload TEXT NOT NULL
                );
                """
            )

    def _load_persisted_state(self) -> None:
        assert self._database_path is not None
        with sqlite3.connect(self._database_path) as connection:
            for change_id, payload in connection.execute("SELECT change_id, payload FROM write_intents"):
                data = json.loads(payload)
                data["target_refs"] = tuple(data["target_refs"])
                self._intents[change_id] = WriteIntent(**data)
            for change_id, payload in connection.execute("SELECT change_id, payload FROM change_records"):
                data = json.loads(payload)
                for field in ("target_refs", "implements", "resolves", "assumptions", "tests"):
                    data[field] = tuple(data[field])
                self._changes[change_id] = ChangeRecord(**data)

    def _persist(self, table: str, change_id: str, value: WriteIntent | ChangeRecord) -> None:
        if self._database_path is None:
            return
        payload = json.dumps({field: getattr(value, field) for field in value.__dataclass_fields__})
        with sqlite3.connect(self._database_path) as connection:
            connection.execute(
                f"INSERT INTO {table}(change_id, payload) VALUES (?, ?) "
                "ON CONFLICT(change_id) DO UPDATE SET payload = excluded.payload",
                (change_id, payload),
            )
