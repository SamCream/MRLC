import pytest

from infra.scheduler import ParkDecision, Scheduler, SessionMeta


def test_scheduler_has_two_reusable_slots() -> None:
    scheduler = Scheduler(capacity=2)
    scheduler.enqueue(SessionMeta("ses_one", "prj_one", priority=10))
    scheduler.enqueue(SessionMeta("ses_two", "prj_one", priority=5))
    scheduler.enqueue(SessionMeta("ses_three", "prj_one", priority=20))

    assert scheduler.dispatch().session_id == "ses_three"
    assert scheduler.dispatch().session_id == "ses_one"
    assert scheduler.dispatch() is None


def test_turn_end_does_not_complete_task() -> None:
    scheduler = Scheduler(capacity=1)
    scheduler.enqueue(SessionMeta("ses_one", "prj_one", priority=1))
    scheduler.dispatch()

    scheduler.turn_end("ses_one")

    assert scheduler.task_state("ses_one") == "RUNNABLE"
    assert scheduler.slot_state("ses_one") == "ACTIVE"


def test_hard_blocking_parent_can_park_and_child_gets_slot() -> None:
    scheduler = Scheduler(capacity=1)
    scheduler.enqueue(SessionMeta("ses_parent", "prj_one", priority=1))
    scheduler.dispatch()
    scheduler.park("ses_parent", ParkDecision.ACCEPT, "hard child block")
    scheduler.enqueue(SessionMeta("ses_child", "prj_one", priority=1))

    assert scheduler.slot_state("ses_parent") == "PARKED"
    assert scheduler.dispatch().session_id == "ses_child"


def test_park_defer_keeps_slot_until_safe_point() -> None:
    scheduler = Scheduler(capacity=1)
    scheduler.enqueue(SessionMeta("ses_one", "prj_one", priority=1))
    scheduler.dispatch()
    scheduler.park("ses_one", ParkDecision.DEFER, "after turn")

    assert scheduler.slot_state("ses_one") == "ACTIVE"
    scheduler.turn_end("ses_one")
    assert scheduler.slot_state("ses_one") == "PARKED"


def test_expired_lease_reclaims_slot() -> None:
    scheduler = Scheduler(capacity=1, lease_ttl_seconds=1)
    scheduler.enqueue(SessionMeta("ses_one", "prj_one", priority=1))
    scheduler.dispatch(now=100)

    scheduler.watchdog(now=102)

    assert scheduler.slot_state("ses_one") == "PARKED"


def test_family_reservation_prioritizes_parent_review() -> None:
    scheduler = Scheduler(capacity=1)
    scheduler.enqueue(SessionMeta("ses_parent", "prj_one", priority=1))
    scheduler.dispatch()
    scheduler.park("ses_parent", ParkDecision.ACCEPT, "child")
    scheduler.complete_child("ses_child", "ses_parent")
    scheduler.enqueue(SessionMeta("ses_other", "prj_one", priority=100))

    assert scheduler.dispatch().session_id == "ses_parent"


def test_unknown_park_decision_is_rejected() -> None:
    scheduler = Scheduler(capacity=1)
    with pytest.raises(ValueError):
        scheduler.park("missing", "invalid", "reason")


def test_scheduler_state_survives_controller_restart(tmp_path) -> None:
    database = tmp_path / "scheduler.sqlite"
    first = Scheduler(capacity=1, database_path=database)
    first.enqueue(SessionMeta("ses_one", "prj_one", priority=10))
    first.enqueue(SessionMeta("ses_two", "prj_one", priority=5))
    assert first.dispatch(now=100).session_id == "ses_one"

    restarted = Scheduler(capacity=1, database_path=database)

    assert restarted.slot_state("ses_one") == "ACTIVE"
    assert restarted.dispatch(now=100) is None
    restarted.park("ses_one", ParkDecision.ACCEPT, "controller restart")
    assert restarted.dispatch(now=101).session_id == "ses_two"


def test_scheduler_fencing_token_survives_restart_and_increments(tmp_path) -> None:
    database = tmp_path / "scheduler-fencing.sqlite"
    first = Scheduler(capacity=1, database_path=database)
    first.enqueue(SessionMeta("ses_one", "prj_one", priority=10))
    dispatched = first.dispatch(now=100)
    assert dispatched is not None
    assert dispatched.fencing_token == 1
    first.park("ses_one", ParkDecision.ACCEPT, "lease expired")

    restarted = Scheduler(capacity=1, database_path=database)
    restarted.enqueue(SessionMeta("ses_one", "prj_one", priority=10))
    dispatched_again = restarted.dispatch(now=200)

    assert dispatched_again is not None
    assert dispatched_again.fencing_token == 2


def test_aging_prevents_low_priority_starvation() -> None:
    scheduler = Scheduler(capacity=1, aging_interval_seconds=10)
    scheduler.enqueue(SessionMeta("ses_old", "prj_one", priority=1), now=0)
    scheduler.enqueue(SessionMeta("ses_new", "prj_one", priority=5), now=35)

    selected = scheduler.dispatch(now=40)

    assert selected is not None
    assert selected.session_id == "ses_old"


def test_repeated_parking_applies_thrashing_cooldown() -> None:
    scheduler = Scheduler(capacity=1, thrash_limit=2, thrash_cooldown_seconds=30)
    scheduler.enqueue(SessionMeta("ses_one", "prj_one", priority=1), now=0)
    assert scheduler.dispatch(now=0) is not None
    scheduler.park("ses_one", ParkDecision.ACCEPT, "first", now=1)
    scheduler.enqueue(SessionMeta("ses_one", "prj_one", priority=1), now=2)
    assert scheduler.dispatch(now=2) is not None
    scheduler.park("ses_one", ParkDecision.ACCEPT, "second", now=3)
    scheduler.enqueue(SessionMeta("ses_one", "prj_one", priority=1), now=4)

    assert scheduler.dispatch(now=4) is None
    assert scheduler.dispatch(now=34) is not None


def test_manual_priority_adjustment_is_audited() -> None:
    scheduler = Scheduler(capacity=1)
    scheduler.enqueue(SessionMeta("ses_one", "prj_one", priority=1))

    scheduler.adjust_priority("ses_one", 9, actor="operator", reason="interactive request")

    assert scheduler.get("ses_one").priority == 9
    assert scheduler.priority_adjustments[-1] == {
        "session_id": "ses_one",
        "old_priority": 1,
        "new_priority": 9,
        "actor": "operator",
        "reason": "interactive request",
    }
