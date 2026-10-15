from __future__ import annotations

import json
import secrets
import sqlite3
import threading
import time
from pathlib import Path

from infra.identity import new_id
from infra.persistence.backup import create_backup, restore_backup


class ConsoleOperations:
    """Durable local operations; a restore always creates a separate instance."""

    def __init__(self, model, root=None):
        self.model = model
        self.root = Path(root or Path(model.database_path).parent / "operations").resolve()
        self.stop = threading.Event()
        self.thread = None
        with model.connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS console_grants (
                    grant_id TEXT PRIMARY KEY, actor_id TEXT NOT NULL, project_id TEXT NOT NULL,
                    kind TEXT NOT NULL, target TEXT NOT NULL, revision TEXT NOT NULL,
                    expires_at REAL NOT NULL, used INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS console_jobs (
                    job_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, actor_id TEXT NOT NULL,
                    operation_id TEXT NOT NULL, kind TEXT NOT NULL, target TEXT NOT NULL,
                    state TEXT NOT NULL, result TEXT, authorization_ref TEXT NOT NULL
                );
            """)

    def challenge(self, identity, body):
        if set(body) != {"project_id", "kind", "target", "expected_revision"}:
            raise ValueError("invalid authorization request")
        kind = body["kind"]
        if kind == "frontier_delegate":
            return self._delegate_challenge(identity, body)
        if kind not in {"create_backup", "restore_drill"}:
            raise ValueError("unsupported authorization action")
        required = {"create_backup": "infra.backup", "restore_drill": "infra.restore"}[kind]
        if "*" not in identity["projects"] or required not in identity["authority"]:
            raise PermissionError("whole-workspace operations authority required")
        project = body["project_id"]
        target = body["target"]
        if kind == "create_backup" and target != "infra":
            raise ValueError("backup target must be infra")
        if kind == "restore_drill":
            with self.model.connect() as conn:
                row = conn.execute("SELECT project_id,state,kind FROM console_jobs WHERE job_id=?", (target,)).fetchone()
            if row is None or tuple(row) != (project, "SUCCEEDED", "create_backup"):
                raise ValueError("target must be a completed backup in this project")
        snapshot = self.model.snapshot(tuple(identity["projects"]), project)
        if body["expected_revision"] != snapshot["revision"]:
            from infra.console_commands import Conflict
            raise Conflict("state changed before authorization")
        grant = secrets.token_urlsafe(32)
        expiry = time.time() + 300
        with self.model.connect() as conn:
            conn.execute("INSERT INTO console_grants VALUES(?,?,?,?,?,?,?,0)", (
                grant, identity["actor_id"], project, kind, target, snapshot["revision"], expiry))
        return {"authorization_ref": grant, "risk": "R3", "expires_at": expiry,
                "action": kind, "target": target, "project_id": project,
                "scope": "whole Infra database; all projects and private domain data",
                "effect": "new local backup" if kind == "create_backup" else "restore to NEW instance; active database unchanged",
                "confirmation": target}

    def _delegate_challenge(self, identity, body):
        project = body["project_id"]
        target = body["target"]
        if "frontier.delegate" not in identity["authority"]:
            raise PermissionError("missing authority: frontier.delegate")
        if not isinstance(project, str) or ("*" not in identity["projects"] and project not in identity["projects"]):
            raise PermissionError("project access denied")
        with self.model.connect() as conn:
            task = conn.execute("SELECT project_id FROM tasks WHERE task_id=?", (target,)).fetchone()
        if task is None or task[0] != project:
            raise ValueError("target must be a parent task in this project")
        snapshot = self.model.snapshot(tuple(identity["projects"]), project)
        if body["expected_revision"] != snapshot["revision"]:
            from infra.console_commands import Conflict
            raise Conflict("state changed before authorization")
        grant = secrets.token_urlsafe(32)
        expiry = time.time() + 300
        with self.model.connect() as conn:
            conn.execute("INSERT INTO console_grants VALUES(?,?,?,?,?,?,?,0)", (
                grant, identity["actor_id"], project, "frontier_delegate", target, snapshot["revision"], expiry))
        return {"authorization_ref": grant, "risk": "R3", "expires_at": expiry,
                "action": "frontier_delegate", "target": target, "project_id": project,
                "scope": "one managed Child in an isolated worktree/branch of this project's repository",
                "effect": "runs a Frontier worker and the stated acceptance commands on this host; "
                          "nothing merges without Parent Review",
                "confirmation": target}

    @staticmethod
    def consume_grant(conn, identity, command):
        params = command["parameters"]
        row = conn.execute("SELECT * FROM console_grants WHERE grant_id=?", (params["authorization_ref"],)).fetchone()
        if row is None or row["used"] or row["expires_at"] <= time.time() or any((
            row["actor_id"] != identity["actor_id"], row["project_id"] != command["project_id"],
            row["kind"] != command["kind"], row["target"] != params["target"],
            row["revision"] != command["expected_revision"], params["confirmation"] != row["target"],
        )):
            raise PermissionError("expired, mismatched or unconfirmed authorization")
        conn.execute("UPDATE console_grants SET used=1 WHERE grant_id=?", (row["grant_id"],))
        return row

    @staticmethod
    def enqueue(conn, identity, command):
        params = command["parameters"]
        row = ConsoleOperations.consume_grant(conn, identity, command)
        job_id = new_id("event")
        conn.execute("INSERT INTO console_jobs VALUES(?,?,?,?,?,?,'QUEUED',NULL,?)", (
            job_id, command["project_id"], identity["actor_id"], command["operation_id"],
            command["kind"], params["target"], row["grant_id"]))
        return {"job_id": job_id, "state": "QUEUED", "target": params["target"]}

    def start(self):
        self.thread = threading.Thread(target=self.run, name="console-operations", daemon=True)
        self.thread.start()

    def close(self):
        self.stop.set()
        if self.thread:
            self.thread.join(timeout=30)

    def run(self):
        # A crash during a filesystem effect is not permission to repeat it.
        with self.model.connect() as conn:
            interrupted = [dict(row) for row in conn.execute("SELECT * FROM console_jobs WHERE state='RUNNING'")]
        for job in interrupted:
            self.finish(job, "UNKNOWN", {"reason": "executor restarted; inspect saved manifest before retrying"})
        while not self.stop.wait(.2):
            with self.model.connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute("SELECT * FROM console_jobs WHERE state='QUEUED' ORDER BY rowid LIMIT 1").fetchone()
                if row is None:
                    continue
                job = dict(row)
                conn.execute("UPDATE console_jobs SET state='RUNNING' WHERE job_id=?", (job["job_id"],))
            try:
                destination = self.root / job["job_id"]
                self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
                self.root.chmod(0o700)
                if job["kind"] == "create_backup":
                    create_backup(destination, databases={"infra": Path(self.model.database_path)}, artifact_dirs={})
                    manifest = destination / "manifest.json"
                else:
                    restored = restore_backup(self.root / job["target"], destination)
                    manifest = restored.manifest_path
                    with sqlite3.connect(destination / "databases" / "infra.sqlite") as conn:
                        if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                            raise ValueError("restored database integrity failure")
                self.finish(job, "SUCCEEDED", {"manifest": str(manifest.relative_to(self.root)),
                                               "active_database_unchanged": True})
            except Exception:
                self.finish(job, "FAILED", {"reason": "local operation failed; inspect operation directory and runbook"})

    def finish(self, job, state, result):
        with self.model.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("UPDATE console_jobs SET state=?,result=? WHERE job_id=?", (state, json.dumps(result), job["job_id"]))
            event = self.model.infra.events.append_in_connection(conn, self.model.infra.event(
                job["project_id"], "CONSOLE_OPERATION_" + state,
                {"job_id": job["job_id"], "kind": job["kind"], "result": result},
                actor_id=job["actor_id"], actor_type="system",
                idempotency_key="console-job:" + job["job_id"] + ":" + state))
            receipt = json.loads(conn.execute("SELECT receipt FROM console_operations WHERE actor_id=? AND operation_id=?",
                                             (job["actor_id"], job["operation_id"])).fetchone()[0])
            receipt.update(status=state, result={"job_id": job["job_id"], "state": state, **result}, event_id=event.event_id)
            conn.execute("UPDATE console_operations SET receipt=? WHERE actor_id=? AND operation_id=?", (
                json.dumps(receipt), job["actor_id"], job["operation_id"]))
