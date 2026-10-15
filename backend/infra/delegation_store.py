from __future__ import annotations

import json
import time

SCHEMA = """
CREATE TABLE IF NOT EXISTS frontier_delegate_runs (
    request_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, actor_id TEXT NOT NULL,
    relation_id TEXT NOT NULL, parent_task_id TEXT NOT NULL, owner_session_id TEXT NOT NULL,
    child_session_id TEXT NOT NULL, child_task_id TEXT NOT NULL, capability TEXT NOT NULL,
    objective TEXT NOT NULL, acceptance_commands_json TEXT NOT NULL, budget INTEGER NOT NULL,
    permission_mode TEXT NOT NULL, disclosure_json TEXT NOT NULL, state TEXT NOT NULL,
    branch TEXT, worktree_path TEXT, base_commit TEXT, commit_sha TEXT, merge_sha TEXT, worker_handle TEXT,
    return_package_json TEXT, error TEXT, created_at REAL NOT NULL,
    started_at REAL, finished_at REAL
);
"""


class DelegationStore:
    """Infra-owned writer for Frontier DELEGATE execution state; callers pass their transaction."""

    @staticmethod
    def initialize(connection) -> None:
        connection.executescript(SCHEMA)

    @staticmethod
    def add(connection, *, request_id, project_id, actor_id, relation_id, parent_task_id,
            owner_session_id, child_session_id, child_task_id, capability, objective,
            acceptance_commands, budget, permission_mode, disclosure) -> None:
        connection.execute(
            "INSERT INTO frontier_delegate_runs(request_id,project_id,actor_id,relation_id,parent_task_id,"
            "owner_session_id,child_session_id,child_task_id,capability,objective,acceptance_commands_json,"
            "budget,permission_mode,disclosure_json,state,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,'QUEUED',?)",
            (request_id, project_id, actor_id, relation_id, parent_task_id, owner_session_id,
             child_session_id, child_task_id, capability, objective, json.dumps(list(acceptance_commands)),
             budget, permission_mode, json.dumps(disclosure), time.time()))

    @staticmethod
    def claim(connection, request_id) -> bool:
        return connection.execute(
            "UPDATE frontier_delegate_runs SET state='RUNNING',started_at=? WHERE request_id=? AND state='QUEUED'",
            (time.time(), request_id)).rowcount == 1

    @staticmethod
    def bind_workspace(connection, request_id, branch, worktree_path, base_commit, worker_handle=None) -> None:
        connection.execute(
            "UPDATE frontier_delegate_runs SET branch=?,worktree_path=?,base_commit=?,worker_handle=? "
            "WHERE request_id=? AND state='RUNNING'", (branch, worktree_path, base_commit, worker_handle, request_id))

    @staticmethod
    def mark_cancel_requested(connection, request_id) -> bool:
        return connection.execute(
            "UPDATE frontier_delegate_runs SET state='CANCEL_REQUESTED' WHERE request_id=? "
            "AND state IN ('QUEUED','RUNNING')", (request_id,)).rowcount == 1

    @staticmethod
    def current(connection, request_id):
        return connection.execute("SELECT * FROM frontier_delegate_runs WHERE request_id=?",
                                  (request_id,)).fetchone()

    @staticmethod
    def finish(connection, request_id, state, *, return_package=None, commit_sha=None, error=None) -> None:
        updated = connection.execute(
            "UPDATE frontier_delegate_runs SET state=?,return_package_json=?,commit_sha=?,error=?,finished_at=? "
            "WHERE request_id=? AND state IN ('RUNNING','CANCEL_REQUESTED')",
            (state, None if return_package is None else json.dumps(return_package, sort_keys=True),
             commit_sha, error, time.time(), request_id)).rowcount
        if updated != 1:
            raise ValueError("delegate run is not running")

    @staticmethod
    def review(connection, request_id, state, merge_sha=None) -> None:
        updated = connection.execute(
            "UPDATE frontier_delegate_runs SET state=?,merge_sha=? WHERE request_id=? AND state='WAIT_OWNER_REVIEW'",
            (state, merge_sha, request_id)).rowcount
        if updated != 1:
            raise ValueError("delegate run is not awaiting owner review")
        connection.execute("UPDATE frontier_requests SET review_state=? WHERE request_id=?", (state, request_id))

    @staticmethod
    def set_request_state(connection, request_id, state) -> None:
        connection.execute("UPDATE frontier_requests SET review_state=? WHERE request_id=?", (state, request_id))
