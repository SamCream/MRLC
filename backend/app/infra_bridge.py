"""Compatibility bridge from the Phase 8 FastAPI projection to the migrated Infra core.

The React/API contract remains unchanged.  Infra owns replayable domain state,
consistency primitives, memory/bank registries and scheduler state; the existing
`objects` table remains a compatibility projection for the current UI.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
from datetime import datetime, timezone
from uuid import uuid4
from pathlib import Path
from typing import Any

from infra.events import Event, EventStore
from infra.projects import Project, ProjectRegistry
from infra.tasks import Task, TaskRegistry
from infra.sessions import Session, SessionRegistry
from infra.memory import MemoryStore, MemoryItem, EvidenceItem
from infra.project_bank import Bank, Decision, Issue, Lesson, Progress
from infra.consistency import ConsistencyStore
from infra.scheduler import Scheduler, SessionMeta
from infra.history import HistoryStore
from infra.continuity import ContinuityStore
from infra.mcp import McpRegistry
from infra.skills import SkillRegistry
from infra.worker_runtime_state import WorkerRunStore
from infra.model_gateway import ModelGateway, ModelResult
from infra.replay import replay_project
from infra.context.bundle import ContextAssembler, ContextCandidate
from infra.retrieval.pipeline import RetrievalPipeline, RetrievalDocument
from infra.frontier import (
    FrontierAdvisorUnavailable, FrontierMode, FrontierPolicy, FrontierRequest, FrontierService,
)
from infra.delegation_store import DelegationStore
from infra.runtime import DelegationSpec, Profile, RoleDefinition, Runtime
from adapters.git import worktree as gitwt
from runtime.frontier_advisor import FrontierAdvisor
from runtime.frontier_delegate import delegate_permission_mode


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
        self.model_gateway = ModelGateway({}, database_path=database_path)
        self.context_assembler = ContextAssembler()
        self.retrieval = RetrievalPipeline()
        frontier_budget=max(1,int(os.getenv("MRLC_FRONTIER_BUDGET","1000") or 1000))
        self.frontier_advisor=(
            FrontierAdvisor(gateway=self.model_gateway) if os.getenv("CCR_API_KEY") else None
        )
        delegate_enabled=os.getenv("MRLC_FRONTIER_ALLOW_DELEGATION","false").lower()=="true"
        self.frontier=FrontierService(
            FrontierPolicy(allow_delegation=delegate_enabled,budget=frontier_budget,max_depth=1),
            database_path=database_path,
            advisor=self.frontier_advisor,
        )
        self.runtime=Runtime(
            Profile("frontier",{
                "owner":RoleDefinition("owner",frozenset(),frozenset({"delegate"})),
            }),
            database_path=database_path,
        )
        DelegationStore.initialize(self.store.conn)
        self.delegate_runner=None
        self.infra=self
        store.infra_bridge = self
        self.reconcile_projection()

    def connect(self):
        connection=sqlite3.connect(self.database_path,timeout=30.0)
        connection.row_factory=sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 30000")
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def event(
        self, project_id: str, event_type: str, payload: dict[str, Any], *,
        actor_id: str="system", actor_type: str="system", session_id: str|None=None,
        task_id: str|None=None, idempotency_key: str,
    ) -> Event:
        return Event(
            event_id="evt_"+hashlib.sha256(idempotency_key.encode()).hexdigest()[:32],
            project_id=project_id,event_type=event_type,payload=payload,
            occurred_at=datetime.now(timezone.utc),idempotency_key=idempotency_key,
            actor_type=actor_type,actor_id=actor_id,session_id=session_id,task_id=task_id,
            correlation_id=idempotency_key,
        )

    def attach_delegate_runner(self, runner) -> None:
        self.delegate_runner=runner

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
            for row in self.store.conn.execute("SELECT * FROM jobs WHERE status='queued' ORDER BY created_at").fetchall():
                agent=self.store.obj("agent",row["agent_id"]) if row["agent_id"] else None
                if agent:
                    self.enqueue_job(agent,row["project_id"],int(row["priority"]),job_id=row["id"])

    def sync_change(self, source: str, kind: str, body: dict, project_id=None, task_id=None) -> None:
        if kind == "project":
            self._sync_project(source, body)
        elif kind == "task":
            self._sync_task(source, body)
        elif kind == "agent":
            self._sync_agent(source, body)
        elif project_id and kind == "artifact":
            checksum=str(body.get("checksum") or "")
            self._append(project_id,"ARTIFACT_RECORDED",
                         {"artifact_id":body.get("id"),"content_hash":checksum,
                          "name":body.get("name"),"version":body.get("version")},
                         actor_id=source,
                         idempotency_key=self._key(f"artifact:{body.get('id')}:recorded",body),
                         task_id=task_id)
        elif project_id and kind == "evidenceLink":
            self._append(project_id,"EVIDENCE_LINKED",
                         {"evidence_link_id":body.get("id"),"artifact_id":body.get("artifactId"),
                          "kind":body.get("kind"),"reference_id":body.get("referenceId")},
                         actor_id=source,
                         idempotency_key=self._key(f"evidence:{body.get('id')}:linked",body),
                         task_id=task_id)
        elif project_id and kind == "message":
            channel_id=str(body.get("channelId") or "unknown")
            actor=str(body.get("senderName") or source or "unknown")
            content=str(body.get("content") or "")
            occurred=str(body.get("createdAt") or datetime.now(timezone.utc).isoformat())
            self.store.conn.execute(
                "INSERT INTO history(project_id,session_id,actor,content,occurred_at) "
                "SELECT ?,?,?,?,? WHERE NOT EXISTS ("
                "SELECT 1 FROM history WHERE project_id=? AND session_id=? AND actor=? "
                "AND content=? AND occurred_at=?)",
                (project_id,f"channel:{channel_id}",actor,content,occurred,
                 project_id,f"channel:{channel_id}",actor,content,occurred),
            )
            self._append(project_id,"MESSAGE_RECORDED",
                         {"message_id":body.get("id"),"channel_id":channel_id,
                          "actor":actor,"artifact_ids":body.get("artifactIds",[])},
                         actor_id=source,
                         idempotency_key=f"message:{body.get('id')}:recorded",
                         task_id=task_id)
        elif project_id and kind in {"approval", "role"}:
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
            if desired != "CREATED":
                evidence=[aid for criterion in item.get("acceptanceCriteria",[])
                          for aid in criterion.get("evidenceArtifactIds",[]) if aid]
                if desired=="COMPLETED" and not evidence:
                    evidence=[f"mrlc://projection/task/{tid}/accepted"]
                self._append(pid,"TASK_STATE_CHANGED",
                             {"task_id":tid,"from":"CREATED","to":desired,"evidence_refs":evidence},
                             actor_id=source,
                             idempotency_key=self._key(f"compat:task:{tid}:initial-state",{"to":desired,"evidence":evidence}),
                             task_id=tid)
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

    def reconcile_runtime_after_restart(self) -> None:
        """Clear stale active leases and reconstitute queued scheduler state after restart."""
        scheduler=Scheduler.from_connection(self.store.conn)
        for agent in self.store.all("agent"):
            sid=agent.get("sessionId")
            if not sid:
                continue
            try:
                meta=scheduler.get(sid)
            except KeyError:
                continue
            if meta.slot_state in {"ACTIVE","DRAINING"}:
                scheduler.finish(sid,task_state="FAILED")
        scheduler.save_in_connection(self.store.conn)
        for row in self.store.conn.execute("SELECT * FROM jobs WHERE status='queued' ORDER BY created_at").fetchall():
            agent=self.store.obj("agent",row["agent_id"]) if row["agent_id"] else None
            if agent:
                self.enqueue_job(agent,row["project_id"],int(row["priority"]),job_id=row["id"])

    def enqueue_job(self, agent: dict, project_id: str, priority: int, *, job_id: str) -> None:
        """Persist scheduler ownership in the same transaction as the compatibility job.

        The job id is the stable idempotency boundary, so startup reconciliation can
        safely enqueue the same queued job again without duplicating domain events.
        """
        sid=agent.get("sessionId")
        if not sid:
            raise ValueError("agent has no persistent session")
        scheduler=Scheduler.from_connection(self.store.conn)
        scheduler.enqueue(
            SessionMeta(
                sid,project_id,priority=max(-100,min(int(priority),100)),task_state="RUNNABLE"
            ),
            now=int(time.time()),
        )
        scheduler.save_in_connection(self.store.conn)
        self._append(project_id,"SESSION_QUEUED",
                     {"session_id":sid,"priority":int(priority),"job_id":job_id},
                     actor_id="scheduler",idempotency_key=f"compat:job:{job_id}:queued")

    def claim_next_job(self):
        """Select a queued execution with the migrated scheduler under the caller transaction."""
        scheduler=Scheduler.from_connection(self.store.conn)
        selected=scheduler.dispatch(now=int(time.time()))
        if selected is None:
            return None
        scheduler.save_in_connection(self.store.conn)
        agent=next((a for a in self.store.all("agent") if a.get("sessionId")==selected.session_id),None)
        if agent is None:
            scheduler.finish(selected.session_id,task_state="FAILED")
            scheduler.save_in_connection(self.store.conn)
            return None
        row=self.store.conn.execute(
            "SELECT * FROM jobs WHERE agent_id=? AND status='queued' ORDER BY priority DESC,created_at LIMIT 1",
            (agent["id"],)
        ).fetchone()
        if row is None:
            scheduler.finish(selected.session_id,task_state="FAILED")
            scheduler.save_in_connection(self.store.conn)
            return None
        self._append(row["project_id"],"LEASE_ACQUIRED",
                     {"resource_id":"slot:"+selected.session_id,"fencing_token":selected.fencing_token,
                      "session_id":selected.session_id,"job_id":row["id"]},
                     actor_id="scheduler",idempotency_key=f"compat:job:{row['id']}:lease:{selected.fencing_token}",
                     task_id=row["target_id"] if row["kind"]=="task" else None)
        return dict(row)

    def finish_job(self, job: dict, task_state: str) -> None:
        agent=self.store.obj("agent",job.get("agent_id")) if job.get("agent_id") else None
        sid=agent.get("sessionId") if agent else None
        if not sid:
            return
        scheduler=Scheduler.from_connection(self.store.conn)
        try:
            scheduler.finish(sid,task_state=task_state)
        except KeyError:
            return
        scheduler.save_in_connection(self.store.conn)
        self._append(job["project_id"],"LEASE_EXPIRED",
                     {"resource_id":"slot:"+sid,"session_id":sid,"job_id":job["id"]},
                     actor_id="scheduler",idempotency_key=f"compat:job:{job['id']}:lease-expired",
                     task_id=job["target_id"] if job["kind"]=="task" else None)

    def assemble_runtime_context(
        self, *, project_id: str, role_id: str, query: str, job_id: str,
        profile_id: str="runtime", token_budget: int=1200,
    ):
        """Build and persist the exact durable-memory context used for one provider call.

        Retrieval is deterministic and project/role scoped. When lexical retrieval finds
        no hit, only a small high-confidence fallback set is considered; all omissions
        remain visible in the persisted trace.
        """
        now_dt=datetime.now(timezone.utc)
        active=self.memory.active(project_id,role_id,now_dt)
        documents=[
            RetrievalDocument(
                document_id=item.memory_id,
                project_id=item.project_id,
                role_scope=item.role_scope,
                content=item.content,
                occurred_at=item.valid_from,
                confidence=item.confidence,
                source_refs=tuple(item.source_event_refs),
                status=item.status,
                valid_to=item.valid_to,
            )
            for item in active
        ]
        hits=self.retrieval.search(
            query,project_id,role_id,now_dt,documents,limit=min(24,len(documents))
        ) if query.strip() else []
        hit_ids=[hit.document.document_id for hit in hits]
        if hit_ids:
            selected_ids=set(hit_ids)
            rank_bonus={memory_id:max(1,200-index) for index,memory_id in enumerate(hit_ids)}
        else:
            fallback=sorted(active,key=lambda item:(-item.confidence,item.valid_from,item.memory_id))[:3]
            selected_ids={item.memory_id for item in fallback if item.confidence>=0.75}
            rank_bonus={}
        candidates=[]
        candidate_meta=[]
        retrieval_excluded={}
        for item in active:
            if item.memory_id not in selected_ids:
                retrieval_excluded[f"memory:{item.memory_id}"]="retrieval_miss"
                candidate_meta.append({
                    "sourceRef":f"memory:{item.memory_id}","memoryId":item.memory_id,
                    "confidence":item.confidence,"roleScope":item.role_scope,
                    "retrieval":"miss",
                })
                continue
            priority=round(item.confidence*100)+rank_bonus.get(item.memory_id,0)
            candidate=ContextCandidate(
                source_ref=f"memory:{item.memory_id}",
                project_id=item.project_id,
                content=item.content,
                token_count=max(1,len(item.content.split()),(len(item.content)+3)//4),
                priority=priority,
                occurred_at=item.valid_from,
                role_scopes=(item.role_scope,),
                sensitivity="project",
            )
            candidates.append(candidate)
            candidate_meta.append({
                "sourceRef":candidate.source_ref,"memoryId":item.memory_id,
                "confidence":item.confidence,"roleScope":item.role_scope,
                "retrieval":"hit" if item.memory_id in hit_ids else "fallback",
                "priority":priority,
            })
        bundle=self.context_assembler.build(
            project_id,profile_id,role_id,max(0,int(token_budget)),candidates
        )
        excluded={**retrieval_excluded,**bundle.manifest.excluded}
        trace_id="ctxrun_"+hashlib.sha256(
            f"{project_id}:{job_id}:{bundle.bundle_id}".encode()
        ).hexdigest()[:24]
        self.store.conn.execute(
            "INSERT OR REPLACE INTO context_assembly_runs("
            "id,project_id,job_id,role_id,profile_id,query_excerpt,token_budget,total_tokens,"
            "included_json,excluded_json,candidates_json,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                trace_id,project_id,job_id,role_id,profile_id,str(query)[:2000],
                int(token_budget),bundle.total_tokens,json.dumps(bundle.manifest.included),
                json.dumps(excluded,sort_keys=True),json.dumps(candidate_meta,sort_keys=True),
                now_dt.isoformat(),
            ),
        )
        self._append(
            project_id,"CONTEXT_ASSEMBLED",
            {"context_run_id":trace_id,"job_id":job_id,"role_id":role_id,
             "bundle_id":bundle.bundle_id,"included":list(bundle.manifest.included),
             "excluded":excluded,"token_budget":int(token_budget),
             "total_tokens":bundle.total_tokens},
            actor_id="context-assembler",
            idempotency_key=f"context:{job_id}:{bundle.bundle_id}",
        )
        return bundle,trace_id

    def record_provider_result(self, job: dict, result: dict, *, status: str="COMPLETED") -> None:
        model=str(result.get("model") or "unknown")
        provider_request_id=result.get("providerRequestId")
        self.model_gateway.record(ModelResult(
            job["project_id"],model,job["kind"],job["id"],None,status,
            None if provider_request_id is None else str(provider_request_id),
            int(result.get("inputTokens") or 0),int(result.get("outputTokens") or 0),
        ),idempotency_key="job:"+job["id"],connection=self.store.conn)
        self._append(job["project_id"],"MODEL_INVOCATION_"+status,
                     {"job_id":job["id"],"model":model,"provider_request_id":provider_request_id,
                      "input_tokens":int(result.get("inputTokens") or 0),
                      "output_tokens":int(result.get("outputTokens") or 0)},
                     actor_id="model-gateway",idempotency_key=f"compat:job:{job['id']}:provider:{status}",
                     task_id=job["target_id"] if job["kind"]=="task" else None)

    def record_memory(
        self, item: MemoryItem, evidence: tuple[EvidenceItem, ...], *, actor_id: str
    ) -> MemoryItem:
        """Store evidence-backed Memory and its event atomically on the shared DB."""
        for event_id in item.source_event_refs:
            if self.store.conn.execute(
                "SELECT 1 FROM events WHERE project_id=? AND event_id=?",
                (item.project_id,event_id),
            ).fetchone() is None:
                raise ValueError(f"unknown source event for project: {event_id}")
        for entry in evidence:
            if self.store.conn.execute(
                "SELECT 1 FROM events WHERE project_id=? AND event_id=?",
                (item.project_id,entry.event_id),
            ).fetchone() is None:
                raise ValueError(f"unknown evidence event for project: {entry.event_id}")
        self.memory.put_in_connection(self.store.conn,item,evidence)
        self._append(
            item.project_id,"MEMORY_RECORDED",
            {"memory_id":item.memory_id,"status":item.status,"kind":item.kind,
             "role_scope":item.role_scope,"confidence":item.confidence},
            actor_id=actor_id,idempotency_key=f"memory:{item.memory_id}:recorded",
        )
        return item

    def supersede_memory(
        self, project_id: str, old_memory_id: str, new_memory_id: str, *, actor_id: str
    ) -> None:
        self.memory.supersede_in_connection(
            self.store.conn,project_id,old_memory_id,new_memory_id
        )
        self._append(
            project_id,"MEMORY_SUPERSEDED",
            {"memory_id":old_memory_id,"replacement_memory_id":new_memory_id},
            actor_id=actor_id,
            idempotency_key=f"memory:{old_memory_id}:superseded:{new_memory_id}",
        )

    def create_issue(self, issue: Issue, *, actor_id: str) -> Issue:
        created=self.bank.create_issue_in_connection(self.store.conn,issue)
        self._append(
            issue.project_id,"ISSUE_CREATED",
            {"issue_id":issue.issue_id,"title":issue.title,"blocking":issue.blocking},
            actor_id=actor_id,idempotency_key=f"issue:{issue.issue_id}:created",
        )
        return created

    def record_decision(self, decision: Decision, *, actor_id: str) -> Decision:
        recorded=self.bank.record_decision_in_connection(self.store.conn,decision)
        self._append(
            decision.project_id,"DECISION_RECORDED",
            {"decision_id":decision.decision_id,"title":decision.title,
             "supersedes":decision.supersedes},
            actor_id=actor_id,idempotency_key=f"decision:{decision.decision_id}:recorded",
        )
        return recorded

    def resolve_issue(
        self, project_id: str, issue_id: str, decision_id: str, *, actor_id: str
    ) -> Issue:
        decision=self.bank.get_decision(project_id,decision_id)
        if decision.project_id != project_id:
            raise PermissionError("decision access crosses project boundary")
        resolved=self.bank.resolve_issue_in_connection(self.store.conn,issue_id,decision_id)
        if resolved.project_id != project_id:
            raise PermissionError("issue access crosses project boundary")
        self._append(
            project_id,"ISSUE_RESOLVED",
            {"issue_id":issue_id,"decision_id":decision_id},
            actor_id=actor_id,idempotency_key=f"issue:{issue_id}:resolved:{decision_id}",
        )
        return resolved

    def record_lesson(self, lesson: Lesson, *, actor_id: str) -> Lesson:
        recorded=self.bank.record_lesson_in_connection(self.store.conn,lesson)
        self._append(
            lesson.project_id,"LESSON_RECORDED",
            {"lesson_id":lesson.lesson_id,"title":lesson.title},
            actor_id=actor_id,idempotency_key=f"lesson:{lesson.lesson_id}:recorded",
        )
        return recorded

    def record_progress(self, progress: Progress, *, actor_id: str) -> Progress:
        recorded=self.bank.record_progress_in_connection(self.store.conn,progress)
        self._append(
            progress.project_id,"PROGRESS_RECORDED",
            {"progress_id":progress.progress_id,"milestone":progress.milestone,
             "status":progress.status},
            actor_id=actor_id,idempotency_key=f"progress:{progress.progress_id}:recorded",
        )
        return recorded

    def record_approval_decision(self, approval: dict, record: dict) -> None:
        """Promote a resolved FastAPI approval decision into the authoritative Project Bank."""
        decision=Decision(
            decision_id=record["id"],
            project_id=approval["projectId"],
            title=approval.get("title") or record["id"],
            authority=str(record.get("actor") or "owner"),
            risk=str(approval.get("risk") or "unknown"),
            rationale=str(record.get("reason") or record.get("decision") or ""),
        )
        self.bank.record_decision_in_connection(self.store.conn,decision)
        self._append(
            approval["projectId"],"DECISION_RECORDED",
            {"decision_id":decision.decision_id,"title":decision.title,"supersedes":None,
             "approval_id":approval["id"],"resolution":record.get("decision")},
            actor_id=decision.authority,
            idempotency_key=f"decision:{decision.decision_id}:recorded",
        )

    def frontier_snapshot(self, project_id: str) -> dict[str, Any]:
        requests=[
            {
                "requestId":row["request_id"],"mode":row["mode"],
                "ownerSessionId":row["owner_session_id"],
                "taskOwnerSessionId":row["task_owner_session_id"],
                "childSessionId":row["child_session_id"],
                "advice":row["advice"],"returnContract":row["return_contract"],
                "reviewState":row["review_state"],"budgetUsed":int(row["budget_used"]),
            }
            for row in self.store.conn.execute(
                "SELECT * FROM frontier_requests WHERE project_id=? ORDER BY rowid DESC LIMIT 100",
                (project_id,),
            ).fetchall()
        ]
        sessions=[
            {"sessionId":row["session_id"],"roleId":row["role_id"],"lifecycle":row["lifecycle"]}
            for row in self.store.conn.execute(
                "SELECT session_id,role_id,lifecycle FROM sessions "
                "WHERE project_id=? ORDER BY rowid",(project_id,)
            ).fetchall()
        ]
        tasks=[
            {"taskId":row["task_id"],"state":row["state"]}
            for row in self.store.conn.execute(
                "SELECT task_id,state FROM tasks WHERE project_id=? ORDER BY rowid",(project_id,)
            ).fetchall()
        ]
        return {
            "capabilities":{
                "consult":{
                    "available":self.frontier_advisor is not None,
                    "budgetLimit":self.frontier.policy.budget,
                    "reason":None if self.frontier_advisor is not None
                             else "CCR_API_KEY is not configured; no Frontier call will be made.",
                },
                "delegate":{
                    "available":bool(self.frontier.policy.allow_delegation and self.delegate_runner is not None),
                    "reason":(
                        None if self.frontier.policy.allow_delegation and self.delegate_runner is not None
                        else "MRLC_FRONTIER_ALLOW_DELEGATION is disabled."
                        if not self.frontier.policy.allow_delegation
                        else "Delegation worker failed to initialize."
                    ),
                    "worker":getattr(self.delegate_runner,"worker_kind",None),
                    "permissionMode":(
                        delegate_permission_mode() if self.delegate_runner is not None else None
                    ),
                },
            },
            "sessions":sessions,"tasks":tasks,"requests":requests,
            "delegateRuns":[
                {
                    "requestId":row["request_id"],"relationId":row["relation_id"],
                    "parentTaskId":row["parent_task_id"],"ownerSessionId":row["owner_session_id"],
                    "childSessionId":row["child_session_id"],"childTaskId":row["child_task_id"],
                    "capability":row["capability"],"state":row["state"],
                    "branch":row["branch"],"commitSha":row["commit_sha"],"mergeSha":row["merge_sha"],
                    "error":row["error"],"createdAt":float(row["created_at"]),
                }
                for row in self.store.conn.execute(
                    "SELECT * FROM frontier_delegate_runs WHERE project_id=? "
                    "ORDER BY created_at DESC LIMIT 100",(project_id,)
                ).fetchall()
            ],
        }

    def frontier_delegate(
        self, *, project_id: str, parent_task_id: str, owner_session_id: str,
        capability: str, objective: str, acceptance_commands: list[str], budget: int,
        actor_id: str, operation_id: str, confirmation: str,
    ) -> dict[str, Any]:
        if not self.frontier.policy.allow_delegation or self.delegate_runner is None:
            raise RuntimeError("frontier DELEGATE is not available in this runtime")
        if confirmation!="RUN_DELEGATE":
            raise ValueError("confirmation must equal RUN_DELEGATE")
        if not capability.strip() or not objective.strip():
            raise ValueError("capability and objective must not be empty")
        if len(capability)>500 or len(objective)>8000:
            raise ValueError("capability/objective is too long")
        if type(budget) is not int or budget<1 or budget>self.frontier.policy.budget:
            raise ValueError(
                f"budget must be an integer between 1 and {self.frontier.policy.budget}"
            )
        if not isinstance(acceptance_commands,list) or not acceptance_commands:
            raise ValueError("acceptanceCommands must contain at least one command")
        commands=[]
        for command in acceptance_commands:
            if not isinstance(command,str) or not command.strip() or len(command)>1000:
                raise ValueError("acceptance commands must be non-empty strings up to 1000 chars")
            commands.append(command.strip())
        if len(commands)>20:
            raise ValueError("at most 20 acceptance commands are allowed")
        if not operation_id.strip() or len(operation_id)>128:
            raise ValueError("operationId must contain 1-128 characters")

        task=self.store.conn.execute(
            "SELECT project_id FROM tasks WHERE task_id=?",(parent_task_id,)
        ).fetchone()
        if task is None or task["project_id"]!=project_id:
            raise ValueError("parent task must belong to this project")
        session=self.store.conn.execute(
            "SELECT project_id,lifecycle FROM sessions WHERE session_id=?",(owner_session_id,)
        ).fetchone()
        if session is None or session["project_id"]!=project_id:
            raise ValueError("owner session must belong to this project")
        if session["lifecycle"]!="ACTIVE":
            raise ValueError("owner session must be ACTIVE")

        existing=self.store.conn.execute(
            "SELECT status,request_id,error FROM frontier_delegate_operations "
            "WHERE actor_id=? AND operation_id=?",(actor_id,operation_id)
        ).fetchone()
        if existing is not None:
            if existing["status"]=="COMPLETED" and existing["request_id"]:
                run=DelegationStore.current(self.store.conn,existing["request_id"])
                if run is None:
                    raise RuntimeError("completed delegation receipt references missing run")
                return self._delegate_run_payload(run,operation_id=operation_id,replayed=True)
            raise RuntimeError(
                "operationId already used for a non-completed Frontier delegation: "
                +(existing["error"] or existing["status"])
            )

        child_session=Session(
            f"ses_{uuid4().hex}",project_id,"frontier","frontier.child",
            runtime_adapter=getattr(self.delegate_runner,"worker_kind","unknown"),
            model_binding=getattr(self.delegate_runner,"model_binding","router-managed"),
        )
        child_task=Task(
            f"tsk_{uuid4().hex}",project_id,
            "Frontier: "+capability.strip()[:120],"CREATED",
        )
        relation_id=f"rel_{uuid4().hex}"
        created=datetime.now(timezone.utc).isoformat()
        permission_mode=delegate_permission_mode()
        with self.store.tx():
            self.store.conn.execute(
                "INSERT INTO frontier_delegate_operations("
                "actor_id,operation_id,project_id,status,created_at) VALUES(?,?,?,?,?)",
                (actor_id,operation_id,project_id,"RUNNING",created)
            )
            self.sessions.create_in_connection(self.store.conn,child_session)
            self.tasks.create_in_connection(self.store.conn,child_task)
            self.tasks.set_parent_in_connection(self.store.conn,child_task.task_id,parent_task_id)
            self.tasks.transition_in_connection(self.store.conn,child_task.task_id,"CREATED","RUNNABLE")
            result=self.frontier.execute(
                FrontierRequest(
                    project_id,parent_task_id,owner_session_id,capability.strip(),
                    FrontierMode.DELEGATE,budget,0,child_session.session_id,
                ),
                connection=self.store.conn,
            )
            Runtime.delegate_in_connection(
                self.store.conn,
                DelegationSpec(
                    project_id,parent_task_id,owner_session_id,child_session.session_id,
                    "frontier.delegate",result.return_contract or "artifact,tests,risks",
                ),
                relation_id,
            )
            DelegationStore.add(
                self.store.conn,request_id=result.request_id,project_id=project_id,
                actor_id=actor_id,relation_id=relation_id,parent_task_id=parent_task_id,
                owner_session_id=owner_session_id,child_session_id=child_session.session_id,
                child_task_id=child_task.task_id,capability=capability.strip(),
                objective=objective.strip(),acceptance_commands=commands,budget=budget,
                permission_mode=permission_mode,
                disclosure={"operation_id":operation_id,"actor_id":actor_id},
            )
            DelegationStore.set_request_state(self.store.conn,result.request_id,"CHILD_QUEUED")
            self._append(
                project_id,"SESSION_CREATED",
                {"session_id":child_session.session_id,"application_id":"frontier",
                 "role_id":"frontier.child"},
                actor_id=actor_id,
                idempotency_key=f"frontier-delegate:{operation_id}:session",
            )
            self._append(
                project_id,"TASK_CREATED",
                {"task_id":child_task.task_id,"title":child_task.title,"state":"RUNNABLE",
                 "parent_task_id":parent_task_id},
                actor_id=actor_id,
                idempotency_key=f"frontier-delegate:{operation_id}:task",
                task_id=child_task.task_id,
            )
            self._append(
                project_id,"TASK_STATE_CHANGED",
                {"from":"CREATED","to":"RUNNABLE","evidence_refs":[]},
                actor_id=actor_id,
                idempotency_key=f"frontier-delegate:{operation_id}:task-runnable",
                task_id=child_task.task_id,
            )
            self._append(
                project_id,"DELEGATION_CREATED",
                {"relation_id":relation_id,"parent_task_id":parent_task_id,
                 "child_session_id":child_session.session_id,
                 "return_contract":result.return_contract or "artifact,tests,risks"},
                actor_id=actor_id,
                idempotency_key=f"frontier-delegate:{operation_id}:relation",
                task_id=parent_task_id,
            )
            self._append(
                project_id,"FRONTIER_DELEGATE_QUEUED",
                {"request_id":result.request_id,"relation_id":relation_id,
                 "parent_task_id":parent_task_id,"child_task_id":child_task.task_id,
                 "child_session_id":child_session.session_id,"budget":budget,
                 "permission_mode":permission_mode},
                actor_id=actor_id,
                idempotency_key=f"frontier-delegate:{operation_id}:queued",
                task_id=parent_task_id,
            )
            self.store.conn.execute(
                "UPDATE frontier_delegate_operations SET status='COMPLETED',request_id=?,completed_at=? "
                "WHERE actor_id=? AND operation_id=?",
                (result.request_id,datetime.now(timezone.utc).isoformat(),actor_id,operation_id)
            )
            self.store.emit(
                "infra.frontier.delegate.queued",project_id,
                {"requestId":result.request_id,"operationId":operation_id},
            )
            run=DelegationStore.current(self.store.conn,result.request_id)
        assert run is not None
        return self._delegate_run_payload(run,operation_id=operation_id,replayed=False)

    def frontier_cancel_delegate(
        self, *, project_id: str, request_id: str, actor_id: str, rationale: str,
    ) -> dict[str, Any]:
        if self.delegate_runner is None:
            raise RuntimeError("delegation runner is unavailable")
        if not rationale.strip():
            raise ValueError("rationale must not be empty")
        with self.store.tx():
            run=DelegationStore.current(self.store.conn,request_id)
            if run is None or run["project_id"]!=project_id:
                raise KeyError("unknown delegate run in this project")
            if run["state"]=="QUEUED":
                self.store.conn.execute(
                    "UPDATE frontier_delegate_runs SET state='RUNNING' WHERE request_id=?",(request_id,)
                )
                DelegationStore.finish(
                    self.store.conn,request_id,"CANCELLED",error="operator cancelled before start"
                )
                DelegationStore.set_request_state(self.store.conn,request_id,"CANCELLED")
                Runtime.finish_in_connection(
                    self.store.conn,run["relation_id"],"FAILED",
                    json.dumps({"error":"operator cancelled before start"},sort_keys=True),
                )
                self.tasks.transition_in_connection(
                    self.store.conn,run["child_task_id"],"RUNNABLE","FAILED"
                )
                self._append(
                    project_id,"TASK_STATE_CHANGED",
                    {"from":"RUNNABLE","to":"FAILED","evidence_refs":[]},
                    actor_id=actor_id,
                    idempotency_key=f"frontier-cancel:{request_id}:task-failed",
                    task_id=run["child_task_id"],
                )
                state="CANCELLED";signal=None
            elif run["state"]=="RUNNING" and DelegationStore.mark_cancel_requested(
                self.store.conn,request_id
            ):
                signal=None
                if run["worker_handle"]:
                    try:
                        signal=self.delegate_runner.adapter.cancel(
                            run["worker_handle"],reason=rationale.strip(),fencing_token=1
                        ).status
                    except Exception:
                        signal="UNKNOWN"
                state="CANCEL_REQUESTED"
            else:
                raise ValueError("child is not queued/running; nothing to cancel")
            self._append(
                project_id,"FRONTIER_DELEGATE_CANCEL_REQUESTED",
                {"request_id":request_id,"state":state,"worker_signal":signal,
                 "rationale":rationale.strip()[:1000]},
                actor_id=actor_id,
                idempotency_key=self._key(f"frontier-cancel:{request_id}",{"state":state,"rationale":rationale.strip()}),
                task_id=run["parent_task_id"],
            )
            self.store.emit("infra.frontier.delegate.cancelled",project_id,{"requestId":request_id,"state":state})
        return {"requestId":request_id,"state":state,"workerSignal":signal}

    def frontier_review_delegate(
        self, *, project_id: str, request_id: str, owner_session_id: str,
        decision: str, actor_id: str, rationale: str,
    ) -> dict[str, Any]:
        if self.delegate_runner is None:
            raise RuntimeError("delegation runner is unavailable")
        if decision not in {"ACCEPT","REWORK"}:
            raise ValueError("decision must be ACCEPT or REWORK")
        if not rationale.strip():
            raise ValueError("rationale must not be empty")
        run=DelegationStore.current(self.store.conn,request_id)
        if run is None or run["project_id"]!=project_id:
            raise KeyError("unknown delegate run in this project")
        if run["owner_session_id"]!=owner_session_id:
            raise PermissionError("reviewer session is not the delegation owner")
        if run["state"]!="WAIT_OWNER_REVIEW":
            raise ValueError("frontier child has not returned for review")
        merge_sha=None
        if decision=="ACCEPT":
            if not run["branch"]:
                raise RuntimeError("delegation branch binding is unavailable")
            merge_sha=gitwt.merge_reviewed(
                self.delegate_runner.repository(project_id),run["branch"],"main",
                "merge: reviewed frontier "+request_id[-12:],
            )
        with self.store.tx():
            relation=Runtime.review_in_connection(
                self.store.conn,project_id,run["relation_id"],owner_session_id,decision
            )
            state="ACCEPTED" if relation.state=="ACCEPTED" else "REWORK"
            DelegationStore.review(self.store.conn,request_id,state,merge_sha)
            self._append(
                project_id,"REVIEW_ACCEPTED" if state=="ACCEPTED" else "REVIEW_REWORK",
                {"relation_id":run["relation_id"],"request_id":request_id,
                 "merge_sha":merge_sha},
                actor_id=actor_id,
                idempotency_key=f"frontier-review:{request_id}:{state}:replay",
                task_id=run["parent_task_id"],
            )
            self._append(
                project_id,"DELEGATION_REVIEWED",
                {"request_id":request_id,"relation_id":run["relation_id"],
                 "decision":decision,"state":state,"merge_sha":merge_sha,
                 "rationale":rationale.strip()[:1000]},
                actor_id=actor_id,
                idempotency_key=self._key(f"frontier-review:{request_id}",{"decision":decision,"rationale":rationale.strip()}),
                task_id=run["parent_task_id"],
            )
            self.store.emit("infra.frontier.delegate.reviewed",project_id,{"requestId":request_id,"state":state})
        return {"requestId":request_id,"state":state,"mergeSha":merge_sha}

    @staticmethod
    def _delegate_run_payload(run, *, operation_id: str|None=None, replayed: bool=False) -> dict[str, Any]:
        return {
            "requestId":run["request_id"],"relationId":run["relation_id"],
            "projectId":run["project_id"],"parentTaskId":run["parent_task_id"],
            "ownerSessionId":run["owner_session_id"],"childSessionId":run["child_session_id"],
            "childTaskId":run["child_task_id"],"capability":run["capability"],
            "state":run["state"],"branch":run["branch"],"commitSha":run["commit_sha"],
            "mergeSha":run["merge_sha"],"error":run["error"],
            "operationId":operation_id,"replayed":replayed,
        }

    def frontier_consult(
        self, *, project_id: str, parent_task_id: str, owner_session_id: str,
        capability: str, budget: int, actor_id: str, operation_id: str,
    ) -> dict[str, Any]:
        if self.frontier_advisor is None:
            raise FrontierAdvisorUnavailable(
                "frontier CONSULT unavailable: configure CCR_API_KEY and MRLC_CCR_BASE_URL"
            )
        if not capability.strip():
            raise ValueError("capability must not be empty")
        if not operation_id.strip() or len(operation_id)>128:
            raise ValueError("operationId must contain 1-128 characters")
        if type(budget) is not int or budget<1 or budget>self.frontier.policy.budget:
            raise ValueError(
                f"budget must be an integer between 1 and {self.frontier.policy.budget}"
            )
        task=self.store.conn.execute(
            "SELECT project_id FROM tasks WHERE task_id=?",(parent_task_id,)
        ).fetchone()
        if task is None or task["project_id"]!=project_id:
            raise ValueError("parent task must belong to this project")
        session=self.store.conn.execute(
            "SELECT project_id,lifecycle FROM sessions WHERE session_id=?",(owner_session_id,)
        ).fetchone()
        if session is None or session["project_id"]!=project_id:
            raise ValueError("owner session must belong to this project")
        if session["lifecycle"]!="ACTIVE":
            raise ValueError("owner session must be ACTIVE")

        existing=self.store.conn.execute(
            "SELECT status,request_id,error FROM frontier_consult_operations "
            "WHERE actor_id=? AND operation_id=?",(actor_id,operation_id)
        ).fetchone()
        if existing is not None:
            if existing["status"]=="COMPLETED" and existing["request_id"]:
                row=self.store.conn.execute(
                    "SELECT * FROM frontier_requests WHERE request_id=?",(existing["request_id"],)
                ).fetchone()
                if row is None:
                    raise RuntimeError("completed Frontier receipt references missing request")
                return {
                    "requestId":row["request_id"],"projectId":project_id,"mode":row["mode"],
                    "ownerSessionId":row["owner_session_id"],"advice":row["advice"],
                    "reviewState":row["review_state"],"budgetUsed":int(row["budget_used"]),
                    "operationId":operation_id,"replayed":True,
                }
            raise RuntimeError(
                "operationId already used for a non-completed Frontier consultation: "
                +(existing["error"] or existing["status"])
            )

        created=datetime.now(timezone.utc).isoformat()
        with self.store.tx():
            self.store.conn.execute(
                "INSERT INTO frontier_consult_operations("
                "actor_id,operation_id,project_id,status,created_at) VALUES(?,?,?,?,?)",
                (actor_id,operation_id,project_id,"RUNNING",created)
            )
            self._append(
                project_id,"FRONTIER_CONSULT_REQUESTED",
                {"operation_id":operation_id,"parent_task_id":parent_task_id,
                 "owner_session_id":owner_session_id,"capability":capability,"budget":budget},
                actor_id=actor_id,idempotency_key=f"frontier-consult:{actor_id}:{operation_id}:requested",
                task_id=parent_task_id,
            )
            self.store.emit("infra.frontier.consult.requested",project_id,{"operationId":operation_id})

        request=FrontierRequest(
            project_id,parent_task_id,owner_session_id,capability.strip(),
            FrontierMode.CONSULT,budget,
        )
        try:
            # Network execution is intentionally outside the SQLite write transaction.
            advice=self.frontier_advisor.consult(request)
        except Exception as exc:
            completed=datetime.now(timezone.utc).isoformat()
            with self.store.tx():
                self.store.conn.execute(
                    "UPDATE frontier_consult_operations SET status='FAILED',error=?,completed_at=? "
                    "WHERE actor_id=? AND operation_id=?",
                    (type(exc).__name__+": "+str(exc)[:500],completed,actor_id,operation_id)
                )
                self._append(
                    project_id,"FRONTIER_CONSULT_FAILED",
                    {"operation_id":operation_id,"error":type(exc).__name__},
                    actor_id=actor_id,
                    idempotency_key=f"frontier-consult:{actor_id}:{operation_id}:failed",
                    task_id=parent_task_id,
                )
                self.store.emit("infra.frontier.consult.failed",project_id,{"operationId":operation_id})
            raise

        with self.store.tx():
            # Persist FrontierResult without a second provider call.
            result=self.frontier.execute(
                request,connection=self.store.conn,advisor=lambda _:advice
            )
            completed=datetime.now(timezone.utc).isoformat()
            self.store.conn.execute(
                "UPDATE frontier_consult_operations SET status='COMPLETED',request_id=?,completed_at=? "
                "WHERE actor_id=? AND operation_id=?",
                (result.request_id,completed,actor_id,operation_id)
            )
            self._append(
                project_id,"FRONTIER_CONSULT_RETURNED",
                {"operation_id":operation_id,"request_id":result.request_id,
                 "parent_task_id":parent_task_id,"owner_session_id":owner_session_id,
                 "capability":capability,"budget_used":result.budget_used},
                actor_id=actor_id,
                idempotency_key=f"frontier-consult:{actor_id}:{operation_id}:returned",
                task_id=parent_task_id,
            )
            self.store.emit(
                "infra.frontier.consult.completed",project_id,
                {"operationId":operation_id,"requestId":result.request_id},
            )
        return {
            "requestId":result.request_id,"projectId":result.project_id,
            "mode":result.mode.value,"ownerSessionId":result.owner_session_id,
            "advice":result.advice,"reviewState":result.review_state,
            "budgetUsed":result.budget_used,"operationId":operation_id,"replayed":False,
        }

    def scheduler_snapshot(self, project_id: str) -> dict[str, Any]:
        """Project-scoped scheduler/lease view for the Infra Console.

        This intentionally exposes arbitration metadata rather than duplicating the
        product Compute page's slot/resource presentation.
        """
        row=self.store.conn.execute("SELECT state_json FROM scheduler_state WHERE id=1").fetchone()
        state=json.loads(row["state_json"]) if row else {"capacity":self.scheduler.capacity,"sessions":{},"queue":[],"priority_adjustments":[]}
        sessions=[]
        scoped_ids=set()
        now_ts=int(time.time())
        for session_id,value in state.get("sessions",{}).items():
            if value.get("project_id")!=project_id:
                continue
            scoped_ids.add(session_id)
            queued_at=int(value.get("queued_at") or 0)
            lease_started=value.get("lease_started")
            sessions.append({
                "sessionId":session_id,
                "priority":int(value.get("priority") or 0),
                "taskState":value.get("task_state"),
                "slotState":value.get("slot_state"),
                "yieldability":value.get("yieldability"),
                "fencingToken":int(value.get("fencing_token") or 0),
                "queuedAt":queued_at or None,
                "waitSeconds":max(0,now_ts-queued_at) if queued_at else None,
                "leaseStarted":lease_started,
                "leaseAgeSeconds":max(0,now_ts-int(lease_started)) if lease_started else None,
                "parkRequested":bool(value.get("park_requested")),
                "parkCount":int(value.get("park_count") or 0),
                "cooldownUntil":int(value.get("cooldown_until") or 0) or None,
                "familyParent":value.get("family_parent"),
            })
        queue=[sid for sid in state.get("queue",[]) if sid in scoped_ids]
        adjustments=[
            item for item in state.get("priority_adjustments",[])
            if item.get("session_id") in scoped_ids
        ]
        active=sum(item["slotState"] in {"ACTIVE","DRAINING"} for item in sessions)
        parked=sum(item["slotState"]=="PARKED" for item in sessions)
        queued=sum(item["slotState"] in {"QUEUED","RESERVED_FAMILY"} for item in sessions)
        return {
            "capacity":int(state.get("capacity") or self.scheduler.capacity),
            "active":active,"queued":queued,"parked":parked,
            "sessions":sessions,"queue":queue,"priorityAdjustments":adjustments,
        }

    def replay(self, project_id: str, sequence: int | None = None):
        events=self.events.read(project_id)
        if sequence is not None:
            events=[event for event in events if event.sequence<=int(sequence)]
        return replay_project(events)

    def replay_compare(self, project_id: str, from_sequence: int, to_sequence: int) -> dict[str, Any]:
        if from_sequence<0 or to_sequence<0:
            raise ValueError("replay sequence must be non-negative")
        left=self.replay(project_id,from_sequence)
        right=self.replay(project_id,to_sequence)
        fields=("projects","sessions","tasks","delegations","leases","issues","decisions","artifacts","memories")
        changes={}
        for field in fields:
            before=getattr(left,field)
            after=getattr(right,field)
            keys=sorted(set(before)|set(after))
            delta=[]
            for key in keys:
                old=before.get(key)
                new=after.get(key)
                if old!=new:
                    delta.append({"id":key,"from":old,"to":new})
            if delta:
                changes[field]=delta
        if left.dependencies!=right.dependencies:
            changes["dependencies"]={
                "from":[list(item) for item in left.dependencies],
                "to":[list(item) for item in right.dependencies],
            }
        return {
            "fromSequence":from_sequence,
            "toSequence":to_sequence,
            "changes":changes,
            "changedSections":len(changes),
        }

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
            "database":Path(self.database_path).name,
            "projection":"objects",
            "uiEvents":"ui_events",
            "domainEvents":"events",
            "counts":counts,
        }
