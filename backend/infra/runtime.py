from __future__ import annotations

from dataclasses import dataclass, replace
import sqlite3
from pathlib import Path
from uuid import uuid4


@dataclass(frozen=True, slots=True)
class RoleDefinition:
    role_id: str
    capabilities: frozenset[str]
    permissions: frozenset[str]


@dataclass(frozen=True, slots=True)
class Profile:
    profile_id: str
    roles: dict[str, RoleDefinition]


@dataclass(frozen=True, slots=True)
class DelegationSpec:
    project_id: str
    parent_task_id: str
    owner_session_id: str
    executor_session_id: str
    relation_type: str
    return_contract: str


@dataclass(frozen=True, slots=True)
class Delegation:
    relation_id: str
    project_id: str
    parent_task_id: str
    owner_session_id: str
    executor_session_id: str
    relation_type: str
    return_contract: str
    state: str
    result: str | None = None


@dataclass(frozen=True, slots=True)
class AgentToolRequest:
    project_id: str
    requester_session_id: str
    capability: str
    input: dict[str, object]


@dataclass(frozen=True, slots=True)
class AgentToolResult:
    project_id: str
    capability: str
    output: dict[str, object]


class Runtime:
    def __init__(self, profile: Profile, database_path: str | Path | None = None) -> None:
        self.profile = profile
        self._database_path = str(database_path) if database_path is not None else None
        self._delegations: dict[str, Delegation] = {}
        self.project_state_writes: list[object] = []
        if self._database_path is not None:
            self._initialize_persistence()
            self._load_delegations()

    def delegate(self, spec: DelegationSpec) -> Delegation:
        if not spec.return_contract.strip():
            raise ValueError("delegation requires a return contract")
        owner = self.profile.roles.get("owner")
        if owner is None or "delegate" not in owner.permissions:
            raise PermissionError("owner lacks delegate authority")
        relation = Delegation(
            relation_id=f"rel_{uuid4().hex}",
            project_id=spec.project_id,
            parent_task_id=spec.parent_task_id,
            owner_session_id=spec.owner_session_id,
            executor_session_id=spec.executor_session_id,
            relation_type=spec.relation_type,
            return_contract=spec.return_contract,
            state="OPEN",
        )
        self._delegations[relation.relation_id] = relation
        self._persist(relation)
        return relation

    def complete(self, relation_id: str, result: str) -> Delegation:
        relation = self._get(relation_id)
        updated = replace(relation, state="WAIT_OWNER_REVIEW", result=result)
        self._delegations[relation_id] = updated
        self._persist(updated)
        return updated

    def review(self, relation_id: str, reviewer_session_id: str, decision: str) -> Delegation:
        relation = self._get(relation_id)
        if reviewer_session_id != relation.owner_session_id:
            raise PermissionError("only owner can review delegation")
        if relation.state != "WAIT_OWNER_REVIEW":
            raise ValueError("delegation is not ready for review")
        if not relation.result or relation.return_contract not in relation.result:
            raise ValueError("return contract is not satisfied")
        if decision not in {"ACCEPT", "REWORK"}:
            raise ValueError(f"unsupported review decision: {decision}")
        updated = replace(relation, state="ACCEPTED" if decision == "ACCEPT" else "REWORK")
        self._delegations[relation_id] = updated
        self._persist(updated)
        return updated

    @staticmethod
    def delegate_in_connection(connection, spec: DelegationSpec, relation_id: str) -> Delegation:
        """Create a relation inside the caller's transaction (the console holds BEGIN IMMEDIATE)."""
        if not spec.return_contract.strip():
            raise ValueError("delegation requires a return contract")
        relation = Delegation(relation_id, spec.project_id, spec.parent_task_id, spec.owner_session_id,
                              spec.executor_session_id, spec.relation_type, spec.return_contract, "OPEN")
        connection.execute(
            "INSERT INTO runtime_delegations(relation_id,project_id,parent_task_id,owner_session_id,"
            "executor_session_id,relation_type,return_contract,state,result) VALUES(?,?,?,?,?,?,?,?,NULL)",
            (relation.relation_id, relation.project_id, relation.parent_task_id, relation.owner_session_id,
             relation.executor_session_id, relation.relation_type, relation.return_contract, relation.state))
        return relation

    @staticmethod
    def finish_in_connection(connection, relation_id: str, state: str, result: str) -> None:
        """Child return: OPEN -> WAIT_OWNER_REVIEW (or FAILED); never ACCEPTED without Parent Review."""
        if state not in {"WAIT_OWNER_REVIEW", "FAILED"}:
            raise ValueError("child return can only await review or fail")
        updated = connection.execute(
            "UPDATE runtime_delegations SET state=?,result=? WHERE relation_id=? AND state='OPEN'",
            (state, result, relation_id)).rowcount
        if updated != 1:
            raise ValueError("delegation is not open")

    @staticmethod
    def review_in_connection(connection, project_id, relation_id, reviewer_session_id, decision):
        row = connection.execute(
            "SELECT relation_id,project_id,parent_task_id,owner_session_id,executor_session_id,"
            "relation_type,return_contract,state,result FROM runtime_delegations WHERE relation_id=?",
            (relation_id,)).fetchone()
        if row is None:
            raise KeyError("unknown delegation")
        relation = Delegation(*tuple(row))
        if relation.project_id != project_id:
            raise PermissionError("delegation crosses project boundary")
        if reviewer_session_id != relation.owner_session_id:
            raise PermissionError("only owner can review delegation")
        if relation.state != "WAIT_OWNER_REVIEW":
            raise ValueError("delegation is not ready for review")
        if decision not in {"ACCEPT", "REWORK"}:
            raise ValueError("unsupported review decision")
        if not relation.result or relation.return_contract not in relation.result:
            raise ValueError("return contract is not satisfied")
        updated = replace(relation, state="ACCEPTED" if decision == "ACCEPT" else "REWORK")
        connection.execute("UPDATE runtime_delegations SET state=? WHERE relation_id=? AND state=?",
                           (updated.state, relation_id, relation.state))
        return updated

    def invoke_agent_tool(self, request: AgentToolRequest) -> AgentToolResult:
        backend = str(request.input.get("backend", "local_176b"))
        resource_domain = str(request.input.get("resource_domain", backend))
        return AgentToolResult(
            request.project_id,
            request.capability,
            {
                "accepted": True,
                "candidate_only": True,
                "backend": backend,
                "resource_domain": resource_domain,
                "writes_project_state": False,
            },
        )

    def _get(self, relation_id: str) -> Delegation:
        try:
            return self._delegations[relation_id]
        except KeyError as error:
            raise KeyError(f"unknown delegation: {relation_id}") from error

    def _connect(self) -> sqlite3.Connection:
        if self._database_path is None:
            raise RuntimeError("runtime persistence is not configured")
        connection = sqlite3.connect(self._database_path, timeout=30.0)
        connection.execute("PRAGMA busy_timeout = 30000")
        return connection

    def _initialize_persistence(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS runtime_delegations (
                    relation_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    parent_task_id TEXT NOT NULL,
                    owner_session_id TEXT NOT NULL,
                    executor_session_id TEXT NOT NULL,
                    relation_type TEXT NOT NULL,
                    return_contract TEXT NOT NULL,
                    state TEXT NOT NULL,
                    result TEXT
                )
                """
            )

    def _persist(self, relation: Delegation) -> None:
        if self._database_path is None:
            return
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO runtime_delegations(
                    relation_id, project_id, parent_task_id, owner_session_id,
                    executor_session_id, relation_type, return_contract, state, result
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(relation_id) DO UPDATE SET
                    state = excluded.state, result = excluded.result
                """,
                (
                    relation.relation_id, relation.project_id, relation.parent_task_id,
                    relation.owner_session_id, relation.executor_session_id,
                    relation.relation_type, relation.return_contract, relation.state,
                    relation.result,
                ),
            )

    def _load_delegations(self) -> None:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT relation_id, project_id, parent_task_id, owner_session_id, "
                "executor_session_id, relation_type, return_contract, state, result "
                "FROM runtime_delegations"
            ).fetchall()
        self._delegations = {
            str(row[0]): Delegation(
                relation_id=str(row[0]), project_id=str(row[1]), parent_task_id=str(row[2]),
                owner_session_id=str(row[3]), executor_session_id=str(row[4]),
                relation_type=str(row[5]), return_contract=str(row[6]), state=str(row[7]),
                result=None if row[8] is None else str(row[8]),
            )
            for row in rows
        }
