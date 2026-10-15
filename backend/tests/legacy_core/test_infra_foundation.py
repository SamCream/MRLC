from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor
import sqlite3

import pytest

from infra.events import Event, EventStore
from infra.identity import new_id
from infra.projects import ProjectRegistry
from infra.sessions import SessionRegistry, SessionSpec
from infra.tasks import TaskRegistry, TaskSpec


def test_ids_use_stable_prefixes() -> None:
    assert new_id("project").startswith("prj_")
    assert new_id("session").startswith("ses_")
    assert new_id("event").startswith("evt_")


def test_event_store_is_idempotent_and_project_scoped(tmp_path) -> None:
    store = EventStore(tmp_path / "infra.sqlite3")
    event = Event(
        event_id="evt_source",
        project_id="prj_one",
        event_type="SESSION_CREATED",
        payload={"session_id": "ses_one"},
        occurred_at=datetime.now(timezone.utc),
        idempotency_key="create-session-one",
    )

    first = store.append(event)
    second = store.append(event)

    assert first.sequence == second.sequence == 1
    assert store.read("prj_one")[0] == event
    assert store.read("prj_two") == []


def test_event_store_rejects_idempotency_conflict(tmp_path) -> None:
    store = EventStore(tmp_path / "infra.sqlite3")
    base = dict(
        project_id="prj_one",
        event_type="SESSION_CREATED",
        occurred_at=datetime.now(timezone.utc),
        idempotency_key="same-key",
    )
    store.append(Event(event_id="evt_one", payload={}, **base))

    with pytest.raises(ValueError, match="idempotency"):
        store.append(Event(event_id="evt_two", payload={"changed": True}, **base))


def test_session_registry_persists_and_replays_state(tmp_path) -> None:
    registry = SessionRegistry(tmp_path / "infra.sqlite3")
    session = registry.create(
        SessionSpec(project_id="prj_one", application_id="dev", role_id="dev.engineer")
    )
    registry.bind_runtime(session.session_id, "codex", "local_176b")
    registry.snapshot(session.session_id, {"context": ["evt_1"]})
    restored = registry.get(session.session_id)

    assert restored.runtime_adapter == "codex"
    assert restored.model_binding == "local_176b"
    assert restored.snapshot == {"context": ["evt_1"]}


def test_session_registry_isolates_projects(tmp_path) -> None:
    registry = SessionRegistry(tmp_path / "infra.sqlite3")
    session = registry.create(
        SessionSpec(project_id="prj_one", application_id="dev", role_id="dev.pm")
    )

    assert registry.list_project("prj_one") == [session]
    assert registry.list_project("prj_two") == []


def test_project_registry_creates_stable_project(tmp_path) -> None:
    registry = ProjectRegistry(tmp_path / "infra.sqlite3")
    project = registry.create("demo")

    assert project.project_id.startswith("prj_")
    assert registry.get(project.project_id) == project


def test_task_registry_requires_expected_state(tmp_path) -> None:
    registry = TaskRegistry(tmp_path / "infra.sqlite3")
    task = registry.create(TaskSpec(project_id="prj_one", title="baseline"))

    updated = registry.transition(task.task_id, "CREATED", "RUNNABLE")

    assert updated.state == "RUNNABLE"
    with pytest.raises(ValueError, match="expected state"):
        registry.transition(task.task_id, "CREATED", "COMPLETED")


def test_task_dependencies_block_and_unblock_transitions(tmp_path) -> None:
    registry = TaskRegistry(tmp_path / "infra.sqlite3")
    dependency = registry.create(TaskSpec(project_id="prj_one", title="dependency"))
    dependent = registry.create(TaskSpec(project_id="prj_one", title="dependent"))
    registry.add_dependency(dependent.task_id, dependency.task_id)

    with pytest.raises(ValueError, match="dependency"):
        registry.transition(dependent.task_id, "CREATED", "RUNNABLE")
    registry.transition(dependency.task_id, "CREATED", "COMPLETED")
    runnable = registry.transition(dependent.task_id, "CREATED", "RUNNABLE")
    blocked = registry.block(runnable.task_id, expected_state="RUNNABLE", reason="waiting")

    assert blocked.state == "BLOCKED"
    assert blocked.block_reason == "waiting"
    assert registry.unblock(blocked.task_id, expected_state="BLOCKED").state == "RUNNABLE"


def test_event_store_replays_project_events(tmp_path) -> None:
    store = EventStore(tmp_path / "infra.sqlite3")
    for index in range(2):
        store.append(
            Event(
                event_id=f"evt_{index}",
                project_id="prj_one",
                event_type="TASK_STATE_CHANGED",
                payload={"index": index},
                occurred_at=datetime.now(timezone.utc),
                idempotency_key=f"event-{index}",
            )
        )

    replayed: list[str] = []
    store.replay("prj_one", lambda event: replayed.append(event.event_id))

    assert replayed == ["evt_0", "evt_1"]


def test_event_store_serializes_concurrent_project_appends_and_replay(tmp_path) -> None:
    store = EventStore(tmp_path / "infra.sqlite3")

    def append(index: int) -> None:
        store.append(
            Event(
                event_id=f"evt-concurrent-{index}", project_id="prj_one",
                event_type="OBSERVED", payload={"index": index},
                occurred_at=datetime.now(timezone.utc),
                idempotency_key=f"concurrent-{index}",
            )
        )

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(append, range(40)))

    first: list[str] = []
    second: list[str] = []
    store.replay("prj_one", lambda event: first.append(event.event_id))
    store.replay("prj_one", lambda event: second.append(event.event_id))

    assert len(first) == 40
    assert first == second
    assert [event.event_id for event in store.read("prj_one")] == first


def test_event_store_reads_legacy_schema_and_migrates_new_columns(tmp_path) -> None:
    database = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE events (project_id TEXT, sequence INTEGER, event_id TEXT UNIQUE, "
            "event_type TEXT, payload TEXT, occurred_at TEXT, idempotency_key TEXT, "
            "PRIMARY KEY (project_id, sequence))"
        )
        occurred_at = datetime(2026, 1, 1, tzinfo=timezone.utc).isoformat()
        connection.execute(
            "INSERT INTO events VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("prj_one", 1, "legacy-1", "LEGACY", '{"ok":true}', occurred_at, "legacy-key"),
        )

    store = EventStore(database)
    store.append(Event(
        event_id="new-2", project_id="prj_one", event_type="NEW", payload={},
        occurred_at=datetime(2026, 1, 2, tzinfo=timezone.utc), idempotency_key="new-key",
    ))

    events = store.read("prj_one")
    assert [event.event_id for event in events] == ["legacy-1", "new-2"]
    assert events[0].recorded_at.isoformat() == occurred_at
    assert events[0].actor_id == "system"


def test_event_envelope_preserves_actor_and_causation_metadata(tmp_path) -> None:
    store = EventStore(tmp_path / "infra.sqlite3")
    event = Event(
        event_id="evt_trace",
        project_id="prj_one",
        event_type="TASK_STATE_CHANGED",
        payload={"state": "RUNNABLE"},
        occurred_at=datetime(2026, 10, 6, tzinfo=timezone.utc),
        idempotency_key="task-state-1",
        actor_type="role",
        actor_id="engineer",
        session_id="ses_one",
        task_id="tsk_one",
        correlation_id="corr_one",
        causation_id="evt_task_created",
    )

    store.append(event)

    restored = store.read("prj_one")[0]
    assert restored.schema_version == 1
    assert restored.actor_type == "role"
    assert restored.actor_id == "engineer"
    assert restored.session_id == "ses_one"
    assert restored.task_id == "tsk_one"
    assert restored.correlation_id == "corr_one"
    assert restored.causation_id == "evt_task_created"
    assert restored.recorded_at >= restored.occurred_at


def test_session_registry_denies_cross_project_lookup(tmp_path) -> None:
    registry = SessionRegistry(tmp_path / "infra.sqlite3")
    session = registry.create(
        SessionSpec(project_id="prj_one", application_id="dev", role_id="dev.pm")
    )

    assert registry.get_for_project("prj_one", session.session_id) == session
    with pytest.raises(PermissionError, match="project boundary"):
        registry.get_for_project("prj_two", session.session_id)


def test_session_lifecycle_is_versioned_and_recovers_after_restart(tmp_path) -> None:
    database = tmp_path / "infra.sqlite3"
    registry = SessionRegistry(database)
    session = registry.create(
        SessionSpec(project_id="prj_one", application_id="dev", role_id="dev.engineer")
    )

    parked = registry.park(session.session_id, expected_version=0, reason="waiting for child")
    assert parked.lifecycle == "PARKED"
    assert parked.version == 1
    assert parked.park_reason == "waiting for child"
    with pytest.raises(ValueError, match="version"):
        registry.resume(session.session_id, expected_version=0)

    restarted = SessionRegistry(database)
    resumed = restarted.resume(session.session_id, expected_version=1)
    archived = restarted.archive(session.session_id, expected_version=2)

    assert resumed.lifecycle == "ACTIVE"
    assert resumed.version == 2
    assert archived.lifecycle == "ARCHIVED"
    assert archived.version == 3
    with pytest.raises(ValueError, match="archived"):
        restarted.bind_runtime(session.session_id, "codex", "router-managed")
