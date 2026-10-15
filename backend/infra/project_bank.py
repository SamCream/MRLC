from __future__ import annotations

import sqlite3
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path


@dataclass(frozen=True, slots=True)
class Issue:
    issue_id: str
    project_id: str
    title: str
    blocking: str
    decision_owner: str
    auto_decidable: bool
    reversible: bool
    status: str = "OPEN"
    resolved_by: str | None = None


@dataclass(frozen=True, slots=True)
class Decision:
    decision_id: str
    project_id: str
    title: str
    authority: str
    risk: str
    rationale: str
    supersedes: str | None = None
    status: str = "active"


@dataclass(frozen=True, slots=True)
class Lesson:
    lesson_id: str
    project_id: str
    title: str
    source_refs: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Progress:
    progress_id: str
    project_id: str
    milestone: str
    status: str
    artifact_refs: tuple[str, ...]


class Bank:
    def __init__(self, database_path: str | Path) -> None:
        self._database_path = str(database_path)
        with sqlite3.connect(self._database_path) as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS bank_issues "
                "(issue_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, title TEXT NOT NULL, blocking TEXT NOT NULL, "
                "decision_owner TEXT NOT NULL, auto_decidable INTEGER NOT NULL, reversible INTEGER NOT NULL, "
                "status TEXT NOT NULL, resolved_by TEXT)"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS bank_decisions "
                "(decision_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, title TEXT NOT NULL, authority TEXT NOT NULL, "
                "risk TEXT NOT NULL, rationale TEXT NOT NULL, supersedes TEXT, status TEXT NOT NULL)"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS bank_lessons "
                "(lesson_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, title TEXT NOT NULL, source_refs TEXT NOT NULL)"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS bank_progress "
                "(progress_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, milestone TEXT NOT NULL, "
                "status TEXT NOT NULL, artifact_refs TEXT NOT NULL)"
            )

    def create_issue(self, issue: Issue) -> Issue:
        with sqlite3.connect(self._database_path) as connection:
            return self.create_issue_in_connection(connection, issue)

    def create_issue_in_connection(self, connection: sqlite3.Connection, issue: Issue) -> Issue:
        connection.execute(
            "INSERT INTO bank_issues VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (issue.issue_id, issue.project_id, issue.title, issue.blocking, issue.decision_owner,
             int(issue.auto_decidable), int(issue.reversible), issue.status, issue.resolved_by),
        )
        return issue

    def record_decision(self, decision: Decision) -> Decision:
        with sqlite3.connect(self._database_path) as connection:
            return self.record_decision_in_connection(connection, decision)

    def record_decision_in_connection(self, connection: sqlite3.Connection, decision: Decision) -> Decision:
        connection.execute(
            "INSERT INTO bank_decisions VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (decision.decision_id, decision.project_id, decision.title, decision.authority, decision.risk,
             decision.rationale, decision.supersedes, decision.status),
        )
        if decision.supersedes:
            connection.execute(
                "UPDATE bank_decisions SET status = 'superseded' WHERE decision_id = ? AND project_id = ?",
                (decision.supersedes, decision.project_id),
            )
        return decision

    def record_lesson(self, lesson: Lesson) -> Lesson:
        with sqlite3.connect(self._database_path) as connection:
            return self.record_lesson_in_connection(connection, lesson)

    def record_lesson_in_connection(self, connection: sqlite3.Connection, lesson: Lesson) -> Lesson:
        if not lesson.source_refs:
            raise ValueError("lesson requires source references")
        connection.execute("INSERT INTO bank_lessons VALUES (?, ?, ?, ?)",
                           (lesson.lesson_id, lesson.project_id, lesson.title, "\n".join(lesson.source_refs)))
        return lesson

    def record_progress(self, progress: Progress) -> Progress:
        with sqlite3.connect(self._database_path) as connection:
            return self.record_progress_in_connection(connection, progress)

    def record_progress_in_connection(self, connection: sqlite3.Connection, progress: Progress) -> Progress:
        connection.execute("INSERT INTO bank_progress VALUES (?, ?, ?, ?, ?)",
                           (progress.progress_id, progress.project_id, progress.milestone,
                            progress.status, "\n".join(progress.artifact_refs)))
        return progress

    def resolve_issue(self, issue_id: str, decision_id: str) -> Issue:
        with sqlite3.connect(self._database_path) as connection:
            return self.resolve_issue_in_connection(connection, issue_id, decision_id)

    def resolve_issue_in_connection(
        self, connection: sqlite3.Connection, issue_id: str, decision_id: str
    ) -> Issue:
        issue = self._get_issue_in_connection(connection, issue_id)
        updated = replace(issue, status="RESOLVED", resolved_by=decision_id)
        connection.execute("UPDATE bank_issues SET status = ?, resolved_by = ? WHERE issue_id = ?",
                           (updated.status, updated.resolved_by, issue_id))
        return updated

    def open_issues(self, project_id: str) -> list[Issue]:
        with sqlite3.connect(self._database_path) as connection:
            rows = connection.execute("SELECT * FROM bank_issues WHERE project_id = ? AND status NOT IN ('RESOLVED', 'WONT_FIX', 'DUPLICATE')", (project_id,)).fetchall()
        return [self._issue(row) for row in rows]

    def get_issue(self, project_id: str, issue_id: str) -> Issue:
        issue = self._get_issue(issue_id)
        if issue.project_id != project_id:
            raise PermissionError("issue access crosses project boundary")
        return issue

    def get_issue_by_id(self, issue_id: str) -> Issue:
        return self._get_issue(issue_id)

    def get_decision(self, project_id: str, decision_id: str) -> Decision:
        with sqlite3.connect(self._database_path) as connection:
            row = connection.execute("SELECT * FROM bank_decisions WHERE decision_id = ?", (decision_id,)).fetchone()
        if row is None:
            raise KeyError(f"unknown decision: {decision_id}")
        if row[1] != project_id:
            raise PermissionError("decision access crosses project boundary")
        return Decision(row[0], row[1], row[2], row[3], row[4], row[5], row[6], row[7])

    def get_progress(self, project_id: str, progress_id: str) -> Progress:
        with sqlite3.connect(self._database_path) as connection:
            row = connection.execute("SELECT * FROM bank_progress WHERE progress_id = ?", (progress_id,)).fetchone()
        if row is None:
            raise KeyError(f"unknown progress: {progress_id}")
        if row[1] != project_id:
            raise PermissionError("progress access crosses project boundary")
        return Progress(row[0], row[1], row[2], row[3], tuple(filter(None, row[4].split("\n"))))

    def fallback(self, issue_id: str, waited_since: datetime, *, now: datetime | None = None, actor: str) -> Decision:
        issue = self._get_issue(issue_id)
        now = now or datetime.now(timezone.utc)
        if not issue.auto_decidable or not issue.reversible:
            raise PermissionError("issue is not eligible for safe fallback")
        if now - waited_since < timedelta(hours=1):
            raise TimeoutError("one hour fallback window has not elapsed")
        decision = Decision(f"DEC-FALLBACK-{issue.issue_id}", issue.project_id, f"fallback: {issue.title}", "policy", "low", f"safe timeout fallback by {actor}")
        self.record_decision(decision)
        self.resolve_issue(issue.issue_id, decision.decision_id)
        return decision

    def _get_issue(self, issue_id: str) -> Issue:
        with sqlite3.connect(self._database_path) as connection:
            return self._get_issue_in_connection(connection, issue_id)

    def _get_issue_in_connection(self, connection: sqlite3.Connection, issue_id: str) -> Issue:
        row = connection.execute("SELECT * FROM bank_issues WHERE issue_id = ?", (issue_id,)).fetchone()
        if row is None:
            raise KeyError(f"unknown issue: {issue_id}")
        return self._issue(row)

    @staticmethod
    def _issue(row: tuple[object, ...]) -> Issue:
        return Issue(row[0], row[1], row[2], row[3], row[4], bool(row[5]), bool(row[6]), row[7], row[8])
