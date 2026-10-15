"""Read-only Evidence Graph: Requirement → Task → Delegation/Invocation → Artifact → Tests → Review → Commit.

Every node is read from an existing Infra record. A link that has no record is reported as
PENDING (the run has not reached that stage yet) or MISSING (it should exist but does not);
nothing is inferred from a later stage.
"""
from __future__ import annotations

import json

STAGES = ("requirement", "task", "delegation", "invocation", "artifact", "tests", "review", "commit")
IN_FLIGHT = {"QUEUED", "RUNNING", "CANCEL_REQUESTED"}
TERMINAL_WITHOUT_REVIEW = {"FAILED", "CANCELLED", "UNKNOWN"}


def _node(stage, status, ref=None, summary=None, source=None):
    return {"stage": stage, "status": status, "ref": ref, "summary": summary, "source": source}


def _absent(stage, run_state, reached_after):
    """PENDING while the run can still produce this stage; otherwise the record is MISSING."""
    if run_state in IN_FLIGHT or run_state in reached_after:
        return _node(stage, "PENDING")
    if run_state in TERMINAL_WITHOUT_REVIEW:
        return _node(stage, "NOT_REACHED", summary="run ended as " + run_state)
    return _node(stage, "MISSING")


def _review_events(connection, project_id):
    reviews = {}
    for payload, actor, occurred in connection.execute(
            "SELECT payload,actor_id,occurred_at FROM events WHERE project_id=? AND event_type='DELEGATION_REVIEWED' "
            "ORDER BY sequence", (project_id,)):
        body = json.loads(payload)
        if body.get("relation_id"):
            reviews[body["relation_id"]] = {"actor_id": actor, "occurred_at": occurred, "state": body.get("state"),
                                            "rationale": body.get("rationale")}
    return reviews


def _has_table(connection, name):
    return connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def evidence_chains(connection, project_id: str) -> dict:
    if not _has_table(connection, "frontier_delegate_runs"):
        return {"project_id": project_id, "chains": [], "stages": list(STAGES)}
    reviews = _review_events(connection, project_id)
    chains = []
    for run in connection.execute("SELECT * FROM frontier_delegate_runs WHERE project_id=? ORDER BY created_at",
                                  (project_id,)).fetchall():
        state = run["state"]
        package = json.loads(run["return_package_json"]) if run["return_package_json"] else None
        nodes = []
        parent = connection.execute("SELECT title,state FROM tasks WHERE task_id=?", (run["parent_task_id"],)).fetchone()
        nodes.append(_node("requirement", "PRESENT" if parent else "MISSING", run["parent_task_id"],
                           run["objective"] if parent is None else parent["title"] + " — " + run["objective"],
                           "tasks / frontier_delegate_runs.objective"))
        child = connection.execute("SELECT state FROM tasks WHERE task_id=?", (run["child_task_id"],)).fetchone()
        nodes.append(_node("task", "PRESENT" if child else "MISSING", run["child_task_id"],
                           None if child is None else "Task State " + child["state"], "tasks"))
        relation = connection.execute("SELECT state,return_contract FROM runtime_delegations WHERE relation_id=?",
                                      (run["relation_id"],)).fetchone()
        nodes.append(_node("delegation", "PRESENT" if relation else "MISSING", run["relation_id"],
                           None if relation is None else f"{relation['state']} · contract {relation['return_contract']}",
                           "runtime_delegations"))
        invocation = connection.execute(
            "SELECT status,usage_input,usage_output,model FROM gateway_invocations WHERE idempotency_key=?",
            ("delegate:" + run["request_id"],)).fetchone() if _has_table(connection, "gateway_invocations") else None
        if invocation:
            usage = ("usage UNKNOWN" if invocation["status"] == "UNKNOWN"
                     else f"usage {invocation['usage_input'] + invocation['usage_output']} tokens")
            nodes.append(_node("invocation", "PRESENT", "delegate:" + run["request_id"],
                               f"{invocation['status']} · {usage} · {invocation['model']}", "gateway_invocations"))
        else:
            nodes.append(_absent("invocation", state, set()))
        if run["commit_sha"]:
            files = (package or {}).get("changed_files") or []
            nodes.append(_node("artifact", "PRESENT", run["commit_sha"],
                               f"{run['branch']} · {len(files)} file(s): " + ", ".join(files[:10]),
                               "frontier_delegate_runs.commit_sha"))
        else:
            nodes.append(_absent("artifact", state, set()))
        verification = (package or {}).get("verification")
        if verification:
            passed = all(item.get("passed") for item in verification)
            nodes.append(_node("tests", "PRESENT" if passed else "FAILED", run["request_id"],
                               "Parent re-ran " + "; ".join(
                                   f"{item['command']} → {'PASS' if item.get('passed') else 'FAIL'}"
                                   for item in verification), "return_package.verification"))
        else:
            nodes.append(_absent("tests", state, set()))
        review = reviews.get(run["relation_id"])
        if review:
            nodes.append(_node("review", "PRESENT", run["relation_id"],
                               f"{review['state']} by {review['actor_id']}: {review['rationale'] or '—'}",
                               "events.DELEGATION_REVIEWED"))
        else:
            nodes.append(_absent("review", state, {"WAIT_OWNER_REVIEW"}))
        if run["merge_sha"]:
            nodes.append(_node("commit", "PRESENT", run["merge_sha"], "merged into main",
                               "frontier_delegate_runs.merge_sha"))
        elif state == "REWORK":
            nodes.append(_node("commit", "NOT_APPLICABLE", summary="Owner requested rework; main unchanged"))
        else:
            nodes.append(_absent("commit", state, {"WAIT_OWNER_REVIEW"}))
        statuses = {node["status"] for node in nodes}
        if "MISSING" in statuses:
            overall = "BROKEN"
        elif state in IN_FLIGHT or state == "WAIT_OWNER_REVIEW":
            overall = "IN_PROGRESS"
        elif state == "ACCEPTED" and statuses == {"PRESENT"}:
            overall = "COMPLETE"
        else:
            overall = state
        chains.append({"request_id": run["request_id"], "relation_id": run["relation_id"], "run_state": state,
                       "chain_state": overall, "nodes": nodes})
    return {"project_id": project_id, "chains": chains, "stages": list(STAGES)}
