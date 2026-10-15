from __future__ import annotations

from dataclasses import dataclass, field

from infra.events import Event


class ReplayInvariantError(ValueError):
    """Raised when an event stream cannot produce a valid authoritative state."""


@dataclass(frozen=True, slots=True)
class ReplayState:
    projects: dict[str, str]
    sessions: dict[str, str]
    tasks: dict[str, str]
    dependencies: tuple[tuple[str, str], ...]
    delegations: dict[str, str] = field(default_factory=dict)
    leases: dict[str, int] = field(default_factory=dict)
    issues: dict[str, str] = field(default_factory=dict)
    decisions: dict[str, str | None] = field(default_factory=dict)
    artifacts: dict[str, str] = field(default_factory=dict)
    memories: dict[str, str] = field(default_factory=dict)
    beliefs: dict[str, str] = field(default_factory=dict)


def replay_project(events: list[Event]) -> ReplayState:
    state = _project_events(events)
    return validate_replay_invariants(events, state)


def _project_events(events: list[Event]) -> ReplayState:
    projects: dict[str, str] = {}
    sessions: dict[str, str] = {}
    tasks: dict[str, str] = {}
    dependencies: set[tuple[str, str]] = set()
    delegations: dict[str, str] = {}
    leases: dict[str, int] = {}
    issues: dict[str, str] = {}
    decisions: dict[str, str | None] = {}
    artifacts: dict[str, str] = {}
    memories: dict[str, str] = {}
    beliefs: dict[str, str] = {}
    for event in sorted(events, key=lambda item: (item.sequence, item.occurred_at, item.event_id)):
        payload = event.payload
        if event.event_type == "PROJECT_CREATED":
            projects[str(payload["project_id"])] = str(payload["name"])
        elif event.event_type == "SESSION_CREATED":
            sessions[str(payload["session_id"])] = str(payload["role_id"])
        elif event.event_type == "TASK_CREATED":
            tasks[str(payload["task_id"])] = "CREATED"
        elif event.event_type == "TASK_STATE_CHANGED":
            if event.task_id is None:
                raise ValueError("task state event has no task_id")
            tasks[event.task_id] = str(payload["to"])
        elif event.event_type == "TASK_DEPENDENCY_ADDED":
            dependencies.add((str(payload["task_id"]), str(payload["dependency_task_id"])))
        elif event.event_type == "DELEGATION_CREATED":
            delegations[str(payload["relation_id"])] = "OPEN"
        elif event.event_type == "CHILD_COMPLETE":
            delegations[str(payload["relation_id"])] = "WAIT_OWNER_REVIEW"
        elif event.event_type == "REVIEW_ACCEPTED":
            delegations[str(payload["relation_id"])] = "ACCEPTED"
        elif event.event_type == "REVIEW_REWORK":
            delegations[str(payload["relation_id"])] = "REWORK"
        elif event.event_type == "DELEGATION_FAILED":
            delegations[str(payload["relation_id"])] = "FAILED"
        elif event.event_type == "LEASE_ACQUIRED":
            leases[str(payload["resource_id"])] = int(payload["fencing_token"])
        elif event.event_type == "LEASE_EXPIRED":
            leases.pop(str(payload["resource_id"]), None)
        elif event.event_type == "ISSUE_CREATED":
            issues[str(payload["issue_id"])] = "OPEN"
        elif event.event_type == "ISSUE_RESOLVED":
            issues[str(payload["issue_id"])] = "RESOLVED"
        elif event.event_type == "DECISION_RECORDED":
            decisions[str(payload["decision_id"])] = payload.get("supersedes")
        elif event.event_type == "ARTIFACT_RECORDED":
            artifacts[str(payload["artifact_id"])] = str(payload["content_hash"])
        elif event.event_type == "MEMORY_RECORDED":
            memories[str(payload["memory_id"])] = str(payload.get("status", "ACTIVE")).upper()
        elif event.event_type == "MEMORY_SUPERSEDED":
            memories[str(payload["memory_id"])] = "SUPERSEDED"
        elif event.event_type == "BELIEF_CANDIDATE_CREATED":
            beliefs[str(payload["candidate_id"])] = str(payload.get("status","CANDIDATE"))
        elif event.event_type == "BELIEF_CANDIDATE_REVIEWED":
            beliefs[str(payload["candidate_id"])] = str(payload["status"])
    return ReplayState(
        projects=projects,
        sessions=sessions,
        tasks=tasks,
        dependencies=tuple(sorted(dependencies)),
        delegations=delegations,
        leases=leases,
        issues=issues,
        decisions=decisions,
        artifacts=artifacts,
        memories=memories,
        beliefs=beliefs,
    )


def validate_replay_invariants(events: list[Event], state: ReplayState | None = None) -> ReplayState:
    """Validate the cross-aggregate invariants required for deterministic replay."""
    ordered = sorted(events, key=lambda item: (item.sequence, item.occurred_at, item.event_id))
    if not ordered:
        return state or replay_project([])

    project_ids = {event.project_id for event in ordered}
    if len(project_ids) != 1:
        raise ReplayInvariantError("replay input contains multiple projects")

    replayed = state or _project_events(ordered)
    delegations: dict[str, dict[str, object]] = {}
    completed_relations: set[str] = set()
    lease_tokens: dict[str, int] = {}
    task_evidence: dict[str, bool] = {}

    for event in ordered:
        payload = event.payload
        if event.event_type == "TASK_CREATED":
            task_id = str(payload.get("task_id", ""))
            if not task_id:
                raise ReplayInvariantError("TASK_CREATED requires task_id")
            task_evidence[task_id] = False
        elif event.event_type == "TASK_STATE_CHANGED":
            task_id = event.task_id or str(payload.get("task_id", ""))
            if not task_id:
                raise ReplayInvariantError("TASK_STATE_CHANGED requires task_id")
            if str(payload.get("to")) == "COMPLETED":
                evidence_refs = payload.get("evidence_refs", ())
                if not isinstance(evidence_refs, list) or not evidence_refs:
                    raise ReplayInvariantError(
                        f"completed task lacks evidence references: {task_id}"
                    )
                task_evidence[task_id] = True
        elif event.event_type == "DELEGATION_CREATED":
            relation_id = str(payload.get("relation_id", ""))
            required = ("parent_task_id", "child_session_id", "return_contract")
            if not relation_id or any(not str(payload.get(key, "")).strip() for key in required):
                raise ReplayInvariantError(
                    "DELEGATION_CREATED requires parent task, child session, and return contract"
                )
            delegations[relation_id] = payload
        elif event.event_type == "CHILD_COMPLETE":
            relation_id = str(payload.get("relation_id", ""))
            if relation_id not in delegations:
                raise ReplayInvariantError(f"CHILD_COMPLETE references unknown delegation: {relation_id}")
            if not str(payload.get("result", "")).strip():
                raise ReplayInvariantError(f"child completion lacks result: {relation_id}")
            completed_relations.add(relation_id)
        elif event.event_type == "REVIEW_ACCEPTED":
            relation_id = str(payload.get("relation_id", ""))
            if relation_id not in completed_relations:
                raise ReplayInvariantError(
                    f"delegation review occurred before child completion: {relation_id}"
                )
        elif event.event_type == "LEASE_ACQUIRED":
            resource_id = str(payload.get("resource_id", ""))
            fencing_token = int(payload.get("fencing_token", 0))
            if not resource_id or fencing_token <= 0:
                raise ReplayInvariantError("LEASE_ACQUIRED requires a positive fencing token")
            previous = lease_tokens.get(resource_id, 0)
            if fencing_token <= previous:
                raise ReplayInvariantError(f"fencing token did not increase: {resource_id}")
            lease_tokens[resource_id] = fencing_token

    if any(task_id in replayed.tasks and status == "COMPLETED" and not task_evidence.get(task_id, False)
           for task_id, status in replayed.tasks.items()):
        raise ReplayInvariantError("completed task has no completion evidence")
    return replayed
