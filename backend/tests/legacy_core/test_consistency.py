import sqlite3

import pytest

from infra.consistency import ConsistencyStore, LeaseConflict, StaleFence, StaleVersion


def test_transition_commits_state_and_outbox_atomically(tmp_path) -> None:
    store = ConsistencyStore(tmp_path / "consistency.sqlite3")
    store.create_aggregate("task", "tsk-1", "prj-1", "QUEUED", {"title": "demo"})

    receipt = store.transition(
        aggregate_type="task",
        aggregate_id="tsk-1",
        expected_version=0,
        expected_state="QUEUED",
        target_state="RUNNING",
        operation_id="op-1",
        event_type="TASK_STARTED",
        payload={"task_id": "tsk-1"},
    )

    assert receipt.version == 1
    assert store.get_aggregate("tsk-1").state == "RUNNING"
    assert [item.event_type for item in store.list_outbox()] == ["TASK_STARTED"]


def test_outbox_claim_and_publish_are_recoverable(tmp_path) -> None:
    store = ConsistencyStore(tmp_path / "consistency.sqlite3")
    store.create_aggregate("task", "tsk-1", "prj-1", "QUEUED", {})
    store.transition("task", "tsk-1", 0, "QUEUED", "RUNNING", "op-1", "TASK_STARTED", {})

    claimed = store.claim_outbox("publisher-a", now=10, lease_seconds=5)
    assert [message.message_id for message in claimed] == ["out_op-1"]
    assert store.claim_outbox("publisher-b", now=10, lease_seconds=5) == []
    store.fail_outbox("out_op-1", "publisher-a", now=15, error="connection lost")
    assert [message.message_id for message in store.claim_outbox("publisher-b", now=16, lease_seconds=5)] == ["out_op-1"]
    store.mark_published("out_op-1", "publisher-b", now=17)
    assert store.list_outbox()[0].published_at == 17


def test_outbox_can_be_moved_to_persistent_dead_letter(tmp_path) -> None:
    store = ConsistencyStore(tmp_path / "consistency.sqlite3")
    store.create_aggregate("task", "tsk-1", "prj-1", "QUEUED", {})
    store.transition("task", "tsk-1", 0, "QUEUED", "RUNNING", "op-1", "TASK_STARTED", {})

    claimed = store.claim_outbox("publisher-a", now=10, lease_seconds=5)[0]
    store.dead_letter_outbox(claimed.message_id, "publisher-a", now=11, error="permanent schema error")

    message = store.list_outbox()[0]
    assert message.dead_letter_at == 11
    assert message.dead_letter_reason == "permanent schema error"
    assert store.claim_outbox("publisher-b", now=100, lease_seconds=5) == []


def test_transition_rejects_stale_version_and_replays_operation(tmp_path) -> None:
    store = ConsistencyStore(tmp_path / "consistency.sqlite3")
    store.create_aggregate("task", "tsk-1", "prj-1", "QUEUED", {})
    first = store.transition("task", "tsk-1", 0, "QUEUED", "RUNNING", "op-1", "TASK_STARTED", {})

    assert store.transition("task", "tsk-1", 0, "QUEUED", "RUNNING", "op-1", "TASK_STARTED", {}) == first
    with pytest.raises(StaleVersion):
        store.transition("task", "tsk-1", 0, "QUEUED", "DONE", "op-2", "TASK_DONE", {})


def test_inbox_applies_duplicate_message_once(tmp_path) -> None:
    store = ConsistencyStore(tmp_path / "consistency.sqlite3")
    with sqlite3.connect(tmp_path / "consistency.sqlite3") as connection:
        connection.execute("CREATE TABLE effects (message_id TEXT PRIMARY KEY, count INTEGER NOT NULL)")

    def apply(connection: sqlite3.Connection, payload: dict[str, object]) -> dict[str, object]:
        connection.execute("INSERT INTO effects(message_id, count) VALUES (?, ?)", (payload["id"], 1))
        return {"applied": True}

    assert store.consume("worker", "msg-1", {"id": "msg-1"}, apply) == {"applied": True}
    assert store.consume("worker", "msg-1", {"id": "msg-1"}, apply) == {"applied": True}
    with sqlite3.connect(tmp_path / "consistency.sqlite3") as connection:
        assert connection.execute("SELECT COUNT(*) FROM effects").fetchone()[0] == 1


def test_fencing_rejects_old_holder_after_reacquire(tmp_path) -> None:
    store = ConsistencyStore(tmp_path / "consistency.sqlite3")
    first = store.acquire_lease("slot-1", "worker-a", now=0, ttl=10)
    store.release_lease("slot-1", "worker-a", first.fencing_token)
    second = store.acquire_lease("slot-1", "worker-b", now=11, ttl=10)
    assert second.fencing_token > first.fencing_token

    with pytest.raises(StaleFence):
        store.assert_fence("slot-1", "worker-a", first.fencing_token, now=11)
    store.assert_fence("slot-1", "worker-b", second.fencing_token, now=11)


def test_inbox_rolls_back_effect_when_consumer_fails(tmp_path) -> None:
    store = ConsistencyStore(tmp_path / "consistency.sqlite3")
    with sqlite3.connect(tmp_path / "consistency.sqlite3") as connection:
        connection.execute("CREATE TABLE effects (id TEXT PRIMARY KEY)")

    def apply(connection: sqlite3.Connection, payload: dict[str, object]) -> dict[str, object]:
        connection.execute("INSERT INTO effects(id) VALUES (?)", (payload["id"],))
        raise RuntimeError("consumer crashed")

    with pytest.raises(RuntimeError, match="consumer crashed"):
        store.consume("worker", "msg-1", {"id": "msg-1"}, apply)
    with sqlite3.connect(tmp_path / "consistency.sqlite3") as connection:
        assert connection.execute("SELECT COUNT(*) FROM effects").fetchone()[0] == 0


def test_saga_transitions_are_explicit_and_idempotent(tmp_path) -> None:
    store = ConsistencyStore(tmp_path / "consistency.sqlite3")
    store.create_saga("saga-1", "delegate", {"task_id": "tsk-1"})
    store.transition_saga("saga-1", "REQUESTED", "PREPARED", "op-1")
    store.transition_saga("saga-1", "PREPARED", "DISPATCHED", "op-2")

    assert store.get_saga("saga-1").state == "DISPATCHED"
    assert store.transition_saga("saga-1", "PREPARED", "DISPATCHED", "op-2").state == "DISPATCHED"
    with pytest.raises(ValueError):
        store.transition_saga("saga-1", "DISPATCHED", "REQUESTED", "op-3")
