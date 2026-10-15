from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
import sqlite3
from typing import Callable
from pathlib import Path
from uuid import uuid4


class FrontierMode(StrEnum):
    CONSULT = "CONSULT"
    DELEGATE = "DELEGATE"


class FrontierAdvisorUnavailable(RuntimeError):
    """Raised when a CONSULT request cannot be answered by a real frontier advisor."""


@dataclass(frozen=True, slots=True)
class FrontierPolicy:
    allow_delegation: bool
    budget: int
    max_depth: int = 1


@dataclass(frozen=True, slots=True)
class FrontierRequest:
    project_id: str
    parent_task_id: str
    owner_session_id: str
    capability: str
    mode: FrontierMode
    budget: int
    depth: int = 0
    child_session_id: str | None = None


@dataclass(frozen=True, slots=True)
class FrontierResult:
    request_id: str
    project_id: str
    mode: FrontierMode
    owner_session_id: str
    task_owner_session_id: str
    child_session_id: str | None
    advice: str | None
    return_contract: str | None
    review_state: str
    budget_used: int


class FrontierService:
    def __init__(self, policy: FrontierPolicy, database_path: str | Path | None = None,
                 advisor: Callable[[FrontierRequest], str] | None = None) -> None:
        self.policy = policy
        self._database_path = str(database_path) if database_path is not None else None
        self._advisor = advisor
        self._requests: dict[str, FrontierResult] = {}
        if self._database_path is not None:
            self._initialize_persistence()
            self._load_requests()

    def execute(self, request: FrontierRequest, *,
                connection: sqlite3.Connection | None = None,
                advisor: Callable[[FrontierRequest], str] | None = None) -> FrontierResult:
        if request.mode is FrontierMode.CONSULT:
            # Without a bound advisor there is no real frontier answer. Returning an
            # unexecuted request is honest; synthesizing placeholder text is not.
            active_advisor = advisor if advisor is not None else self._advisor
            if active_advisor is None:
                raise FrontierAdvisorUnavailable(
                    "frontier CONSULT requires a bound advisor; no advice was generated")
            advice = active_advisor(request)
            if not isinstance(advice, str) or not advice.strip():
                raise FrontierAdvisorUnavailable("frontier advisor returned no advice")
            result = FrontierResult(
                request_id=f"inv_{uuid4().hex}",
                project_id=request.project_id,
                mode=request.mode,
                owner_session_id=request.owner_session_id,
                task_owner_session_id=request.owner_session_id,
                child_session_id=None,
                advice=advice.strip(),
                return_contract=None,
                review_state="ADVICE_RETURNED",
                budget_used=request.budget,
            )
            self._requests[result.request_id] = result
            self._persist(result, connection)
            return result

        if not self.policy.allow_delegation:
            raise PermissionError("frontier delegation disabled")
        if request.depth >= self.policy.max_depth:
            raise PermissionError("frontier delegation depth exceeded")
        if request.budget > self.policy.budget:
            raise RuntimeError("frontier delegation budget exceeded")
        result = FrontierResult(
            request_id=f"inv_{uuid4().hex}",
            project_id=request.project_id,
            mode=request.mode,
            owner_session_id=request.owner_session_id,
            task_owner_session_id=request.owner_session_id,
            child_session_id=request.child_session_id or f"ses_{uuid4().hex}",
            advice=None,
            return_contract="artifact,tests,risks",
            review_state="WAIT_OWNER_REVIEW",
            budget_used=request.budget,
        )
        self._requests[result.request_id] = result
        self._persist(result, connection)
        return result

    def review(self, request_id: str, reviewer_session_id: str, decision: str, completed: bool) -> FrontierResult:
        try:
            result = self._requests[request_id]
        except KeyError as error:
            raise KeyError(f"unknown frontier request: {request_id}") from error
        if reviewer_session_id != result.owner_session_id:
            raise PermissionError("only owner can review frontier delegation")
        if result.mode is not FrontierMode.DELEGATE:
            raise ValueError("consult result does not require delegation review")
        if not completed:
            raise ValueError("delegation result is not completed")
        if decision not in {"ACCEPT", "REWORK", "REJECT"}:
            raise ValueError(f"unsupported review decision: {decision}")
        state = {"ACCEPT": "ACCEPTED", "REWORK": "REWORK", "REJECT": "REJECTED"}[decision]
        updated = replace(result, review_state=state)
        self._requests[request_id] = updated
        self._persist(updated)
        return updated

    def _connect(self) -> sqlite3.Connection:
        if self._database_path is None:
            raise RuntimeError("frontier persistence is not configured")
        connection = sqlite3.connect(self._database_path, timeout=30.0)
        connection.execute("PRAGMA busy_timeout = 30000")
        return connection

    def _initialize_persistence(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS frontier_requests (
                    request_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    owner_session_id TEXT NOT NULL,
                    task_owner_session_id TEXT NOT NULL,
                    child_session_id TEXT,
                    advice TEXT,
                    return_contract TEXT,
                    review_state TEXT NOT NULL,
                    budget_used INTEGER NOT NULL
                )
                """
            )

    def _persist(self, result: FrontierResult, connection: sqlite3.Connection | None = None) -> None:
        if self._database_path is None:
            return
        statement = """
                INSERT INTO frontier_requests(
                    request_id, project_id, mode, owner_session_id, task_owner_session_id,
                    child_session_id, advice, return_contract, review_state, budget_used
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(request_id) DO UPDATE SET review_state = excluded.review_state
                """
        values = (
            result.request_id, result.project_id, str(result.mode),
            result.owner_session_id, result.task_owner_session_id,
            result.child_session_id, result.advice, result.return_contract,
            result.review_state, result.budget_used,
        )
        if connection is not None:
            connection.execute(statement, values)
            return
        with self._connect() as own:
            own.execute(statement, values)

    def _load_requests(self) -> None:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT request_id, project_id, mode, owner_session_id, task_owner_session_id, "
                "child_session_id, advice, return_contract, review_state, budget_used "
                "FROM frontier_requests"
            ).fetchall()
        self._requests = {
            str(row[0]): FrontierResult(
                request_id=str(row[0]), project_id=str(row[1]), mode=FrontierMode(str(row[2])),
                owner_session_id=str(row[3]), task_owner_session_id=str(row[4]),
                child_session_id=None if row[5] is None else str(row[5]),
                advice=None if row[6] is None else str(row[6]),
                return_contract=None if row[7] is None else str(row[7]),
                review_state=str(row[8]), budget_used=int(row[9]),
            )
            for row in rows
        }
