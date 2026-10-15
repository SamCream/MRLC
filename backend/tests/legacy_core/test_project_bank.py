from datetime import datetime, timedelta, timezone

import pytest

from infra.project_bank import Bank, Decision, Issue, Lesson, Progress


def test_issue_resolution_removes_open_view_but_keeps_record(tmp_path) -> None:
    bank = Bank(tmp_path / "infra.sqlite3")
    issue = bank.create_issue(Issue("ISS-1", "prj_one", "missing adapter", "hard", "engineer", True, True))
    decision = bank.record_decision(
        Decision("DEC-1", "prj_one", "use adapter", "engineer", "reversible", "create adapter")
    )

    bank.resolve_issue(issue.issue_id, decision.decision_id)

    assert bank.open_issues("prj_one") == []
    assert bank.get_issue("prj_one", issue.issue_id).resolved_by == decision.decision_id


def test_decisions_are_superseded_not_overwritten(tmp_path) -> None:
    bank = Bank(tmp_path / "infra.sqlite3")
    first = bank.record_decision(Decision("DEC-1", "prj_one", "use sqlite", "engineer", "low", "portable"))
    second = bank.record_decision(
        Decision("DEC-2", "prj_one", "use postgres", "engineer", "medium", "concurrency", supersedes="DEC-1")
    )

    assert bank.get_decision("prj_one", first.decision_id).status == "superseded"
    assert bank.get_decision("prj_one", second.decision_id).status == "active"


def test_lesson_requires_source_and_progress_is_append_only(tmp_path) -> None:
    bank = Bank(tmp_path / "infra.sqlite3")
    with pytest.raises(ValueError, match="source"):
        bank.record_lesson(Lesson("LES-1", "prj_one", "avoid drift", ()))
    bank.record_lesson(Lesson("LES-1", "prj_one", "avoid drift", ("DEC-1",)))
    progress = bank.record_progress(Progress("PRG-1", "prj_one", "epic", "green", ("art-1",)))

    assert bank.get_progress("prj_one", progress.progress_id) == progress


def test_safe_fallback_requires_all_guards(tmp_path) -> None:
    bank = Bank(tmp_path / "infra.sqlite3")
    issue = bank.create_issue(Issue("ISS-1", "prj_one", "choice", "soft", "user", True, True))
    waited = datetime.now(timezone.utc) - timedelta(hours=2)

    decision = bank.fallback(issue.issue_id, waited, now=datetime.now(timezone.utc), actor="pm")

    assert decision.authority == "policy"
    assert bank.get_issue("prj_one", issue.issue_id).resolved_by == decision.decision_id


def test_fallback_rejects_high_risk_or_short_wait(tmp_path) -> None:
    bank = Bank(tmp_path / "infra.sqlite3")
    risky = bank.create_issue(Issue("ISS-1", "prj_one", "delete data", "hard", "user", True, False))
    recent = bank.create_issue(Issue("ISS-2", "prj_one", "choice", "soft", "user", True, True))
    now = datetime.now(timezone.utc)

    with pytest.raises(PermissionError, match="fallback"):
        bank.fallback(risky.issue_id, now - timedelta(hours=2), now=now, actor="pm")
    with pytest.raises(TimeoutError, match="one hour"):
        bank.fallback(recent.issue_id, now - timedelta(minutes=30), now=now, actor="pm")
