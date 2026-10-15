from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from infra.audit.redaction import redact_value
from infra.delegation_store import DelegationStore
from infra.evented_infra import EventedInfra
from infra.evidence_graph import evidence_chains
from infra.roles import load_profile, role_catalogue

TASK_STATE_LABELS = {"CREATED": "待规划", "RUNNABLE": "就绪", "RUNNING": "执行中",
                     "BLOCKED": "阻塞", "COMPLETED": "执行完成", "FAILED": "失败"}
SLOT_STATE_LABELS = {"ACTIVE": "占用", "PARKED": "已停泊", "READY": "排队",
                     "NONE": "资源状态未确认", "UNKNOWN": "未知", "DRAINING": "释放中"}
LIFECYCLE_LABELS = {"ACTIVE": "活动", "ARCHIVED": "归档", "CLOSED": "关闭"}

# Only explicitly owned, project-scoped tables may be projected to clients.
TABLES = {
    "tasks": "tasks", "sessions": "sessions", "issues": "bank_issues",
    "decisions": "bank_decisions", "milestones": "bank_progress",
    "delegations": "runtime_delegations", "frontier": "frontier_requests",
    "candidates": "a2cm_candidates", "artifacts": "artifacts",
    "memory": "memories", "audit": "permission_audit",
    "usage": "gateway_invocations",
    "operations": "console_jobs", "delegate_runs": "frontier_delegate_runs",
    "messages": "conversation_messages", "members": "conversation_members",
    "turns": "conversation_turns",
}


def _redact_whole_token(value: str, secrets: tuple[str, ...]) -> str:
    # Only redact a secret when it appears as a whole token. A short credential
    # must not corrupt unrelated values that merely contain it as a substring
    # (for example a CSRF token that happens to embed the identity key).
    for secret in secrets:
        if not secret:
            continue
        value = re.sub(r"(?<![\w.-])" + re.escape(secret) + r"(?![\w.-])", "[REDACTED]", value)
    return value


def safe_value(value: Any, secrets: tuple[str, ...] = ()) -> Any:
    value = redact_value(value)
    if isinstance(value, dict):
        return {k: "[REDACTED]" if k.lower() in {
            "token", "api_key", "secret", "password", "credential", "authorization"
        } else safe_value(v, secrets) for k, v in value.items()}
    if isinstance(value, list):
        return [safe_value(v, secrets) for v in value]
    if isinstance(value, str):
        value = _redact_whole_token(value, secrets)
    return value


class ConsoleModel:
    def __init__(self, database_path: str | Path) -> None:
        self.database_path = str(database_path)
        self.infra = EventedInfra(database_path)
        self._initialize_domain_schema()

    def _initialize_domain_schema(self) -> None:
        # Frontier and Runtime own these tables, but a console read must not depend on
        # which service happened to start first. Creating the schema here keeps the
        # projection honest: an empty domain reads as empty, not as unavailable.
        with self.connect() as connection:
            connection.executescript("""
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
                );
                CREATE TABLE IF NOT EXISTS frontier_requests (
                    request_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    owner_session_id TEXT NOT NULL,
                    task_owner_session_id TEXT NOT NULL,
                    child_session_id TEXT,
                    advice TEXT,
                    return_contract TEXT,
                    review_state TEXT NOT NULL,
                    budget_used INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS artifacts (
                    artifact_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    media_type TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    content BLOB NOT NULL,
                    sensitivity TEXT NOT NULL DEFAULT 'project'
                );
                CREATE TABLE IF NOT EXISTS memories (
                    memory_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    role_scope TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    content TEXT NOT NULL,
                    source_event_refs TEXT NOT NULL,
                    valid_from TEXT NOT NULL,
                    valid_to TEXT,
                    confidence REAL NOT NULL,
                    status TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS gateway_invocations (
                    idempotency_key TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    model TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    invocation_id TEXT NOT NULL,
                    fallback_reason TEXT,
                    status TEXT NOT NULL DEFAULT 'COMPLETED',
                    provider_request_id TEXT,
                    usage_input INTEGER NOT NULL DEFAULT 0,
                    usage_output INTEGER NOT NULL DEFAULT 0
                );
            """)
            DelegationStore.initialize(connection)

    def connect(self):
        connection = sqlite3.connect(self.database_path, timeout=30)
        connection.row_factory = sqlite3.Row
        return connection

    @staticmethod
    def _columns(connection, table):
        return [r[1] for r in connection.execute(f"PRAGMA table_info({table})")
                if r[1] not in {"snapshot", "content"} or table in {"memories", "conversation_messages"}]

    @staticmethod
    def _sanitize_rows(key, rows):
        for row in rows:
            if key == "operations":
                row.pop("authorization_ref", None)
            if key == "tasks":
                row["task_state"] = row.pop("state")
            if key == "candidates":
                for field in ("content", "statement", "evidence", "evidence_refs_json", "source", "rationale"):
                    if field in row:
                        row[field] = "[PRIVATE]"
            if key == "memory" and row.get("sensitivity") not in {"public", "low"}:
                row["content"] = "[PRIVATE]"
        return safe_value(rows)

    def rows_page(self, allowed, project_id, key, after=0, limit=100):
        if key not in TABLES or not project_id or after < 0 or not 1 <= limit <= 500:
            raise ValueError("invalid entity page")
        with self.connect() as conn:
            conn.execute("BEGIN")
            if ("*" not in allowed and project_id not in allowed) or not conn.execute(
                    "SELECT 1 FROM projects WHERE project_id=?", (project_id,)).fetchone():
                raise PermissionError("project access denied")
            table = TABLES[key]
            columns = self._columns(conn, table)
            if "project_id" not in columns:
                raise ValueError("domain not available")
            selected = ",".join(columns)
            rows = [dict(row) for row in conn.execute(
                f"SELECT rowid AS page_cursor,{selected} FROM {table} WHERE project_id=? AND rowid>? "
                "ORDER BY rowid LIMIT ?", (project_id, after, limit + 1))]
            has_more = len(rows) > limit
            rows = rows[:limit]
            cursor = rows[-1]["page_cursor"] if rows else after
            for row in rows:
                row.pop("page_cursor")
            if key == "tasks":
                self._task_dependencies(conn, project_id, rows)
            return {"rows": self._sanitize_rows(key, rows), "cursor": cursor, "has_more": has_more}

    def evidence_graph(self, allowed, project_id):
        if not project_id:
            raise ValueError("project_id required")
        with self.connect() as conn:
            conn.execute("BEGIN")
            if ("*" not in allowed and project_id not in allowed) or not conn.execute(
                    "SELECT 1 FROM projects WHERE project_id=?", (project_id,)).fetchone():
                raise PermissionError("project access denied")
            return safe_value(evidence_chains(conn, project_id))

    @staticmethod
    def _task_dependencies(connection, project_id, rows):
        tasks = {row["task_id"]: row for row in rows}
        for task in tasks.values():
            task["dependencies"] = []
        for dependency in connection.execute(
            "SELECT deps.task_id, dep.task_id, dep.title, dep.state FROM task_dependencies deps "
            "JOIN tasks task ON task.task_id=deps.task_id "
            "JOIN tasks dep ON dep.task_id=deps.dependency_task_id "
            "WHERE task.project_id=? AND dep.project_id=? ORDER BY deps.task_id, dep.task_id",
            (project_id, project_id),
        ):
            if dependency[0] in tasks:
                tasks[dependency[0]]["dependencies"].append({
                    "task_id": dependency[1], "title": dependency[2], "task_state": dependency[3],
                })
        for parent in connection.execute(
            "SELECT task.task_id, parent.task_id, parent.title, parent.state FROM tasks task "
            "JOIN tasks parent ON parent.task_id=task.parent_task_id "
            "WHERE task.project_id=? AND parent.project_id=? ORDER BY task.task_id",
            (project_id, project_id),
        ):
            if parent[0] in tasks:
                tasks[parent[0]]["parent_task"] = {
                    "task_id": parent[1], "title": parent[2], "task_state": parent[3],
                }

    def events_page(self, allowed, project_id, after=0, limit=100):
        if not project_id or not 1 <= limit <= 500:
            raise ValueError("project and bounded page size required")
        with self.connect() as conn:
            conn.execute("BEGIN")
            project = conn.execute("SELECT 1 FROM projects WHERE project_id=?", (project_id,)).fetchone()
            if not project or ("*" not in allowed and project_id not in allowed):
                raise PermissionError("project access denied")
            cursor = conn.execute("SELECT COALESCE(MAX(sequence),0) FROM events WHERE project_id=?",
                                  (project_id,)).fetchone()[0]
            if after < 0 or after > cursor:
                raise ValueError("invalid cursor; resnapshot required")
            rows = [dict(row) for row in conn.execute(
                "SELECT sequence,event_id,event_type,payload,actor_id,session_id,task_id,"
                "correlation_id,occurred_at FROM events WHERE project_id=? AND sequence>? "
                "ORDER BY sequence LIMIT ?", (project_id, after, limit))]
            for row in rows:
                row["payload"] = json.loads(row["payload"])
            next_cursor = rows[-1]["sequence"] if rows else after
            return safe_value({"events": self._private_events(rows), "cursor": next_cursor,
                               "snapshot_cursor": cursor, "has_more": next_cursor < cursor})

    @staticmethod
    def _conversation_turn_phase(connection, project_id, result):
        """Attach the owning turn's phase to each project message.

        The group chat reads one turn at a time; a client needs to know whether a role message's
        turn is still being routed/assigned/running (not yet cuttable) or finished, and it must
        come from the persisted turn rather than from any client-side timing guess.
        """
        messages = result.get("messages")
        if not messages:
            return
        if not connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                                  "AND name='conversation_turns'").fetchone():
            return
        phases = {row["turn_id"]: row["phase"] for row in connection.execute(
            "SELECT turn_id, phase FROM conversation_turns WHERE project_id=?", (project_id,))}
        for message in messages:
            message["turn_phase"] = phases.get(message.get("turn_id"))

    @staticmethod
    def _private_events(events):
        for event in events:
            if event["event_type"].startswith(("OBSERVATION_", "BELIEF_", "MEMORY_", "CANDIDATE_")):
                event["payload"] = {key: value if key.endswith("_id") or key in {"status", "source"}
                                    else "[PRIVATE]" for key, value in event["payload"].items()}
        return events

    @staticmethod
    def _role_catalogue(connection, project_id):
        """Project roles a client may see: Profile baseline plus this project's overrides.

        Kept read-only; role edits go through the `configure_role` command so permissions,
        audit, idempotency, and CAS stay on the one Infra write path.
        """
        if not connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                                  "AND name='conversation_role_overrides'").fetchone():
            return []
        overrides = list(connection.execute(
            "SELECT * FROM conversation_role_overrides WHERE project_id=? ORDER BY role_id", (project_id,)))
        grants = (list(connection.execute(
            "SELECT * FROM conversation_role_grants WHERE project_id=? ORDER BY role_id,authority",
            (project_id,))) if connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='conversation_role_grants'").fetchone()
            else [])
        return role_catalogue(load_profile(), overrides, grants)

    def _command_capabilities(self, overrides: dict[str, dict[str, Any]] | None) -> dict[str, dict[str, Any]]:
        base = {
            "create_backup": {"available": True, "risk": "R3", "scope": "workspace"},
            "restore_drill": {"available": True, "risk": "R3", "scope": "new-instance-only"},
            "frontier_consult": {"available": False, "reason": "frontier advisor not bound"},
            "frontier_delegate": {"available": False, "reason": "delegation switch disabled for this console"},
            "cancel_child": {"available": False, "reason": "frontier delegation executor not running"},
            "start_dev": {"available": False, "reason": "project execution binding not configured"},
            "start_a2cm": {"available": False, "reason": "domain execution binding not configured"},
            "review_candidate": {"available": True, "risk": "R2", "scope": "project",
                                 "requires": "bound active A2CM Primary session"},
            "configure_role": {"available": True, "risk": "R2", "scope": "project"},
            "grant_role": {"available": True, "risk": "R2", "scope": "project"},
        }
        return base | (overrides or {})

    def snapshot(self, allowed: tuple[str, ...], project_id: str | None = None,
                 connection=None, capabilities: dict[str, dict[str, Any]] | None = None,
                 admin: dict[str, Any] | None = None) -> dict[str, Any]:
        if connection is None:
            with self.connect() as conn:
                conn.execute("BEGIN")
                return self.snapshot(allowed, project_id, conn, capabilities=capabilities, admin=admin)
        projects = [dict(row) for row in connection.execute("SELECT * FROM projects ORDER BY project_id")
                    if "*" in allowed or row["project_id"] in allowed]
        result: dict[str, Any] = {"schema_version": "ui.v1", "project_id": project_id,
                                  "admin": {} if admin is None else admin,
                                  "projects": projects,
                                  "command_capabilities": self._command_capabilities(capabilities)}
        if project_id:
            if not any(p["project_id"] == project_id for p in projects):
                raise PermissionError("project access denied")
            tables = {r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            result["project"] = next(p for p in projects if p["project_id"] == project_id)
            result["capabilities"] = {}
            for key, table in TABLES.items():
                columns = self._columns(connection, table) if table in tables else []
                available = "project_id" in columns
                result["capabilities"][key] = {"available": available,
                                                "reason": None if available else "领域未初始化或缺少项目隔离合同"}
                rows = [dict(r) for r in connection.execute(
                    f"SELECT rowid AS page_cursor,{','.join(columns)} FROM {table} WHERE project_id = ? ORDER BY rowid LIMIT 501", (project_id,)
                )] if available else []
                result["capabilities"][key]["cursor"] = rows[min(len(rows), 500) - 1]["page_cursor"] if rows else 0
                result["capabilities"][key]["total"] = connection.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE project_id=?", (project_id,)).fetchone()[0] if available else None
                for row in rows:
                    row.pop("page_cursor")
                result[key] = self._sanitize_rows(key, rows[:500])
                result["capabilities"][key]["truncated"] = len(rows) > 500
            self._task_dependencies(connection, project_id, result["tasks"])
            self._conversation_turn_phase(connection, project_id, result)
            scheduler = connection.execute("SELECT state_json FROM scheduler_state WHERE id=1").fetchone() \
                if "scheduler_state" in tables else None
            scheduler = json.loads(scheduler[0]) if scheduler else {"sessions": {}, "queue": []}
            visible_sessions = {key: value for key, value in scheduler["sessions"].items()
                                if value["project_id"] == project_id}
            result["scheduler"] = {"sessions": {
                k: v for k, v in scheduler["sessions"].items() if v["project_id"] == project_id},
                "queue": [sid for sid in scheduler["queue"] if sid in {
                    k for k, v in scheduler["sessions"].items() if v["project_id"] == project_id}]}
            result["scheduler"].update({
                "source": "Infra scheduler_state", "capacity": scheduler.get("capacity") if "capacity" in scheduler else None,
                "status": "AVAILABLE" if "capacity" in scheduler else "UNKNOWN",
                "project_active": sum(value["slot_state"] in {"ACTIVE", "DRAINING"} for value in visible_sessions.values()),
                # Workspace-wide occupancy is a read-model fact owned by Infra; visibility is
                # decided by the caller's authority, never by opening a database connection in the UI.
                "global_active": sum(value["slot_state"] in {"ACTIVE", "DRAINING"}
                                     for value in scheduler["sessions"].values()) if "*" in allowed else None,
                "reason": None if "capacity" in scheduler else "尚未采集持久化容量；不能推断空闲",
            })
            for session in result["sessions"]:
                session["slot_state"] = result["scheduler"]["sessions"].get(
                    session["session_id"], {}).get("slot_state", "NONE")
            result["role_catalogue"] = self._role_catalogue(connection, project_id)
            result["cursor"] = connection.execute(
                "SELECT COALESCE(MAX(sequence),0) FROM events WHERE project_id=?", (project_id,)
            ).fetchone()[0]
            result["events"] = [dict(row) for row in connection.execute(
                "SELECT sequence,event_id,event_type,payload,actor_id,session_id,task_id,"
                "correlation_id,occurred_at FROM events WHERE project_id=? ORDER BY sequence DESC LIMIT 100",
                (project_id,))][::-1]
            for event in result["events"]:
                event["payload"] = json.loads(event["payload"])
            result["events"] = self._private_events(result["events"])
            # Personal cognitive content is withheld until field-level consent is available.
            for candidate in result["candidates"]:
                for field in ("content", "evidence", "evidence_refs", "source", "rationale"):
                    if field in candidate:
                        candidate[field] = "[PRIVATE]"
            for memory in result["memory"]:
                if memory.get("sensitivity") not in {"public", "low"}:
                    memory["content"] = "[PRIVATE]"
            for session in result["sessions"]:
                session.pop("snapshot", None)
        else:
            result["cursor"] = sum(connection.execute(
                "SELECT COALESCE(MAX(sequence),0) FROM events WHERE project_id=?", (p["project_id"],)
            ).fetchone()[0] for p in projects)
        # Capability, admin and authority-gated read views are not domain state: they must not
        # move the CAS revision, or two reads of the same database through different scopes would
        # disagree and every command would look like a concurrent edit.
        binding_reports = {"command_capabilities": result.pop("command_capabilities"),
                           "admin": result.pop("admin")}
        scheduler_view = result.get("scheduler")
        if isinstance(scheduler_view, dict):
            scheduler_view.pop("global_active")
        result["revision"] = hashlib.sha256(
            json.dumps(result, sort_keys=True, ensure_ascii=True).encode()).hexdigest()
        if isinstance(scheduler_view, dict):
            scheduler_view["global_active"] = sum(
                value["slot_state"] in {"ACTIVE", "DRAINING"}
                for value in scheduler_view["sessions"].values()) if "*" in allowed else None
        result.update(binding_reports)
        result["server_time"] = datetime.now(timezone.utc).isoformat()
        return safe_value(result)


def workspace_snapshot(database_path, allowed=("*",)):
    return ConsoleModel(database_path).snapshot(tuple(allowed))


def project_overview(database_path, project_id, allowed=("*",)):
    return ConsoleModel(database_path).snapshot(tuple(allowed), project_id)
