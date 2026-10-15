from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any, Callable

from .events import Event, EventStore
from .a2cm_plugin import A2CMPlugin, BeliefCandidate, Observation
from .dev_plugin import DevEvent, DevPlugin, HarnessEvent
from .frontier import FrontierRequest, FrontierResult, FrontierService
from .identity import new_id
from .project_bank import Bank, Decision, Issue, Lesson, Progress
from .projects import Project, ProjectRegistry
from .runtime import Delegation, DelegationSpec, Runtime
from .scheduler import ParkDecision, Scheduler, SessionMeta
from .workers import WorkerEvent, is_workflow_event
from .sessions import Session, SessionRegistry, SessionSpec
from .tasks import Task, TaskRegistry, TaskSpec


class EventedInfra:
    def __init__(self, database_path: str | Path) -> None:
        self._database_path = str(database_path)
        self.events = EventStore(database_path)
        self.projects = ProjectRegistry(database_path)
        self.sessions = SessionRegistry(database_path)
        self.tasks = TaskRegistry(database_path)
        self.bank = Bank(database_path)

    def event(
        self,
        project_id: str,
        event_type: str,
        payload: dict[str, Any],
        *,
        actor_id: str,
        idempotency_key: str,
        actor_type: str = "role",
        session_id: str | None = None,
        task_id: str | None = None,
        causation_id: str | None = None,
    ) -> Event:
        return Event(
            event_id=f"evt_{sha256(idempotency_key.encode()).hexdigest()[:32]}",
            project_id=project_id,
            event_type=event_type,
            payload=payload,
            occurred_at=datetime.now(timezone.utc),
            idempotency_key=idempotency_key,
            actor_type=actor_type,
            actor_id=actor_id,
            session_id=session_id,
            task_id=task_id,
            correlation_id=idempotency_key,
            causation_id=causation_id,
        )

    def create_project(self, name: str, *, actor_id: str, idempotency_key: str | None = None) -> Project:
        if not name.strip():
            raise ValueError("project name must not be empty")
        project = Project(new_id("project"), name)
        with sqlite3.connect(self._database_path) as connection:
            connection.row_factory = sqlite3.Row
            self.projects.create_in_connection(connection, project)
            self.events.append_in_connection(
                connection,
                self.event(
                    project.project_id,
                    "PROJECT_CREATED",
                    {"project_id": project.project_id, "name": project.name},
                    actor_id=actor_id,
                    idempotency_key=idempotency_key or f"project:{project.project_id}:created",
                ),
            )
        return project

    def create_session(self, spec: SessionSpec, *, actor_id: str) -> Session:
        session = Session(new_id("session"), spec.project_id, spec.application_id, spec.role_id)
        with sqlite3.connect(self._database_path) as connection:
            connection.row_factory = sqlite3.Row
            self.sessions.create_in_connection(connection, session)
            self.events.append_in_connection(
                connection,
                self.event(
                    session.project_id,
                    "SESSION_CREATED",
                    {"session_id": session.session_id, "role_id": session.role_id},
                    actor_id=actor_id,
                    session_id=session.session_id,
                    idempotency_key=f"session:{session.session_id}:created",
                ),
            )
        return session

    def bind_session(self, session_id: str, runtime_adapter: str, model_binding: str, *, actor_id: str) -> Session:
        with sqlite3.connect(self._database_path) as connection:
            connection.row_factory = sqlite3.Row
            session = self.sessions.bind_runtime_in_connection(
                connection, session_id, runtime_adapter, model_binding
            )
            self.events.append_in_connection(
                connection,
                self.event(
                    session.project_id,
                    "SESSION_BOUND",
                    {"runtime_adapter": runtime_adapter, "model_binding": model_binding},
                    actor_id=actor_id,
                    session_id=session.session_id,
                    idempotency_key=f"session:{session.session_id}:bound:{runtime_adapter}:{model_binding}",
                ),
            )
        return session

    def create_task(self, spec: TaskSpec, *, actor_id: str) -> Task:
        if not spec.title.strip():
            raise ValueError("task title must not be empty")
        task = Task(new_id("task"), spec.project_id, spec.title, "CREATED")
        with sqlite3.connect(self._database_path) as connection:
            connection.row_factory = sqlite3.Row
            self.tasks.create_in_connection(connection, task)
            self.events.append_in_connection(
                connection,
                self.event(
                    task.project_id,
                    "TASK_CREATED",
                    {"task_id": task.task_id, "title": task.title},
                    actor_id=actor_id,
                    task_id=task.task_id,
                    idempotency_key=f"task:{task.task_id}:created",
                ),
            )
        return task

    def transition_task(
        self,
        task_id: str,
        expected_state: str,
        target_state: str,
        *,
        actor_id: str,
        session_id: str | None = None,
    ) -> Task:
        with sqlite3.connect(self._database_path) as connection:
            connection.row_factory = sqlite3.Row
            task = self.tasks.transition_in_connection(connection, task_id, expected_state, target_state)
            self.events.append_in_connection(
                connection,
                self.event(
                    task.project_id,
                    "TASK_STATE_CHANGED",
                    {
                        "from": expected_state,
                        "to": target_state,
                        "evidence_refs": [
                            f"event:task:{task.task_id}:state:{target_state}"
                        ] if target_state == "COMPLETED" else [],
                    },
                    actor_id=actor_id,
                    session_id=session_id,
                    task_id=task.task_id,
                    idempotency_key=f"task:{task.task_id}:state:{target_state}",
                ),
            )
        return task

    def add_task_dependency(self, task_id: str, dependency_task_id: str, *, actor_id: str) -> None:
        with sqlite3.connect(self._database_path) as connection:
            connection.row_factory = sqlite3.Row
            self.tasks.add_dependency_in_connection(connection, task_id, dependency_task_id)
            row = connection.execute(
                "SELECT project_id FROM tasks WHERE task_id = ?", (task_id,)
            ).fetchone()
            if row is None:
                raise KeyError(f"unknown task: {task_id}")
            self.events.append_in_connection(
                connection,
                self.event(
                    row["project_id"], "TASK_DEPENDENCY_ADDED",
                    {"task_id": task_id, "dependency_task_id": dependency_task_id},
                    actor_id=actor_id, task_id=task_id,
                    idempotency_key=f"task:{task_id}:depends:{dependency_task_id}",
                ),
            )

    def create_issue(self, issue: Issue, *, actor_id: str) -> Issue:
        with sqlite3.connect(self._database_path) as connection:
            connection.row_factory = sqlite3.Row
            created = self.bank.create_issue_in_connection(connection, issue)
            self.events.append_in_connection(connection, self.event(
                issue.project_id,
                "ISSUE_CREATED",
                {"issue_id": issue.issue_id, "title": issue.title, "blocking": issue.blocking},
                actor_id=actor_id,
                idempotency_key=f"issue:{issue.issue_id}:created",
            ))
        return created

    def record_decision(self, decision: Decision, *, actor_id: str) -> Decision:
        with sqlite3.connect(self._database_path) as connection:
            connection.row_factory = sqlite3.Row
            recorded = self.bank.record_decision_in_connection(connection, decision)
            self.events.append_in_connection(connection, self.event(
                decision.project_id,
                "DECISION_RECORDED",
                {"decision_id": decision.decision_id, "title": decision.title, "supersedes": decision.supersedes},
                actor_id=actor_id,
                idempotency_key=f"decision:{decision.decision_id}:recorded",
            ))
        return recorded

    def resolve_issue(self, issue_id: str, decision_id: str, *, actor_id: str) -> Issue:
        with sqlite3.connect(self._database_path) as connection:
            connection.row_factory = sqlite3.Row
            resolved = self.bank.resolve_issue_in_connection(connection, issue_id, decision_id)
            self.events.append_in_connection(connection, self.event(
                resolved.project_id,
                "ISSUE_RESOLVED",
                {"issue_id": issue_id, "decision_id": decision_id},
                actor_id=actor_id,
                idempotency_key=f"issue:{issue_id}:resolved:{decision_id}",
            ))
        return resolved

    def record_lesson(self, lesson: Lesson, *, actor_id: str) -> Lesson:
        with sqlite3.connect(self._database_path) as connection:
            connection.row_factory = sqlite3.Row
            recorded = self.bank.record_lesson_in_connection(connection, lesson)
            self.events.append_in_connection(connection, self.event(
                lesson.project_id,
                "LESSON_RECORDED",
                {"lesson_id": lesson.lesson_id, "title": lesson.title},
                actor_id=actor_id,
                idempotency_key=f"lesson:{lesson.lesson_id}:recorded",
            ))
        return recorded

    def record_progress(self, progress: Progress, *, actor_id: str) -> Progress:
        with sqlite3.connect(self._database_path) as connection:
            connection.row_factory = sqlite3.Row
            recorded = self.bank.record_progress_in_connection(connection, progress)
            self.events.append_in_connection(connection, self.event(
                progress.project_id,
                "PROGRESS_RECORDED",
                {"progress_id": progress.progress_id, "milestone": progress.milestone, "status": progress.status},
                actor_id=actor_id,
                idempotency_key=f"progress:{progress.progress_id}:recorded",
            ))
        return recorded

    def delegate(self, runtime: Runtime, spec: DelegationSpec, *, actor_id: str) -> Delegation:
        relation = runtime.delegate(spec)
        self.events.append(
            self.event(
                spec.project_id,
                "DELEGATION_CREATED",
                {
                    "relation_id": relation.relation_id,
                    "parent_task_id": spec.parent_task_id,
                    "child_session_id": spec.executor_session_id,
                    "return_contract": spec.return_contract,
                },
                actor_id=actor_id,
                session_id=spec.owner_session_id,
                task_id=spec.parent_task_id,
                idempotency_key=f"delegation:{relation.relation_id}:created",
            )
        )
        return relation

    def complete_delegation(self, runtime: Runtime, relation_id: str, result: str, *, actor_id: str) -> Delegation:
        relation = runtime.complete(relation_id, result)
        self.events.append(
            self.event(
                relation.project_id,
                "CHILD_COMPLETE",
                {"relation_id": relation_id, "result": result},
                actor_id=actor_id,
                session_id=relation.executor_session_id,
                task_id=relation.parent_task_id,
                idempotency_key=f"delegation:{relation_id}:complete",
            )
        )
        return relation

    def review_delegation(
        self, runtime: Runtime, relation_id: str, reviewer_session_id: str, decision: str, *, actor_id: str
    ) -> Delegation:
        relation = runtime.review(relation_id, reviewer_session_id, decision)
        event_type = "REVIEW_ACCEPTED" if decision == "ACCEPT" else "REVIEW_REWORK"
        self.events.append(
            self.event(
                relation.project_id,
                event_type,
                {"relation_id": relation_id, "decision": decision},
                actor_id=actor_id,
                session_id=reviewer_session_id,
                task_id=relation.parent_task_id,
                idempotency_key=f"delegation:{relation_id}:review:{decision}",
            )
        )
        return relation

    def execute_frontier(
        self, frontier: FrontierService, request: FrontierRequest, *, actor_id: str,
        connection: sqlite3.Connection | None = None,
        advisor: Callable[[FrontierRequest], str] | None = None,
    ) -> FrontierResult:
        prefix = "FRONTIER_CONSULT" if request.mode.value == "CONSULT" else "FRONTIER_DELEGATE"
        def emit(event: Event) -> None:
            if connection is None:
                self.events.append(event)
            else:
                self.events.append_in_connection(connection, event)

        emit(
            self.event(
                request.project_id,
                f"{prefix}_REQUESTED",
                {"capability": request.capability, "mode": request.mode.value},
                actor_id=actor_id,
                session_id=request.owner_session_id,
                task_id=request.parent_task_id,
                idempotency_key=f"frontier:{request.parent_task_id}:{request.mode.value}:{request.capability}",
            )
        )
        result = frontier.execute(request, connection=connection, advisor=advisor)
        emit(
            self.event(
                result.project_id,
                f"{prefix}_RETURNED" if request.mode.value == "CONSULT" else "FRONTIER_DELEGATE_STARTED",
                {"request_id": result.request_id, "child_session_id": result.child_session_id},
                actor_id=actor_id,
                session_id=result.owner_session_id,
                task_id=request.parent_task_id,
                idempotency_key=f"frontier:{result.request_id}:returned",
            )
        )
        return result

    def enqueue_session(self, scheduler: Scheduler, session: SessionMeta, *, actor_id: str) -> None:
        scheduler.enqueue(session)
        self.events.append(
            self.event(
                session.project_id,
                "SLOT_REQUESTED",
                {"session_id": session.session_id, "priority": session.priority},
                actor_id=actor_id,
                session_id=session.session_id,
                idempotency_key=f"slot:{session.session_id}:requested",
            )
        )

    def dispatch_session(self, scheduler: Scheduler, *, now: int, actor_id: str) -> SessionMeta | None:
        session = scheduler.dispatch(now=now)
        if session is not None:
            self.events.append(
                self.event(
                    session.project_id,
                    "SLOT_GRANTED",
                    {"session_id": session.session_id, "lease_started": now},
                    actor_id=actor_id,
                    session_id=session.session_id,
                    idempotency_key=f"slot:{session.session_id}:granted:{now}",
                )
            )
        return session

    def recover_lease(self, scheduler: Scheduler, session_id: str, *, now: int, actor_id: str) -> None:
        scheduler.watchdog(now)
        session = scheduler.get(session_id)
        if session.slot_state == "PARKED":
            self.events.append(
                self.event(
                    session.project_id,
                    "LEASE_EXPIRED",
                    {"session_id": session_id, "recovered_at": now},
                    actor_id=actor_id,
                    session_id=session_id,
                    idempotency_key=f"lease:{session_id}:expired:{now}",
                )
            )

    def ingest_worker_event(
        self,
        plugin: DevPlugin,
        project_id: str,
        event: HarnessEvent,
        *,
        actor_id: str,
        session_id: str,
        task_id: str,
    ) -> DevEvent | None:
        normalized = plugin.normalize_harness_event(event)
        if not is_workflow_event(WorkerEvent(normalized.event_type, normalized.payload)):
            return None
        self.events.append(
            self.event(
                project_id,
                normalized.event_type,
                normalized.payload,
                actor_id=actor_id,
                session_id=session_id,
                task_id=task_id,
                idempotency_key=f"worker:{session_id}:{task_id}:{normalized.event_type}",
            )
        )
        return normalized

    def observe_a2cm(
        self,
        plugin: A2CMPlugin,
        observation: Observation,
        source: str,
        *,
        project_id: str,
        actor_id: str,
    ) -> BeliefCandidate:
        candidate = plugin.observe(observation, source, project_id=project_id)
        self.events.append(
            self.event(
                project_id,
                "OBSERVATION_RECORDED",
                {"observation_id": observation.observation_id, "candidate_id": candidate.candidate_id, "source": source},
                actor_id=actor_id,
                idempotency_key=f"observation:{observation.observation_id}:recorded",
            )
        )
        return candidate

    def promote_belief(
        self, plugin: A2CMPlugin, candidate_id: str, *, project_id: str, actor_id: str
    ) -> BeliefCandidate:
        promoted = plugin.promote(candidate_id, actor_id, project_id=project_id)
        self.events.append(
            self.event(
                project_id,
                "BELIEF_PROMOTED",
                {"candidate_id": candidate_id, "status": promoted.status},
                actor_id=actor_id,
                idempotency_key=f"belief:{candidate_id}:promoted",
            )
        )
        return promoted
