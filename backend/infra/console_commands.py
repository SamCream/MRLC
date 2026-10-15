from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Callable

from infra.a2cm_plugin import A2CMPlugin
from infra.audit.policy import ActionRequest, AuditStore, PermissionDecision, RiskLevel
from infra.identity import new_id
from infra.frontier import FrontierAdvisorUnavailable, FrontierMode, FrontierPolicy, FrontierRequest, FrontierService
from infra.model_gateway import ModelGateway
from infra.project_bank import Decision
from infra.projects import Project
from infra.tasks import Task
from infra.runtime import Runtime
from infra.scheduler import Scheduler
from infra.sessions import Session
from infra.console_projection import ConsoleModel
from infra.console_projection import safe_value
from infra.console_operations import ConsoleOperations
from infra.delegation_store import DelegationStore
from infra.runtime import DelegationSpec
from adapters.git import worktree as gitwt
from runtime.conversation import ConversationService
from runtime.frontier_advisor import FrontierAdvisor
from runtime.frontier_delegate import FrontierDelegateRunner, delegate_permission_mode


class Conflict(ValueError):
    pass


class ConsoleCommands:
    """Transactional console commands using the authoritative domain registries."""

    AUTHORITIES = {"send_message": "conversation.send", "create_project": "project.create", "create_task": "task.create",
                   "answer_issue": "bank.decide", "create_session": "session.create",
                   "change_priority": "task.prioritize", "review_delegation": "delegation.review",
                   "export_audit": "audit.export", "export_evidence": "evidence.export",
                   "create_backup": "infra.backup", "restore_drill": "infra.restore",
                   "frontier_consult": "frontier.consult", "review_candidate": "belief.review",
                   "frontier_delegate": "frontier.delegate", "cancel_child": "task.cancel",
                   "configure_role": "role.configure", "grant_role": "role.grant"}
    PARAMETERS = {"send_message": {"content", "reply_to"}, "create_project": {"name"}, "create_task": {"title"},
                  "answer_issue": {"issue_id", "rationale"},
                  "create_session": {"application_id", "role_id"},
                  "change_priority": {"session_id", "priority", "rationale"},
                  "review_delegation": {"relation_id", "decision", "rationale"},
                  "export_audit": {"rationale"}, "export_evidence": {"rationale"},
                  "create_backup": {"target", "authorization_ref", "confirmation", "rationale"},
                  "restore_drill": {"target", "authorization_ref", "confirmation", "rationale"},
                  "frontier_consult": {"capability", "parent_task_id", "owner_session_id", "budget"},
                  "review_candidate": {"candidate_id", "decision", "rationale", "policy_version",
                                       "primary_session_id", "counterevidence", "uncertainty"},
                  "frontier_delegate": {"capability", "objective", "acceptance_commands", "owner_session_id",
                                        "budget", "target", "authorization_ref", "confirmation", "rationale"},
                  "cancel_child": {"request_id", "rationale"},
                  "configure_role": {"action", "role_id", "definition", "rationale"},
                  "grant_role": {"role_id", "authority", "rationale"}}

    def __init__(self, model: ConsoleModel):
        self.model = model
        self.audit = AuditStore(model.database_path)
        self.operations = ConsoleOperations(model)
        self.conversation = ConversationService(model)
        self.frontier = FrontierService(
            FrontierPolicy(allow_delegation=os.environ.get("MRLC_FRONTIER_ALLOW_DELEGATION", "false") == "true",
                           budget=int(os.environ.get("MRLC_FRONTIER_BUDGET", "1000"))),
            database_path=model.database_path,
            advisor=FrontierAdvisor(gateway=ModelGateway({}, database_path=model.database_path))
            if os.environ.get("CCR_API_KEY") else None)
        self.delegate = FrontierDelegateRunner(model) if self.frontier.policy.allow_delegation else None
        with model.connect() as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS console_operations ("
                         "actor_id TEXT NOT NULL, operation_id TEXT NOT NULL, project_id TEXT, "
                         "request_hash TEXT NOT NULL, receipt TEXT NOT NULL, "
                         "PRIMARY KEY(actor_id,operation_id))")

    def admin_status(self) -> dict[str, object]:
        """Real Profile/Policy/Worker facts for the admin view; nothing is inferred from defaults."""
        return {
            "policy": {"schema": "ui.v1",
                       "frontier_budget_limit": self.frontier.policy.budget,
                       "frontier_allow_delegation": self.frontier.policy.allow_delegation,
                       "frontier_max_depth": self.frontier.policy.max_depth},
            "worker": (None if self.delegate is None else
                       {"kind": self.delegate.worker_kind, "model_binding": self.delegate.model_binding,
                        "permission_mode": delegate_permission_mode()}),
        }

    def capabilities(self) -> dict[str, dict[str, object]]:
        """Report which console commands can actually execute right now."""
        advisor_bound = callable(getattr(self.frontier, "_advisor", None))
        consult = {"available": True, "risk": "R2", "scope": "project"} if advisor_bound else {
            "available": False, "risk": "R2",
            "reason": "frontier advisor not bound; set CCR_API_KEY to enable CONSULT"}
        if self.delegate is None:
            delegate = {"available": False, "risk": "R3", "reason": "MRLC_FRONTIER_ALLOW_DELEGATION is not enabled"}
        else:
            try:
                mode = delegate_permission_mode()
                delegate = {"available": True, "risk": "R3", "scope": "project", "permission_mode": mode,
                            "worker": self.delegate.worker_kind, "budget_limit": self.frontier.policy.budget}
            except ValueError as error:
                delegate = {"available": False, "risk": "R3", "reason": str(error)}
        return {
            "frontier_consult": consult,
            "frontier_delegate": delegate,
            "cancel_child": {"available": self.delegate is not None, "risk": "R2", "scope": "project"}
            if self.delegate is not None else {"available": False, "risk": "R2",
                                                "reason": "frontier delegation executor not running"},
        }

    def frontier_advisor(self, connection) -> Callable[[FrontierRequest], str] | None:
        """Bind the advisor to the caller's transaction so gateway usage is atomic."""
        advisor = self.frontier._advisor
        if isinstance(advisor, FrontierAdvisor):
            return advisor.bind(connection)
        return advisor

    def receipt(self, identity, operation_id):
        with self.model.connect() as conn:
            row = conn.execute("SELECT project_id,receipt FROM console_operations WHERE actor_id=? AND operation_id=?",
                               (identity["actor_id"], operation_id)).fetchone()
            if row is None:
                raise KeyError("operation not found")
            if row["project_id"] and "*" not in identity["projects"] and row["project_id"] not in identity["projects"]:
                raise PermissionError("project access denied")
            return json.loads(row["receipt"])

    @staticmethod
    def _delegated(required: str, authority) -> bool:
        """Whether a delegated authority may stand in for `required`.

        A role that holds `role.grant` is authorized to configure project roles (which itself
        includes creating them), so the future "a role creates another role" flow is expressed
        as the same `configure_role` command plus this delegation instead of a second schema.
        """
        delegated = {"role.configure": frozenset({"role.grant"})}
        return bool(delegated.get(required, frozenset()) & set(authority))

    def execute(self, identity, command):
        try:
            return self._execute(identity, command)
        except FrontierAdvisorUnavailable as error:
            entry = getattr(error, "ledger_entry", None)
            if entry is not None:
                error.ledger.record(entry)
            raise
        except PermissionError:
            project = command.get("project_id")
            # Never record inaccessible project IDs as authorized audit scope.
            if "*" not in identity["projects"] and project not in identity["projects"]:
                project = "workspace"
            request = ActionRequest(identity["actor_id"], "ui.v1", project or "workspace",
                                    str(command.get("kind", "unknown")), project or "workspace",
                                    "local", tuple(identity["authority"]), 0)
            risk = RiskLevel.R3 if command.get("kind") in {"create_backup", "restore_drill", "frontier_delegate"} else RiskLevel.R1
            self.audit.append(request, PermissionDecision(new_id("event"), risk, False,
                                                         "console permission denied", None))
            raise

    def _execute(self, identity, command):
        fields = {"operation_id", "expected_revision", "project_id", "kind", "parameters"}
        if not fields - {"project_id"} <= set(command) or not set(command) <= fields:
            raise ValueError("unexpected or missing command fields")
        operation_id = command.get("operation_id")
        if not isinstance(operation_id, str) or not 1 <= len(operation_id) <= 128:
            raise ValueError("operation_id required (1-128 characters)")
        kind = command.get("kind")
        if kind not in self.AUTHORITIES:
            raise ValueError("unsupported command")
        project_id = command.get("project_id")
        if project_id is not None and not isinstance(project_id, str):
            raise ValueError("invalid project_id")
        params = command.get("parameters")
        if not isinstance(params, dict):
            raise ValueError("parameters must be an object")
        if set(params) != self.PARAMETERS[kind]:
            raise ValueError("unexpected or missing command parameters")
        request_hash = hashlib.sha256(json.dumps(command, sort_keys=True).encode()).hexdigest()
        required = self.AUTHORITIES[kind]
        if required not in identity["authority"] and not self._delegated(required, identity["authority"]):
            raise PermissionError("missing console authority")
        if kind == "create_project" and "*" not in identity["projects"]:
            raise PermissionError("missing console authority")
        if kind in {"create_backup", "restore_drill"} and "*" not in identity["projects"]:
            raise PermissionError("whole-workspace operations authority required")
        with self.model.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            old = conn.execute("SELECT request_hash,receipt FROM console_operations WHERE actor_id=? AND operation_id=?",
                               (identity["actor_id"], operation_id)).fetchone()
            if old:
                if old["request_hash"] != request_hash:
                    raise Conflict("operation_id used with different parameters")
                # Scope is checked before returning any persisted result.
                saved = json.loads(old["receipt"])
                target = saved["project_id"]
                if "*" not in identity["projects"] and target not in identity["projects"]:
                    raise PermissionError("project access denied")
                return saved
            snapshot = self.model.snapshot(tuple(identity["projects"]), project_id, conn,
                                           capabilities=self.capabilities())
            if command.get("expected_revision") != snapshot["revision"]:
                raise Conflict("state changed; refresh and review your draft")
            if kind == "send_message":
                value = self.conversation.enqueue(conn, project_id, identity["actor_id"],
                                                  self._text(params, "content"), params["reply_to"])
                event_type = "PROJECT_MESSAGE_SENT"
            elif kind == "configure_role":
                if not project_id:
                    raise ValueError("project_id required")
                action = self._text(params, "action")
                if action not in {"create", "update", "disable"}:
                    raise ValueError("action must be create, update, or disable")
                definition = params["definition"]
                if not isinstance(definition, dict):
                    raise ValueError("definition must be an object")
                self._text(params, "rationale")
                value, event_type = self.conversation.configure_role(
                    conn, project_id, identity["actor_id"],
                    {"action": action, "role_id": self._text(params, "role_id"), "definition": definition})
            elif kind == "grant_role":
                if not project_id:
                    raise ValueError("project_id required")
                self._text(params, "rationale")
                authority = params["authority"]
                if not isinstance(authority, list) or not authority or any(
                        not isinstance(item, str) for item in authority):
                    raise ValueError("authority must be a non-empty list of strings")
                value, event_type = self.conversation.grant_role(
                    conn, project_id, identity["actor_id"],
                    {"role_id": self._text(params, "role_id"), "authority": authority})
            elif kind in {"create_backup", "restore_drill", "export_audit", "export_evidence"}:
                if not project_id:
                    raise ValueError("project_id required")
                self._text(params, "rationale")
                if kind in {"create_backup", "restore_drill"}:
                    value = ConsoleOperations.enqueue(conn, identity, command)
                elif kind == "export_audit":
                    value = {"format": "json", "records": safe_value([dict(row) for row in conn.execute(
                        "SELECT decision_id,actor_id,action,target,risk,result,reason,recorded_at "
                        "FROM permission_audit WHERE project_id=? ORDER BY sequence", (project_id,))])}
                else:
                    value = {"format": "json", "snapshot": snapshot}
                event_type = "CONSOLE_" + kind.upper()
            elif kind == "create_project":
                if project_id is not None:
                    raise ValueError("create_project requires workspace scope")
                name = self._text(params, "name")
                value = Project(new_id("project"), name)
                self.model.infra.projects.create_in_connection(conn, value)
                project_id = value.project_id
                event_type = "PROJECT_CREATED"
            elif kind == "create_task":
                if not project_id:
                    raise ValueError("project_id required")
                value = Task(new_id("task"), project_id, self._text(params, "title"), "CREATED")
                self.model.infra.tasks.create_in_connection(conn, value)
                event_type = "TASK_CREATED"
            elif kind == "create_session":
                if not project_id:
                    raise ValueError("project_id required")
                value = Session(new_id("session"), project_id,
                                self._text(params, "application_id"), self._text(params, "role_id"))
                self.model.infra.sessions.create_in_connection(conn, value)
                event_type = "SESSION_CREATED"
            elif kind == "change_priority":
                if not project_id:
                    raise ValueError("project_id required")
                value = Scheduler.adjust_priority_in_connection(
                    conn, project_id, self._text(params, "session_id"), params["priority"],
                    actor=identity["actor_id"], reason=self._text(params, "rationale"))
                event_type = "PRIORITY_CHANGED"
            elif kind == "review_delegation":
                if not project_id:
                    raise ValueError("project_id required")
                owner = conn.execute("SELECT owner_session_id FROM runtime_delegations WHERE relation_id=?",
                                     (self._text(params, "relation_id"),)).fetchone()
                if owner is None:
                    raise KeyError("unknown delegation")
                if owner[0] not in identity.get("sessions", []):
                    raise PermissionError("identity is not bound to the delegation owner")
                self._text(params, "rationale")
                value = Runtime.review_in_connection(conn, project_id, params["relation_id"],
                                                     owner[0], self._text(params, "decision"))
                value = asdict(value)
                run = conn.execute("SELECT * FROM frontier_delegate_runs WHERE relation_id=?",
                                   (params["relation_id"],)).fetchone() if conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE name='frontier_delegate_runs'").fetchone() else None
                if run is not None:
                    value["frontier"] = self._review_frontier_run(run, value["state"])
                    DelegationStore.review(conn, run["request_id"], value["frontier"]["state"],
                                           value["frontier"].get("merge_sha"))
                event_type = "DELEGATION_REVIEWED"
            elif kind == "frontier_consult":
                if not project_id:
                    raise ValueError("project_id required")
                if self.frontier.policy is None or not callable(getattr(self.frontier, "_advisor", None)):
                    raise Conflict("frontier advisor is not bound; consult was not executed")
                budget = params["budget"]
                if type(budget) is not int or budget < 1 or budget > self.frontier.policy.budget:
                    raise ValueError(f"budget must be an integer between 1 and {self.frontier.policy.budget}")
                owner_session_id = self._text(params, "owner_session_id")
                if owner_session_id not in identity.get("sessions", []):
                    raise PermissionError("identity is not bound to the owner session")
                value = self.model.infra.execute_frontier(self.frontier, FrontierRequest(
                    project_id, self._text(params, "parent_task_id"), owner_session_id,
                    self._text(params, "capability"), FrontierMode.CONSULT, budget),
                    actor_id=identity["actor_id"], connection=conn,
                    advisor=self.frontier_advisor(conn))
                event_type = "FRONTIER_CONSULT_RETURNED"
            elif kind == "frontier_delegate":
                value = self._frontier_delegate(conn, identity, command, project_id, params)
                event_type = "FRONTIER_DELEGATE_QUEUED"
            elif kind == "cancel_child":
                value = self._cancel_child(conn, identity, project_id, params)
                event_type = "FRONTIER_DELEGATE_CANCEL_REQUESTED"
            elif kind == "review_candidate":
                if not project_id:
                    raise ValueError("project_id required")
                primary = self._text(params, "primary_session_id")
                if primary not in identity.get("sessions", []):
                    raise PermissionError("identity is not bound to the A2CM Primary session")
                session = conn.execute("SELECT project_id,application_id,role_id,lifecycle FROM sessions "
                                       "WHERE session_id=?", (primary,)).fetchone()
                if session is None or tuple(session)[:3] != (project_id, "a2cm", "a2cm.primary"):
                    raise PermissionError("review requires this project's A2CM Primary session")
                if session["lifecycle"] != "ACTIVE":
                    raise Conflict("A2CM Primary session is not active")
                lists = []
                for name in ("counterevidence", "uncertainty"):
                    items = params[name]
                    if not isinstance(items, list) or len(items) > 20 or not all(
                            isinstance(item, str) and 0 < len(item.strip()) <= 1000 for item in items):
                        raise ValueError(f"{name} must be a list of up to 20 short strings")
                    lists.append(tuple(item.strip() for item in items))
                self._text(params, "rationale")
                value = A2CMPlugin.review_in_connection(
                    conn, project_id, self._text(params, "candidate_id"), self._text(params, "decision"),
                    policy_version=self._text(params, "policy_version"),
                    counterevidence=lists[0], uncertainty=lists[1])
                value["primary_session_id"] = primary
                event_type = "BELIEF_CANDIDATE_REVIEWED"
            else:
                if not project_id:
                    raise ValueError("project_id required")
                issue_id = self._text(params, "issue_id")
                issue = self.model.infra.bank._get_issue_in_connection(conn, issue_id)
                if issue.project_id != project_id:
                    raise PermissionError("issue crosses project boundary")
                if issue.status in {"RESOLVED", "WONT_FIX", "DUPLICATE"}:
                    raise Conflict("issue already resolved")
                if issue.decision_owner not in {identity["actor_id"], "user"}:
                    raise PermissionError("only the designated decision owner can answer")
                value = Decision(new_id("event"), project_id, issue.title, identity["actor_id"],
                                 "low", self._text(params, "rationale"))
                self.model.infra.bank.record_decision_in_connection(conn, value)
                self.model.infra.bank.resolve_issue_in_connection(conn, issue_id, value.decision_id)
                event_type = "ISSUE_RESOLVED"
            payload = value if isinstance(value, dict) else asdict(value)
            if "rationale" in params:
                payload["rationale"] = params["rationale"]
            if kind == "answer_issue":
                payload["issue_id"] = params["issue_id"]
            event_payload = {"kind": kind, "operation_id": operation_id} if kind.startswith("export_") else payload
            event = self.model.infra.events.append_in_connection(conn, self.model.infra.event(
                project_id, event_type, event_payload, actor_id=identity["actor_id"],
                actor_type="user", session_id=payload.get("session_id") if kind == "send_message" else None,
                task_id=payload.get("task_id") if kind == "send_message" else None,
                idempotency_key=f"ui:{identity['actor_id']}:{operation_id}"))
            decision_id = new_id("event")
            conn.execute("INSERT INTO permission_audit(decision_id,project_id,actor_id,action,target,risk,result,"
                         "reason,authorization_ref,request_json,recorded_at) VALUES(?,?,?,?,?,?,'ALLOWED',?,?,?,?)",
                         (decision_id, project_id, identity["actor_id"], kind, params.get("target", project_id),
                          3 if kind in {"create_backup", "restore_drill", "frontier_delegate"} else 2 if kind == "change_priority" else 1,
                          "console authority verified", params.get("authorization_ref"), json.dumps({"profile_id": "ui.v1"}),
                          datetime.now(timezone.utc).isoformat()))
            receipt = {"operation_id": operation_id, "project_id": project_id, "result": payload,
                       "status": "QUEUED" if kind in {"create_backup", "restore_drill"} else "COMMITTED",
                       "audit_ref": decision_id, "event_id": event.event_id}
            conn.execute("INSERT INTO console_operations VALUES(?,?,?,?,?)",
                         (identity["actor_id"], operation_id, project_id, request_hash, json.dumps(receipt)))
            return receipt

    def _frontier_delegate(self, conn, identity, command, project_id, params):
        if not project_id:
            raise ValueError("project_id required")
        if self.delegate is None:
            raise Conflict("frontier delegation is disabled; nothing was created")
        permission_mode = delegate_permission_mode()
        budget = params["budget"]
        if type(budget) is not int or budget < 1 or budget > self.frontier.policy.budget:
            raise ValueError(f"budget must be an integer between 1 and {self.frontier.policy.budget}")
        commands = params["acceptance_commands"]
        if not isinstance(commands, list) or not 1 <= len(commands) <= 5 or not all(
                isinstance(item, str) and 0 < len(item.strip()) <= 500 for item in commands):
            raise ValueError("acceptance_commands must list 1-5 commands of up to 500 characters")
        owner = self._text(params, "owner_session_id")
        if owner not in identity.get("sessions", []):
            raise PermissionError("identity is not bound to the owner session")
        session = conn.execute("SELECT project_id,lifecycle FROM sessions WHERE session_id=?", (owner,)).fetchone()
        if session is None or session["project_id"] != project_id:
            raise PermissionError("owner session must belong to this project")
        if session["lifecycle"] != "ACTIVE":
            raise Conflict("owner session is not active")
        parent = conn.execute("SELECT project_id FROM tasks WHERE task_id=?", (params["target"],)).fetchone()
        if parent is None or parent[0] != project_id:
            raise ValueError("parent task must belong to this project")
        objective = self._text(params, "objective")
        capability = self._text(params, "capability")
        self._text(params, "rationale")
        ConsoleOperations.consume_grant(conn, identity, command)
        child = Session(new_id("session"), project_id, "frontier", "frontier.child")
        self.model.infra.sessions.create_in_connection(conn, child)
        child_task = Task(new_id("task"), project_id, ("[DELEGATE] " + objective)[:120], "CREATED")
        self.model.infra.tasks.create_in_connection(conn, child_task)
        # Record task lineage: the Child Task points at the Parent Task that owns the delegation.
        self.model.infra.tasks.set_parent_in_connection(conn, child_task.task_id, params["target"])
        self.model.infra.tasks.transition_in_connection(conn, child_task.task_id, "CREATED", "RUNNABLE")
        result = self.model.infra.execute_frontier(self.frontier, FrontierRequest(
            project_id, params["target"], owner, capability, FrontierMode.DELEGATE, budget,
            child_session_id=child.session_id), actor_id=identity["actor_id"], connection=conn)
        relation = Runtime.delegate_in_connection(conn, DelegationSpec(
            project_id, params["target"], owner, child.session_id, "FRONTIER_DELEGATE",
            result.return_contract), new_id("event"))
        disclosure = {"sent_to_frontier": ["objective", "acceptance_commands", "isolated project worktree"],
                      "withheld": ["Infra database", "gateway credentials", "other projects",
                                   "A2CM memory and beliefs", "conversation history"]}
        DelegationStore.add(conn, request_id=result.request_id, project_id=project_id,
                            actor_id=identity["actor_id"], relation_id=relation.relation_id,
                            parent_task_id=params["target"], owner_session_id=owner,
                            child_session_id=child.session_id, child_task_id=child_task.task_id,
                            capability=capability, objective=objective,
                            acceptance_commands=[item.strip() for item in commands], budget=budget,
                            permission_mode=permission_mode, disclosure=disclosure)
        # Nothing is reviewable until the child returns; do not advertise WAIT_OWNER_REVIEW yet.
        DelegationStore.set_request_state(conn, result.request_id, "CHILD_QUEUED")
        return {"request_id": result.request_id, "relation_id": relation.relation_id,
                "child_session_id": child.session_id, "child_task_id": child_task.task_id,
                "return_contract": result.return_contract, "review_state": "CHILD_QUEUED",
                "permission_mode": permission_mode, "budget": budget, "disclosure": disclosure,
                "state": "QUEUED"}

    def _cancel_child(self, conn, identity, project_id, params):
        if not project_id:
            raise ValueError("project_id required")
        self._text(params, "rationale")
        run = DelegationStore.current(conn, self._text(params, "request_id"))
        if run is None or run["project_id"] != project_id:
            raise KeyError("unknown delegate run in this project")
        if run["owner_session_id"] not in identity.get("sessions", []):
            raise PermissionError("identity is not bound to the delegation owner")
        if run["state"] == "QUEUED":
            # No worker exists yet: cancellation is immediate and certain.
            conn.execute("UPDATE frontier_delegate_runs SET state='RUNNING' WHERE request_id=?", (run["request_id"],))
            DelegationStore.finish(conn, run["request_id"], "CANCELLED", error="owner cancelled before start")
            DelegationStore.set_request_state(conn, run["request_id"], "CANCELLED")
            Runtime.finish_in_connection(conn, run["relation_id"], "FAILED",
                                         json.dumps({"error": "owner cancelled before start"}))
            self.model.infra.tasks.transition_in_connection(conn, run["child_task_id"], "RUNNABLE", "FAILED")
            return {"request_id": run["request_id"], "state": "CANCELLED"}
        if run["state"] != "RUNNING" or not DelegationStore.mark_cancel_requested(conn, run["request_id"]):
            raise Conflict("child is not running; nothing to cancel")
        signalled = None
        if run["worker_handle"] and self.delegate is not None:
            try:
                signalled = self.delegate.adapter.cancel(run["worker_handle"], reason="owner cancelled child",
                                                         fencing_token=1).status
            except Exception:
                signalled = "UNKNOWN"
        # The executor confirms the terminal state; an HTTP success only means "requested".
        return {"request_id": run["request_id"], "state": "CANCEL_REQUESTED", "worker_signal": signalled}

    def _review_frontier_run(self, run, decision_state):
        if run["state"] != "WAIT_OWNER_REVIEW":
            raise Conflict("frontier child has not returned for review")
        if decision_state != "ACCEPTED":
            return {"request_id": run["request_id"], "state": "REWORK", "branch": run["branch"]}
        repository = self.delegate.repository(run["project_id"]) if self.delegate else None
        if repository is None or not run["branch"]:
            raise Conflict("frontier delegation binding unavailable; review was not applied")
        # Last effect inside the transaction: a failed merge aborts the whole review.
        merge = gitwt.merge_reviewed(repository, run["branch"], "main",
                                     "merge: reviewed frontier " + run["request_id"][-12:])
        return {"request_id": run["request_id"], "state": "ACCEPTED", "branch": run["branch"],
                "merge_sha": merge, "commit": run["commit_sha"]}

    @staticmethod
    def _text(params, key):
        text = params.get(key)
        if not isinstance(text, str) or not text.strip() or len(text) > 4000:
            raise ValueError(f"{key} must contain 1-4000 characters")
        return text.strip()
