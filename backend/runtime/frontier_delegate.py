from __future__ import annotations

import json
import os
import subprocess
import threading
from pathlib import Path

from adapters.codex.adapter import CodexAdapter
from adapters.contract import TaskPackage, WorkerSessionSpec
from adapters.dsh.adapter import DshAdapter
from adapters.git import worktree as gitwt
from infra.worker_runtime_state import WorkerRunStore
from gateway.ccr.budget.meter import BudgetMeter
from infra.delegation_store import DelegationStore
from infra.endpoints import ccr_base_url
from infra.model_gateway import ModelGateway, ModelResult
from infra.runtime import Runtime

RETURN_CONTRACT_REF = "return-package-v1"


def delegate_worker_kind() -> str:
    kind = os.environ.get("MRLC_DELEGATE_WORKER", "codex")
    if kind not in {"codex", "dsh"}:
        raise ValueError("MRLC_DELEGATE_WORKER must be codex or dsh")
    return kind


def delegate_permission_mode() -> str:
    mode = os.environ.get("MRLC_DELEGATE_PERMISSION", "workspace-write")
    if mode not in {"workspace-write", "danger-full-access"}:
        raise ValueError("MRLC_DELEGATE_PERMISSION must be workspace-write or danger-full-access")
    return mode


class FrontierDelegateRunner:
    """Executes queued Frontier DELEGATE children in isolated worktrees; never merges.

    Integration into the project branch only happens through Parent Review (ConsoleCommands).
    """

    def __init__(self, model, adapter=None, *, verify_timeout=120, meter=None, worker_store=None):
        self.model = model
        self.root = (Path(model.database_path).parent / "projects").resolve()
        # The FastAPI integration injects the same WorkerRunStore used by InfraBridge.
        # Legacy callers may omit it, but no second store is created when an authoritative
        # store is already available.
        if worker_store is None:
            self.worker_store_path = Path(model.database_path).parent / "worker-runs.sqlite3"
            store = WorkerRunStore(self.worker_store_path)
        else:
            self.worker_store_path = Path(model.database_path)
            store = worker_store
        self.worker_kind = delegate_worker_kind()
        self.adapter = adapter or self._default_adapter(store)
        self.store = store
        self.model_binding = os.environ.get("MRLC_FRONTIER_MODEL_BINDING", "router-managed")
        self.gateway = ModelGateway({}, database_path=model.database_path)
        self.verify_timeout = verify_timeout
        # Hard budget: every Child model call passes this meter before reaching CCR (DEC-0161).
        self.meter = meter or BudgetMeter(ccr_base_url(), os.environ.get("CCR_API_KEY"))
        self.stop = threading.Event()
        self.thread = None
        with model.connect() as conn:
            DelegationStore.initialize(conn)

    def _default_adapter(self, store):
        timeout = int(os.environ.get("MRLC_DELEGATE_TIMEOUT_SECONDS", "900"))
        if self.worker_kind == "dsh":
            binary = (os.environ.get("MRLC_DELEGATE_DSH_BIN") or "").strip()
            if not binary:
                raise RuntimeError(
                    "MRLC_DELEGATE_DSH_BIN is required when MRLC_DELEGATE_WORKER=dsh; "
                    "the legacy repository-local Harness path is intentionally not assumed"
                )
            return DshAdapter(binary, ccr_base_url=ccr_base_url(), ccr_token=os.environ.get("CCR_API_KEY"),
                              state_store=store, timeout_seconds=timeout)
        return CodexAdapter(os.environ.get("MRLC_DELEGATE_CODEX_BIN", "codex"), state_store=store,
                            timeout_seconds=timeout)

    def repository(self, project_id: str) -> Path:
        return self.root / project_id / "repo"

    def start(self):
        self.thread = threading.Thread(target=self._loop, name="frontier-delegate", daemon=True)
        self.thread.start()

    def close(self):
        self.stop.set()
        if self.thread:
            self.thread.join(timeout=5)
        self.meter.close()

    def _loop(self):
        self.recover()
        while not self.stop.wait(.5):
            try:
                self.process_one()
            except Exception:
                self.stop.wait(1)

    def recover(self):
        # A restart mid-run must not re-launch the worker: its effects are unknown.
        with self.model.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            for run in list(conn.execute("SELECT * FROM frontier_delegate_runs WHERE state IN ('RUNNING','CANCEL_REQUESTED')")):
                self._fail(conn, run, "UNKNOWN",
                           "executor restarted mid-run; worktree preserved for inspection, not retried")

    def process_one(self) -> bool:
        with self.model.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            run = conn.execute("SELECT * FROM frontier_delegate_runs WHERE state='QUEUED' "
                               "ORDER BY created_at LIMIT 1").fetchone()
            if run is None or not DelegationStore.claim(conn, run["request_id"]):
                return False
            self._transition_task(conn,run,"RUNNABLE","RUNNING")
            DelegationStore.set_request_state(conn, run["request_id"], "CHILD_RUNNING")
            self._event(conn, run, "FRONTIER_DELEGATE_RUNNING", {})
        try:
            self._execute(dict(run))
        except Exception as error:
            with self.model.connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                current = conn.execute("SELECT * FROM frontier_delegate_runs WHERE request_id=?",
                                       (run["request_id"],)).fetchone()
                if current["state"] == "RUNNING":
                    self._fail(conn, current, "FAILED", "child execution failed: " + str(error)[:500])
                elif current["state"] == "CANCEL_REQUESTED":
                    self._fail(conn, current, "CANCELLED", "owner cancelled child")
        return True

    def _execute(self, run):
        repository = self.repository(run["project_id"])
        base = gitwt.ensure_repository(repository)
        branch = "frontier/" + run["request_id"]
        worktree = self.root / run["project_id"] / "worktrees" / run["request_id"]
        gitwt.add_worktree(repository, worktree, branch, base)
        with self.model.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._event(conn, run, "FRONTIER_DELEGATE_WORKSPACE_READY",
                        {"branch": branch, "base_commit": base})
        commands = tuple(json.loads(run["acceptance_commands_json"]))
        try:
            gateway_url = self.meter.open_run(int(run["budget"]))
        except ValueError as error:
            # Budget cannot be metered: fail before the Child starts, not after the call.
            with self.model.connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                self._fail(conn, self.run_row(run["request_id"]), "FAILED", f"budget meter unavailable: {error}")
            return
        try:
            self._run_worker(run, worktree, branch, base, commands, gateway_url)
        finally:
            self.meter.close_run(gateway_url)

    def _run_worker(self, run, worktree, branch, base, commands, gateway_url):
        spec = WorkerSessionSpec(
            project_id=run["project_id"], session_id=run["child_session_id"], task_id=run["child_task_id"],
            role_id="frontier.child", profile_id="frontier.delegate", cwd=worktree,
            model_binding=self.model_binding, context_ref=run["request_id"],
            permissions=(run["permission_mode"],), lease_token=1, event_cursor=0,
            output_contract_ref=RETURN_CONTRACT_REF, worktree_ref=branch, gateway_url=gateway_url)
        if getattr(self.adapter, "_state_store", None) is None:
            # Managed execution needs a durable handle so the Owner can cancel the process.
            self.adapter._state_store = self.store
        handle = self.adapter.start(spec, TaskPackage(run["objective"], commands))
        with self.model.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            current = DelegationStore.current(conn, run["request_id"])
            if current["state"] == "CANCEL_REQUESTED":
                self._apply_cancel(conn, current, handle)
                return
            DelegationStore.bind_workspace(conn, run["request_id"], branch, str(worktree), base, handle)
            self._event(conn, run, "FRONTIER_DELEGATE_WORKER_STARTED", {"handle": handle})
        try:
            result = self.adapter.wait(handle, timeout=getattr(self.adapter, "_timeout", 900))
        except TimeoutError:
            self.adapter.cancel(handle, reason="delegate wait timeout", fencing_token=1)
            raise
        with self.model.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            current = DelegationStore.current(conn, run["request_id"])
            if current["state"] == "CANCEL_REQUESTED" or result.return_package.status == "CANCELLED":
                self._apply_cancel(conn, current, handle)
                return
        package = result.return_package
        usage = self._usage(self.meter.usage(gateway_url), package.log_ref)
        # The Parent must not trust the child's self-report: re-run acceptance in the worktree.
        verification = [self._verify(worktree, command) for command in commands]
        commit = gitwt.commit_all(worktree, "feat: frontier delegate " + run["request_id"][-12:])
        files = gitwt.changed_files(worktree, base) if commit else []
        verified = all(item["passed"] for item in verification)
        ready = package.status == "READY_FOR_REVIEW" and verified and commit is not None
        reason = None if ready else ("budget exhausted: gateway rejected further model calls"
                                     if usage["rejected_calls"] and package.status != "READY_FOR_REVIEW"
                                     else "child did not reach READY_FOR_REVIEW" if package.status != "READY_FOR_REVIEW"
                                     else "independent acceptance verification failed" if not verified
                                     else "child produced no committed change")
        returned = {"return_contract": "artifact,tests,risks", "contract_ref": RETURN_CONTRACT_REF,
                    "status": package.status, "summary": package.summary[:4000],
                    "worker_tests": [{"command": t.command, "passed": t.passed} for t in package.tests],
                    "verification": verification, "branch": branch, "base_commit": base,
                    "commit": commit, "changed_files": files, "usage": usage,
                    "budget": run["budget"], "budget_exceeded": usage["rejected_calls"] > 0,
                    "log_ref": package.log_ref}
        with self.model.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self.gateway.record(ModelResult(
                run["project_id"], usage["model"], "DELEGATE", run["request_id"],
                None if ready else reason, "COMPLETED" if usage["observed"] else "UNKNOWN", None,
                usage["input"], usage["output"]), idempotency_key="delegate:" + run["request_id"],
                connection=conn)
            # Usage is recorded first: the calls were made even if the owner cancelled meanwhile.
            if DelegationStore.current(conn, run["request_id"])["state"] == "CANCEL_REQUESTED":
                self._apply_cancel(conn, run, run.get("worker_handle") or "")
                return
            if returned["budget_exceeded"]:
                # The meter refused calls before they reached the provider; the Child stopped early.
                self._event(conn, run, "FRONTIER_DELEGATE_BUDGET_EXCEEDED",
                            {"budget": run["budget"], "usage_total": usage["total"],
                             "rejected_calls": usage["rejected_calls"]})
            if not ready:
                self._fail(conn, run, "FAILED", reason, returned)
                return
            DelegationStore.finish(conn, run["request_id"], "WAIT_OWNER_REVIEW", return_package=returned,
                                   commit_sha=commit)
            DelegationStore.set_request_state(conn, run["request_id"], "WAIT_OWNER_REVIEW")
            Runtime.finish_in_connection(conn, run["relation_id"], "WAIT_OWNER_REVIEW",
                                         json.dumps(returned, sort_keys=True))
            self._transition_task(conn,run,"RUNNING","COMPLETED",evidence_refs=[f"git:{commit}"])
            self._event(conn, run, "FRONTIER_DELEGATE_READY_FOR_REVIEW",
                        {"branch": branch, "commit": commit, "changed_files": files,
                         "verification_passed": True, "relation_id": run["relation_id"],
                         "budget_exceeded": returned["budget_exceeded"]})

    def run_row(self, request_id: str):
        with self.model.connect() as conn:
            return conn.execute("SELECT * FROM frontier_delegate_runs WHERE request_id=?",
                                (request_id,)).fetchone()

    def _apply_cancel(self, conn, run, handle):
        try:
            self.adapter.cancel(handle, reason="owner cancelled child", fencing_token=1)
            outcome = "CANCELLED"
        except Exception:
            outcome = "UNKNOWN"
        DelegationStore.finish(conn, run["request_id"], outcome, error="owner cancelled child")
        DelegationStore.set_request_state(conn, run["request_id"], outcome)
        Runtime.finish_in_connection(conn, run["relation_id"], "FAILED",
                                     json.dumps({"error": "owner cancelled child", "cancel": outcome}, sort_keys=True))
        task = conn.execute("SELECT state FROM tasks WHERE task_id=?", (run["child_task_id"],)).fetchone()
        if task is not None and task[0] == "RUNNING":
            self._transition_task(conn,run,"RUNNING","FAILED")
        self._event(conn, run, "FRONTIER_DELEGATE_" + outcome, {"handle": handle})

    def _fail(self, conn, run, state, reason, returned=None):
        DelegationStore.finish(conn, run["request_id"], state, return_package=returned, error=reason)
        DelegationStore.set_request_state(conn, run["request_id"], state)
        Runtime.finish_in_connection(conn, run["relation_id"], "FAILED",
                                     json.dumps({"error": reason, **(returned or {})}, sort_keys=True))
        task = conn.execute("SELECT state FROM tasks WHERE task_id=?", (run["child_task_id"],)).fetchone()
        if task is not None and task[0] == "RUNNING":
            self._transition_task(conn,run,"RUNNING","FAILED")
        self._event(conn, run, "FRONTIER_DELEGATE_" + state, {"reason": reason})

    def _verify(self, worktree: Path, command: str) -> dict:
        try:
            completed = subprocess.run(command, shell=True, cwd=worktree, capture_output=True, text=True,
                                       timeout=self.verify_timeout)
            return {"command": command, "passed": completed.returncode == 0,
                    "output": (completed.stdout + completed.stderr)[-2000:]}
        except subprocess.TimeoutExpired:
            return {"command": command, "passed": False, "output": "verification timed out"}

    @staticmethod
    def _usage(metered: dict, log_ref: str) -> dict:
        """Usage as measured at the gateway meter; the Worker's own report is only a cross-check."""
        reported = {"input": 0, "output": 0, "observed": False}
        try:
            lines = Path(log_ref).read_text().splitlines()
        except OSError:
            lines = []
        for line in lines:
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict) and item.get("type") == "turn.completed" and isinstance(item.get("usage"), dict):
                reported["input"] += int(item["usage"].get("input_tokens") or 0)
                reported["output"] += int(item["usage"].get("output_tokens") or 0)
                reported["observed"] = True
        return {"input": metered["input"], "output": metered["output"], "total": metered["total"],
                "model": "router-managed", "observed": metered["observed"], "calls": metered["calls"],
                "unobserved_calls": metered["unobserved_calls"], "rejected_calls": metered["rejected_calls"],
                "source": "gateway-meter", "worker_reported": reported}

    def _transition_task(self,conn,run,expected,target,*,evidence_refs=None):
        self.model.infra.tasks.transition_in_connection(
            conn,run["child_task_id"],expected,target
        )
        self.model.infra.events.append_in_connection(conn,self.model.infra.event(
            run["project_id"],"TASK_STATE_CHANGED",
            {"from":expected,"to":target,"evidence_refs":list(evidence_refs or [])},
            actor_id="frontier.child",actor_type="system",
            session_id=run["child_session_id"],task_id=run["child_task_id"],
            idempotency_key=f"delegate:{run['request_id']}:task:{expected}:{target}",
        ))

    def _event(self, conn, run, kind, payload):
        self.model.infra.events.append_in_connection(conn, self.model.infra.event(
            run["project_id"], kind, {"request_id": run["request_id"], **payload},
            actor_id="frontier.child", actor_type="system", session_id=run["child_session_id"],
            task_id=run["child_task_id"], idempotency_key=f"delegate:{run['request_id']}:{kind}"))
        if kind=="FRONTIER_DELEGATE_READY_FOR_REVIEW":
            self.model.infra.events.append_in_connection(conn,self.model.infra.event(
                run["project_id"],"CHILD_COMPLETE",
                {"relation_id":run["relation_id"],
                 "result":json.dumps({"commit":payload.get("commit"),"branch":payload.get("branch")},sort_keys=True)},
                actor_id="frontier.child",actor_type="system",
                session_id=run["child_session_id"],task_id=run["child_task_id"],
                idempotency_key=f"delegate:{run['request_id']}:child-complete",
            ))
