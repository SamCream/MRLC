"""Compatibility bridge from the Phase 8 FastAPI projection to the migrated Infra core.

The React/API contract remains unchanged.  Infra owns replayable domain state,
consistency primitives, memory/bank registries and scheduler state; the existing
`objects` table remains a compatibility projection for the current UI.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from infra.events import Event, EventStore
from infra.projects import Project, ProjectRegistry
from infra.tasks import Task, TaskRegistry
from infra.sessions import Session, SessionRegistry
from infra.memory import MemoryStore
from infra.project_bank import Bank
from infra.consistency import ConsistencyStore
from infra.scheduler import Scheduler
from infra.history import HistoryStore
from infra.continuity import ContinuityStore
from infra.mcp import McpRegistry
from infra.skills import SkillRegistry
from infra.worker_runtime_state import WorkerRunStore
from infra.replay import replay_project


_TASK_STATE = {
    "todo": "CREATED",
    "queued": "RUNNABLE",
    "running": "RUNNING",
    "blocked": "BLOCKED",
    "failed": "BLOCKED",
    "cancelled": "BLOCKED",
    "review": "RUNNING",
    "accepted": "COMPLETED",
}


class InfraBridge:
    """Makes the migrated Infra core authoritative without changing the web API."""

    def __init__(self, store, database_path: str | Path, *, scheduler_capacity: int = 2) -> None:
        self.store = store
        self.database_path = str(database_path)
        self.events = EventStore(database_path)
        self.projects = ProjectRegistry(database_path)
        self.tasks = TaskRegistry(database_path)
        self.sessions = SessionRegistry(database_path)
        self.memory = MemoryStore(database_path)
        self.bank = Bank(database_path)
        self.consistency = ConsistencyStore(database_path)
        self.scheduler = Scheduler(capacity=scheduler_capacity, database_path=database_path)
        self.history = HistoryStore(database_path)
        self.continuity = ContinuityStore(database_path)
        self.mcp = McpRegistry(database_path)
        self.skills = SkillRegistry(database_path)
        self.worker_runs = WorkerRunStore(database_path)
        store.infra_bridge = self
        self.reconcile_projection()

    @staticmethod
    def _key(prefix: str, body: dict[str, Any]) -> str:
        material=json.dumps(body,ensure_ascii=False,sort_keys=True,separators=(",",":"))
        return f"{prefix}:{hashlib.sha256(material.encode()).hexdigest()[:24]}"

    def _append(self, project_id: str, event_type: str, payload: dict[str, Any], *,
                actor_id: str, idempotency_key: str, task_id: str | None = None) -> None:
        event_id="evt_"+hashlib.sha256(idempotency_key.encode()).hexdigest()[:32]
        self.events.append_in_connection(self.store.conn, Event(
            event_id=event_id,
            project_id=project_id,
            event_type=event_type,
            payload=payload,
            occurred_at=datetime.now(timezone.utc),
            idempotency_key=idempotency_key,
            actor_type="compat-api",
            actor_id=actor_id or "system",
            task_id=task_id,
            correlation_id=idempotency_key,
        ))

    def reconcile_projection(self) -> None:
        """Import existing Phase 8 projections into Infra idempotently."""
        with self.store.tx():
            projects=self.store.all("project")
            for item in projects:
                self._sync_project("migration", item)
            tasks=self.store.all("task")
            for item in tasks:
                self._sync_task("migration", item, dependencies=False)
            for item in tasks:
                self._sync_dependencies(item)
            for agent in self.store.all("agent"):
                self._sync_agent("migration", agent)

    def sync_change(self, source: str, kind: str, body: dict, project_id=None, task_id=None) -> None:
        if kind == "project":
            self._sync_project(source, body)
        elif kind == "task":
            self._sync_task(source, body)
        elif kind == "agent":
            self._sync_agent(source, body)
        elif project_id and kind in {"artifact", "evidenceLink", "approval", "message", "role"}:
            payload={"kind":kind,"id":body.get("id"),"projection":body}
            self._append(project_id, f"PROJECTION_{kind.upper()}_CHANGED", payload,
                         actor_id=source, idempotency_key=self._key(f"projection:{kind}:{body.get('id')}",body),
                         task_id=task_id)

    def _sync_project(self, source: str, item: dict) -> None:
        pid=item["id"]
        row=self.store.conn.execute("SELECT project_id,name FROM projects WHERE project_id=?",(pid,)).fetchone()
        if row is None:
            self.projects.create_in_connection(self.store.conn, Project(pid,item["name"]))
            self._append(pid,"PROJECT_CREATED",{"project_id":pid,"name":item["name"]},
                         actor_id=source,idempotency_key=f"compat:project:{pid}:created")
        elif row["name"] != item["name"]:
            self.store.conn.execute("UPDATE projects SET name=? WHERE project_id=?",(item["name"],pid))
            self._append(pid,"PROJECT_UPDATED",{"project_id":pid,"name":item["name"]},
                         actor_id=source,idempotency_key=self._key(f"compat:project:{pid}:updated",item))
        if item.get("status") == "archived":
            self._append(pid,"PROJECT_ARCHIVED",{"project_id":pid,"reason":item.get("archiveReason","")},
                         actor_id=source,idempotency_key=self._key(f"compat:project:{pid}:archived",item))

    def _sync_task(self, source: str, item: dict, *, dependencies: bool=True) -> None:
        pid=item["projectId"]; tid=item["id"]
        self._ensure_project_id(pid)
        desired=_TASK_STATE.get(str(item.get("status","todo")).lower(),"CREATED")
        row=self.store.conn.execute(
            "SELECT task_id,project_id,title,state,block_reason FROM tasks WHERE task_id=?",(tid,)
        ).fetchone()
        if row is None:
            self.tasks.create_in_connection(self.store.conn, Task(
                tid,pid,item.get("title") or tid,desired,
                str(item.get("failureReason") or "")[:2000] or None if desired=="BLOCKED" else None,
            ))
            self._append(pid,"TASK_CREATED",{"task_id":tid,"title":item.get("title") or tid},
                         actor_id=source,idempotency_key=f"compat:task:{tid}:created",task_id=tid)
        else:
            previous=row["state"]
            block_reason=(str(item.get("failureReason") or item.get("blockReason") or "")[:2000] or None) if desired=="BLOCKED" else None
            self.store.conn.execute(
                "UPDATE tasks SET title=?,state=?,block_reason=? WHERE task_id=?",
                (item.get("title") or tid,desired,block_reason,tid)
            )
            if previous != desired:
                evidence=[aid for criterion in item.get("acceptanceCriteria",[])
                          for aid in criterion.get("evidenceArtifactIds",[]) if aid]
                if desired=="COMPLETED" and not evidence:
                    evidence=[f"mrlc://projection/task/{tid}/accepted"]
                self._append(pid,"TASK_STATE_CHANGED",
                             {"task_id":tid,"from":previous,"to":desired,"evidence_refs":evidence},
                             actor_id=source,
                             idempotency_key=self._key(f"compat:task:{tid}:state",{"from":previous,"to":desired,"evidence":evidence}),
                             task_id=tid)
            else:
                self._append(pid,"TASK_PROJECTION_UPDATED",{"task_id":tid,"title":item.get("title") or tid},
                             actor_id=source,idempotency_key=self._key(f"compat:task:{tid}:updated",item),task_id=tid)
        if dependencies:
            self._sync_dependencies(item)

    def _sync_dependencies(self, item: dict) -> None:
        tid=item["id"]
        self.store.conn.execute("DELETE FROM task_dependencies WHERE task_id=?",(tid,))
        for dep in item.get("dependencyIds",[]):
            if self.store.conn.execute("SELECT 1 FROM tasks WHERE task_id=?",(dep,)).fetchone() is None:
                projected=self.store.obj("task",dep)
                if projected:
                    self._sync_task("migration",projected,dependencies=False)
            if self.store.conn.execute("SELECT 1 FROM tasks WHERE task_id=?",(dep,)).fetchone():
                self.tasks.add_dependency_in_connection(self.store.conn,tid,dep)
                self._append(item["projectId"],"TASK_DEPENDENCY_ADDED",
                             {"task_id":tid,"dependency_task_id":dep},
                             actor_id="compat-api",idempotency_key=f"compat:task:{tid}:dep:{dep}",task_id=tid)

    def _sync_agent(self, source: str, item: dict) -> None:
        sid=item.get("sessionId")
        if not sid:
            return
        pid=item["projectId"]
        self._ensure_project_id(pid)
        row=self.store.conn.execute("SELECT session_id FROM sessions WHERE session_id=?",(sid,)).fetchone()
        lifecycle="PARKED" if item.get("status") in {"parked","paused"} else "ACTIVE"
        if row is None:
            session=Session(sid,pid,item.get("id") or "mrlc-web",item.get("roleTemplateId") or "unknown",
                            lifecycle=lifecycle)
            self.sessions.create_in_connection(self.store.conn,session)
            self.store.conn.execute("UPDATE sessions SET lifecycle=? WHERE session_id=?",(lifecycle,sid))
            self._append(pid,"SESSION_CREATED",{"session_id":sid,"role_id":session.role_id},
                         actor_id=source,idempotency_key=f"compat:session:{sid}:created")
        else:
            self.store.conn.execute(
                "UPDATE sessions SET role_id=?,application_id=?,lifecycle=? WHERE session_id=?",
                (item.get("roleTemplateId") or "unknown",item.get("id") or "mrlc-web",lifecycle,sid)
            )
            self._append(pid,"SESSION_PROJECTION_UPDATED",{"session_id":sid,"lifecycle":lifecycle},
                         actor_id=source,idempotency_key=self._key(f"compat:session:{sid}:updated",item))

    def _ensure_project_id(self, project_id: str) -> None:
        if self.store.conn.execute("SELECT 1 FROM projects WHERE project_id=?",(project_id,)).fetchone():
            return
        projected=self.store.obj("project",project_id)
        if projected is None:
            raise KeyError(f"project projection missing: {project_id}")
        self._sync_project("migration",projected)

    def replay(self, project_id: str):
        return replay_project(self.events.read(project_id))

    def status(self) -> dict[str, Any]:
        counts={}
        for name in ("projects","tasks","sessions","events","memories","bank_issues",
                     "bank_decisions","consistency_aggregates","worker_runs"):
            try:
                counts[name]=int(self.store.conn.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0])
            except Exception:
                counts[name]=None
        return {
            "mode":"migrated-infra-core",
            "database":self.database_path,
            "projection":"objects",
            "uiEvents":"ui_events",
            "domainEvents":"events",
            "counts":counts,
        }
