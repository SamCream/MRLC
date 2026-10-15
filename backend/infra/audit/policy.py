from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import IntEnum
from pathlib import Path


class RiskLevel(IntEnum):
    R0 = 0
    R1 = 1
    R2 = 2
    R3 = 3
    R4 = 4


@dataclass(frozen=True, slots=True)
class ActionRequest:
    actor_id: str
    profile_id: str
    project_id: str
    action: str
    target: str
    environment: str
    authority: tuple[str, ...]
    budget_cents: int


@dataclass(frozen=True, slots=True)
class PermissionDecision:
    decision_id: str
    risk: RiskLevel
    allowed: bool
    reason: str
    authorization_ref: str | None


@dataclass(frozen=True, slots=True)
class AuditEntry:
    decision_id: str
    project_id: str
    actor_id: str
    action: str
    target: str
    risk: RiskLevel
    result: str
    reason: str
    authorization_ref: str | None
    recorded_at: datetime


class AuditStore:
    def __init__(self, database: str | Path) -> None:
        self._database = Path(database)
        self._database.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS permission_audit (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    decision_id TEXT NOT NULL UNIQUE,
                    project_id TEXT NOT NULL,
                    actor_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    target TEXT NOT NULL,
                    risk INTEGER NOT NULL,
                    result TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    authorization_ref TEXT,
                    request_json TEXT NOT NULL,
                    recorded_at TEXT NOT NULL
                )
                """
            )

    def append(self, request: ActionRequest, decision: PermissionDecision) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO permission_audit(
                    decision_id, project_id, actor_id, action, target, risk, result,
                    reason, authorization_ref, request_json, recorded_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    decision.decision_id, request.project_id, request.actor_id, request.action,
                    request.target, int(decision.risk),
                    "ALLOWED" if decision.allowed else "DENIED", decision.reason,
                    decision.authorization_ref, json.dumps({
                        "profile_id": request.profile_id,
                        "environment": request.environment,
                        "authority": list(request.authority),
                        "budget_cents": request.budget_cents,
                    }, sort_keys=True), datetime.now(UTC).isoformat(),
                ),
            )

    def list(self, project_id: str) -> list[AuditEntry]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT decision_id, project_id, actor_id, action, target, risk, result,
                       reason, authorization_ref, recorded_at
                FROM permission_audit WHERE project_id = ? ORDER BY sequence
                """,
                (project_id,),
            ).fetchall()
        return [
            AuditEntry(
                str(row["decision_id"]), str(row["project_id"]), str(row["actor_id"]),
                str(row["action"]), str(row["target"]), RiskLevel(int(row["risk"])),
                str(row["result"]), str(row["reason"]), row["authorization_ref"],
                datetime.fromisoformat(str(row["recorded_at"])),
            )
            for row in rows
        ]

    def export_jsonl(self, project_id: str) -> str:
        return "\n".join(
            json.dumps(
                {
                    "decision_id": entry.decision_id,
                    "project_id": entry.project_id,
                    "actor_id": entry.actor_id,
                    "action": entry.action,
                    "target": entry.target,
                    "risk": int(entry.risk),
                    "result": entry.result,
                    "reason": entry.reason,
                    "authorization_ref": entry.authorization_ref,
                    "recorded_at": entry.recorded_at.isoformat(),
                },
                sort_keys=True,
            )
            for entry in self.list(project_id)
        )

    def delete(self, decision_id: str) -> None:
        raise RuntimeError(f"permission audit is append-only: {decision_id}")

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._database)
        connection.row_factory = sqlite3.Row
        return connection


class RiskPolicy:
    _R4_ACTIONS = frozenset({"publish", "delete", "destroy", "charge", "merge_main"})
    _R3_ACTIONS = frozenset({"external_send", "secret_export", "production_write"})
    _R2_AUTHORITIES = {
        "cancel_child": "task.cancel",
        "restore_backup": "infra.restore",
        "change_priority": "task.prioritize",
    }

    def __init__(self, audit: AuditStore) -> None:
        self._audit = audit

    def authorize(
        self, request: ActionRequest, *, user_authorization: str | None = None
    ) -> PermissionDecision:
        risk = self.classify(request)
        allowed = True
        reason = "policy allows action"
        if risk >= RiskLevel.R3 and not user_authorization:
            allowed = False
            reason = "explicit user authorization required"
        elif risk == RiskLevel.R2:
            required = self._R2_AUTHORITIES.get(request.action)
            if required is not None and required not in request.authority:
                allowed = False
                reason = f"missing role authority: {required}"
        decision = PermissionDecision(
            f"pdec_{uuid.uuid4().hex}", risk, allowed, reason,
            user_authorization if allowed else None,
        )
        self._audit.append(request, decision)
        return decision

    def classify(self, request: ActionRequest) -> RiskLevel:
        action = request.action.lower()
        environment = request.environment.lower()
        target = request.target.lower()
        if action in self._R4_ACTIONS or "public" in target or action.startswith("delete_"):
            return RiskLevel.R4
        if action in self._R3_ACTIONS or environment == "production":
            return RiskLevel.R3
        if action in self._R2_AUTHORITIES:
            return RiskLevel.R2
        if action.startswith(("write", "edit", "create")):
            return RiskLevel.R1
        return RiskLevel.R0
