from __future__ import annotations

from datetime import datetime, timezone

from infra.evented_infra import EventedInfra
from infra.events import Event
import pytest

from infra.replay import ReplayInvariantError, replay_project, validate_replay_invariants
from infra.sessions import SessionSpec
from infra.tasks import TaskSpec


def test_replay_reconstructs_project_session_task_and_dependencies(tmp_path) -> None:
    infra = EventedInfra(tmp_path / "infra.sqlite3")
    project = infra.create_project("demo", actor_id="owner")
    session = infra.create_session(
        SessionSpec(project.project_id, "dev", "dev.engineer"), actor_id="owner"
    )
    infra.bind_session(session.session_id, "codex", "router-managed", actor_id="owner")
    dependency = infra.create_task(TaskSpec(project.project_id, "dependency"), actor_id="owner")
    dependent = infra.create_task(TaskSpec(project.project_id, "dependent"), actor_id="owner")
    infra.add_task_dependency(dependent.task_id, dependency.task_id, actor_id="owner")
    infra.transition_task(dependency.task_id, "CREATED", "COMPLETED", actor_id="owner")
    infra.transition_task(dependent.task_id, "CREATED", "RUNNABLE", actor_id="owner")

    first = replay_project(infra.events.read(project.project_id))
    second = replay_project(infra.events.read(project.project_id))

    assert first == second
    assert first.projects[project.project_id] == "demo"
    assert first.sessions[session.session_id] == "dev.engineer"
    assert first.tasks[dependency.task_id] == "COMPLETED"
    assert first.tasks[dependent.task_id] == "RUNNABLE"
    assert first.dependencies == ((dependent.task_id, dependency.task_id),)


def test_replay_projects_delegation_lease_bank_artifact_and_memory_invariants() -> None:
    events = [
        Event(
            sequence=1,
            event_id="evt_project",
            project_id="prj_one",
            event_type="PROJECT_CREATED",
            payload={"project_id": "prj_one", "name": "demo"},
            occurred_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            idempotency_key="project",
        ),
        Event(
            sequence=2,
            event_id="evt_task",
            project_id="prj_one",
            event_type="TASK_CREATED",
            payload={"task_id": "task_1", "title": "child"},
            occurred_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            idempotency_key="task",
        ),
        Event(
            sequence=3,
            event_id="evt_delegation",
            project_id="prj_one",
            event_type="DELEGATION_CREATED",
            payload={
                "relation_id": "rel_1",
                "parent_task_id": "task_1",
                "child_session_id": "session_child",
                "return_contract": "artifact,tests",
            },
            occurred_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            idempotency_key="delegation",
        ),
        Event(
            sequence=4,
            event_id="evt_child",
            project_id="prj_one",
            event_type="CHILD_COMPLETE",
            payload={"relation_id": "rel_1", "result": "artifact_1"},
            occurred_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            idempotency_key="child",
        ),
        Event(
            sequence=5,
            event_id="evt_review",
            project_id="prj_one",
            event_type="REVIEW_ACCEPTED",
            payload={"relation_id": "rel_1", "decision": "ACCEPT"},
            occurred_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            idempotency_key="review",
        ),
        Event(
            sequence=6,
            event_id="evt_lease",
            project_id="prj_one",
            event_type="LEASE_ACQUIRED",
            payload={"resource_id": "slot_1", "fencing_token": 4},
            occurred_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            idempotency_key="lease",
        ),
        Event(
            sequence=7,
            event_id="evt_issue",
            project_id="prj_one",
            event_type="ISSUE_CREATED",
            payload={"issue_id": "ISS-1", "blocking": True},
            occurred_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            idempotency_key="issue",
        ),
        Event(
            sequence=8,
            event_id="evt_decision",
            project_id="prj_one",
            event_type="DECISION_RECORDED",
            payload={"decision_id": "DEC-1", "supersedes": None},
            occurred_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            idempotency_key="decision",
        ),
        Event(
            sequence=9,
            event_id="evt_artifact",
            project_id="prj_one",
            event_type="ARTIFACT_RECORDED",
            payload={"artifact_id": "artifact_1", "content_hash": "hash"},
            occurred_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            idempotency_key="artifact",
        ),
        Event(
            sequence=10,
            event_id="evt_memory",
            project_id="prj_one",
            event_type="MEMORY_RECORDED",
            payload={"memory_id": "memory_1", "status": "ACTIVE"},
            occurred_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            idempotency_key="memory",
        ),
    ]

    state = replay_project(events)

    assert state.delegations == {"rel_1": "ACCEPTED"}
    assert state.leases == {"slot_1": 4}
    assert state.issues == {"ISS-1": "OPEN"}
    assert state.decisions == {"DEC-1": None}
    assert state.artifacts == {"artifact_1": "hash"}
    assert state.memories == {"memory_1": "ACTIVE"}
    assert validate_replay_invariants(events, state) == state


def test_replay_rejects_review_before_child_completion() -> None:
    events = [
        Event(event_id="project", project_id="prj_one", event_type="PROJECT_CREATED", payload={"project_id": "prj_one", "name": "demo"}, occurred_at=datetime(2026, 1, 1, tzinfo=timezone.utc), idempotency_key="project", sequence=1),
        Event(event_id="task", project_id="prj_one", event_type="TASK_CREATED", payload={"task_id": "task_1", "title": "parent"}, occurred_at=datetime(2026, 1, 1, tzinfo=timezone.utc), idempotency_key="task", sequence=2),
        Event(event_id="delegation", project_id="prj_one", event_type="DELEGATION_CREATED", payload={"relation_id": "rel_1", "parent_task_id": "task_1", "child_session_id": "child", "return_contract": "tests"}, occurred_at=datetime(2026, 1, 1, tzinfo=timezone.utc), idempotency_key="delegation", sequence=3),
        Event(event_id="review", project_id="prj_one", event_type="REVIEW_ACCEPTED", payload={"relation_id": "rel_1"}, occurred_at=datetime(2026, 1, 1, tzinfo=timezone.utc), idempotency_key="review", sequence=4),
    ]

    with pytest.raises(ReplayInvariantError, match="before child completion"):
        validate_replay_invariants(events)


def test_replay_rejects_completed_task_without_evidence() -> None:
    events = [
        Event(event_id="project", project_id="prj_one", event_type="PROJECT_CREATED", payload={"project_id": "prj_one", "name": "demo"}, occurred_at=datetime(2026, 1, 1, tzinfo=timezone.utc), idempotency_key="project", sequence=1),
        Event(event_id="task", project_id="prj_one", event_type="TASK_CREATED", payload={"task_id": "task_1", "title": "parent"}, occurred_at=datetime(2026, 1, 1, tzinfo=timezone.utc), idempotency_key="task", sequence=2),
        Event(event_id="complete", project_id="prj_one", event_type="TASK_STATE_CHANGED", payload={"from": "CREATED", "to": "COMPLETED"}, occurred_at=datetime(2026, 1, 1, tzinfo=timezone.utc), idempotency_key="complete", task_id="task_1", sequence=3),
    ]

    with pytest.raises(ReplayInvariantError, match="evidence"):
        validate_replay_invariants(events)
