"""Compatibility bridge from the Phase 8 FastAPI projection to the migrated Infra core.

The React/API contract remains unchanged.  Infra owns replayable domain state,
consistency primitives, memory/bank registries and scheduler state; the existing
`objects` table remains a compatibility projection for the current UI.
"""
from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime, timezone
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
                token_count=max(1,len(item.content.split())),
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
        return bundle

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
            "database":Path(self.database_path).name,
            "projection":"objects",
            "uiEvents":"ui_events",
            "domainEvents":"events",
            "counts":counts,
        }
