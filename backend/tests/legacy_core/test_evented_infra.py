import sqlite3

from infra.evented_infra import EventedInfra
from infra.a2cm_plugin import A2CMPlugin, Observation
from infra.dev_plugin import DevPlugin, HarnessEvent
from infra.frontier import FrontierMode, FrontierPolicy, FrontierRequest, FrontierService
from infra.project_bank import Decision, Issue, Lesson, Progress
from infra.runtime import DelegationSpec, Profile, RoleDefinition, Runtime
from infra.scheduler import ParkDecision, Scheduler, SessionMeta
from infra.sessions import SessionSpec
from infra.tasks import TaskSpec
import pytest


def test_registry_operations_publish_project_scoped_events(tmp_path) -> None:
    infra = EventedInfra(tmp_path / "infra.sqlite3")
    project = infra.create_project("demo", actor_id="engineer")
    session = infra.create_session(SessionSpec(project.project_id, "dev", "owner"), actor_id="owner")
    infra.bind_session(session.session_id, "codex", "local_176b", actor_id="owner")
    task = infra.create_task(TaskSpec(project.project_id, "implement"), actor_id="owner")
    infra.transition_task(task.task_id, "CREATED", "RUNNABLE", actor_id="owner", session_id=session.session_id)

    events = infra.events.read(project.project_id)

    assert [event.event_type for event in events] == [
        "PROJECT_CREATED",
        "SESSION_CREATED",
        "SESSION_BOUND",
        "TASK_CREATED",
        "TASK_STATE_CHANGED",
    ]
    assert events[-1].actor_id == "owner"
    assert events[-1].session_id == session.session_id
    assert events[-1].task_id == task.task_id


def test_evented_infra_reuses_idempotency_key_without_duplicate_fact(tmp_path) -> None:
    infra = EventedInfra(tmp_path / "infra.sqlite3")
    project = infra.create_project("demo", actor_id="owner", idempotency_key="project-1")

    same = infra.events.append(
        infra.event(
            project.project_id,
            "PROJECT_CREATED",
            {"project_id": project.project_id, "name": "demo"},
            actor_id="owner",
            idempotency_key="project-1",
        )
    )

    assert same.sequence == 1
    assert len(infra.events.read(project.project_id)) == 1


def test_task_transition_rolls_back_when_event_append_fails(tmp_path, monkeypatch) -> None:
    infra = EventedInfra(tmp_path / "infra.sqlite3")
    project = infra.create_project("demo", actor_id="owner")
    task = infra.create_task(TaskSpec(project.project_id, "implement"), actor_id="owner")

    def fail_append(*_args, **_kwargs):
        raise RuntimeError("event append failed")

    monkeypatch.setattr(infra.events, "append_in_connection", fail_append)
    with pytest.raises(RuntimeError, match="event append failed"):
        infra.transition_task(task.task_id, "CREATED", "RUNNABLE", actor_id="owner")

    with sqlite3.connect(tmp_path / "infra.sqlite3") as connection:
        assert connection.execute("SELECT state FROM tasks WHERE task_id = ?", (task.task_id,)).fetchone()[0] == "CREATED"
    assert [event.event_type for event in infra.events.read(project.project_id)] == [
        "PROJECT_CREATED",
        "TASK_CREATED",
    ]


def test_task_creation_rolls_back_when_event_append_fails(tmp_path, monkeypatch) -> None:
    infra = EventedInfra(tmp_path / "infra.sqlite3")
    project = infra.create_project("demo", actor_id="owner")

    def fail_append(*_args, **_kwargs):
        raise RuntimeError("event append failed")

    monkeypatch.setattr(infra.events, "append_in_connection", fail_append)
    with pytest.raises(RuntimeError, match="event append failed"):
        infra.create_task(TaskSpec(project.project_id, "implement"), actor_id="owner")

    with sqlite3.connect(tmp_path / "infra.sqlite3") as connection:
        assert connection.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 0


@pytest.mark.parametrize("operation", ["project", "session"])
def test_project_and_session_creation_roll_back_with_event_failure(tmp_path, monkeypatch, operation) -> None:
    infra = EventedInfra(tmp_path / "infra.sqlite3")

    def fail_append(*_args, **_kwargs):
        raise RuntimeError("event append failed")

    monkeypatch.setattr(infra.events, "append_in_connection", fail_append)
    with pytest.raises(RuntimeError, match="event append failed"):
        if operation == "project":
            infra.create_project("demo", actor_id="owner")
        else:
            infra.create_session(SessionSpec("prj-1", "codex", "owner"), actor_id="owner")

    with sqlite3.connect(tmp_path / "infra.sqlite3") as connection:
        assert connection.execute("SELECT COUNT(*) FROM projects").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0


def test_session_binding_rolls_back_when_event_append_fails(tmp_path, monkeypatch) -> None:
    infra = EventedInfra(tmp_path / "infra.sqlite3")
    project = infra.create_project("demo", actor_id="owner")
    session = infra.create_session(SessionSpec(project.project_id, "codex", "owner"), actor_id="owner")

    def fail_append(*_args, **_kwargs):
        raise RuntimeError("event append failed")

    monkeypatch.setattr(infra.events, "append_in_connection", fail_append)
    with pytest.raises(RuntimeError, match="event append failed"):
        infra.bind_session(session.session_id, "codex", "local_176b", actor_id="owner")

    restored = infra.sessions.get(session.session_id)
    assert restored.runtime_adapter is None
    assert restored.model_binding is None


def test_project_bank_writes_publish_auditable_events(tmp_path) -> None:
    infra = EventedInfra(tmp_path / "infra.sqlite3")
    issue = infra.create_issue(Issue("ISS-1", "prj-1", "choose route", "soft", "engineer", True, True), actor_id="engineer")
    decision = infra.record_decision(Decision("DEC-1", "prj-1", "use route", "engineer", "low", "portable"), actor_id="engineer")
    infra.resolve_issue(issue.issue_id, decision.decision_id, actor_id="engineer")
    infra.record_lesson(Lesson("LES-1", "prj-1", "keep route portable", (decision.decision_id,)), actor_id="engineer")
    infra.record_progress(Progress("PRG-1", "prj-1", "bank events", "green", ("art-1",)), actor_id="engineer")

    assert [event.event_type for event in infra.events.read("prj-1")] == [
        "ISSUE_CREATED",
        "DECISION_RECORDED",
        "ISSUE_RESOLVED",
        "LESSON_RECORDED",
        "PROGRESS_RECORDED",
    ]


@pytest.mark.parametrize("operation", ["issue", "decision", "lesson", "progress", "resolve"])
def test_project_bank_write_rolls_back_when_event_append_fails(tmp_path, monkeypatch, operation) -> None:
    infra = EventedInfra(tmp_path / "infra.sqlite3")
    issue = Issue("ISS-1", "prj-1", "choose route", "soft", "engineer", True, True)
    decision = Decision("DEC-1", "prj-1", "use route", "engineer", "low", "portable")
    if operation == "resolve":
        infra.create_issue(issue, actor_id="engineer")
        infra.record_decision(decision, actor_id="engineer")

    def fail_append(*_args, **_kwargs):
        raise RuntimeError("event append failed")

    monkeypatch.setattr(infra.events, "append_in_connection", fail_append)
    with pytest.raises(RuntimeError, match="event append failed"):
        if operation == "issue":
            infra.create_issue(issue, actor_id="engineer")
        elif operation == "decision":
            infra.record_decision(decision, actor_id="engineer")
        elif operation == "lesson":
            infra.record_lesson(Lesson("LES-1", "prj-1", "keep route portable", ("DEC-1",)), actor_id="engineer")
        elif operation == "progress":
            infra.record_progress(Progress("PRG-1", "prj-1", "bank events", "green", ("art-1",)), actor_id="engineer")
        else:
            infra.resolve_issue(issue.issue_id, decision.decision_id, actor_id="engineer")

    with sqlite3.connect(tmp_path / "infra.sqlite3") as connection:
        if operation == "issue":
            assert connection.execute("SELECT COUNT(*) FROM bank_issues").fetchone()[0] == 0
        elif operation == "decision":
            assert connection.execute("SELECT COUNT(*) FROM bank_decisions").fetchone()[0] == 0
        elif operation == "lesson":
            assert connection.execute("SELECT COUNT(*) FROM bank_lessons").fetchone()[0] == 0
        elif operation == "progress":
            assert connection.execute("SELECT COUNT(*) FROM bank_progress").fetchone()[0] == 0
        else:
            row = connection.execute("SELECT status, resolved_by FROM bank_issues WHERE issue_id = ?", (issue.issue_id,)).fetchone()
            assert row == ("OPEN", None)


def test_runtime_frontier_and_scheduler_publish_state_events(tmp_path) -> None:
    infra = EventedInfra(tmp_path / "infra.sqlite3")
    runtime = Runtime(Profile("dev", {"owner": RoleDefinition("owner", frozenset(), frozenset({"delegate"}))}))
    relation = infra.delegate(
        runtime,
        DelegationSpec("prj-1", "tsk-1", "ses-owner", "ses-child", "engineer", "artifact,tests"),
        actor_id="ses-owner",
    )
    infra.complete_delegation(runtime, relation.relation_id, "artifact,tests", actor_id="ses-child")
    infra.review_delegation(runtime, relation.relation_id, "ses-owner", "ACCEPT", actor_id="ses-owner")

    frontier = FrontierService(FrontierPolicy(True, 2), advisor=lambda request: f"advisor:{request.capability}")
    request = FrontierRequest("prj-1", "tsk-1", "ses-owner", "research", FrontierMode.CONSULT, 1)
    infra.execute_frontier(frontier, request, actor_id="ses-owner")

    scheduler = Scheduler(capacity=1, lease_ttl_seconds=1)
    infra.enqueue_session(scheduler, SessionMeta("ses-owner", "prj-1", 10), actor_id="ses-owner")
    infra.dispatch_session(scheduler, now=1, actor_id="ses-owner")
    infra.recover_lease(scheduler, "ses-owner", now=3, actor_id="system")

    assert [event.event_type for event in infra.events.read("prj-1")] == [
        "DELEGATION_CREATED",
        "CHILD_COMPLETE",
        "REVIEW_ACCEPTED",
        "FRONTIER_CONSULT_REQUESTED",
        "FRONTIER_CONSULT_RETURNED",
        "SLOT_REQUESTED",
        "SLOT_GRANTED",
        "LEASE_EXPIRED",
    ]


def test_plugins_publish_only_workflow_and_primary_belief_events(tmp_path) -> None:
    infra = EventedInfra(tmp_path / "infra.sqlite3")
    workflow = infra.ingest_worker_event(
        DevPlugin(), "prj-1", HarnessEvent("READY_FOR_REVIEW", {"artifact": "art-1", "harness_metadata": "private"}), actor_id="ses-worker", session_id="ses-worker", task_id="tsk-1"
    )
    ignored = infra.ingest_worker_event(
        DevPlugin(), "prj-1", HarnessEvent("TOOL_CALL", {"tool": "shell"}), actor_id="ses-worker", session_id="ses-worker", task_id="tsk-1"
    )
    plugin = A2CMPlugin()
    candidate = infra.observe_a2cm(plugin, Observation("obs-1", "actor-1", "likes tea", "mem-1", 0.8), "fast_tool", project_id="prj-1", actor_id="system")
    infra.promote_belief(plugin, candidate.candidate_id, project_id="prj-1", actor_id="primary")

    assert workflow is not None
    assert ignored is None
    assert [event.event_type for event in infra.events.read("prj-1")] == [
        "READY_FOR_REVIEW",
        "OBSERVATION_RECORDED",
        "BELIEF_PROMOTED",
    ]
    assert "harness_metadata" not in infra.events.read("prj-1")[0].payload


def test_task_dependency_event_rolls_back_with_dependency_write(tmp_path, monkeypatch) -> None:
    infra = EventedInfra(tmp_path / "infra.sqlite3")
    project = infra.create_project("demo", actor_id="owner")
    dependency = infra.create_task(TaskSpec(project.project_id, "dependency"), actor_id="owner")
    dependent = infra.create_task(TaskSpec(project.project_id, "dependent"), actor_id="owner")

    def fail_append(*_args, **_kwargs):
        raise RuntimeError("event append failed")

    monkeypatch.setattr(infra.events, "append_in_connection", fail_append)
    with pytest.raises(RuntimeError, match="event append failed"):
        infra.add_task_dependency(dependent.task_id, dependency.task_id, actor_id="owner")

    with sqlite3.connect(tmp_path / "infra.sqlite3") as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM task_dependencies WHERE task_id = ?",
            (dependent.task_id,),
        ).fetchone()[0] == 0
