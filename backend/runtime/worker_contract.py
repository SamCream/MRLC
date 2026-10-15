from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class WorkerSessionSpec:
    project_id: str
    session_id: str
    task_id: str
    role_id: str
    profile_id: str
    cwd: Path
    model_binding: str
    context_ref: str
    permissions: tuple[str, ...]
    lease_token: int
    event_cursor: int
    allowed_mcp: tuple[str, ...] = ()
    allowed_skills: tuple[str, ...] = ()
    environment_refs: tuple[str, ...] = ()
    output_contract_ref: str = "return-package-v1"
    worktree_ref: str | None = None
    gateway_url: str | None = None


@dataclass(frozen=True, slots=True)
class TaskPackage:
    objective: str
    acceptance_commands: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class StandardWorkerEvent:
    event_id: str
    sequence: int
    event_type: str
    payload: dict[str, Any]
    fencing_token: int


@dataclass(frozen=True, slots=True)
class TestResult:
    __test__ = False
    command: str
    passed: bool
    output: str = ""


@dataclass(frozen=True, slots=True)
class ReturnPackage:
    status: str
    summary: str
    changed_files: tuple[str, ...]
    tests: tuple[TestResult, ...]
    log_ref: str
    patch_refs: tuple[str, ...] = ()
    commit_refs: tuple[str, ...] = ()
    artifact_refs: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class AdapterResult:
    handle: str
    events: tuple[StandardWorkerEvent, ...]
    return_package: ReturnPackage


@dataclass(frozen=True, slots=True)
class AttachResult:
    handle: str
    status: str
    last_sequence: int
    fencing_token: int


@dataclass(frozen=True, slots=True)
class WorkerHealth:
    handle: str
    status: str
    alive: bool
    last_sequence: int


@dataclass(frozen=True, slots=True)
class ParkResult:
    disposition: str
    checkpoint_ref: str | None = None
    until_event: str | None = None
    reason: str | None = None
    estimated_duration_seconds: int | None = None


@dataclass(frozen=True, slots=True)
class CancelResult:
    status: str
    reason: str


@dataclass(frozen=True, slots=True)
class WorkerCapabilities:
    adapter: str
    permission_modes: tuple[str, ...]
    supports_resume: bool = True
    supports_park: bool = True
    supports_cancel: bool = True
    supports_reconcile: bool = True
    supports_workflow_events: bool = True


@dataclass(frozen=True, slots=True)
class DispatchReceipt:
    handle: str
    accepted: bool
    event_cursor: int


def worker_spec_to_dict(spec: WorkerSessionSpec) -> dict[str, Any]:
    return {
        "project_id": spec.project_id, "session_id": spec.session_id,
        "task_id": spec.task_id, "role_id": spec.role_id,
        "profile_id": spec.profile_id, "cwd": str(spec.cwd),
        "model_binding": spec.model_binding, "context_ref": spec.context_ref,
        "permissions": list(spec.permissions), "lease_token": spec.lease_token,
        "event_cursor": spec.event_cursor, "allowed_mcp": list(spec.allowed_mcp),
        "allowed_skills": list(spec.allowed_skills),
        "environment_refs": list(spec.environment_refs),
        "output_contract_ref": spec.output_contract_ref,
        "worktree_ref": spec.worktree_ref, "gateway_url": spec.gateway_url,
    }


def worker_spec_from_dict(value: dict[str, Any]) -> WorkerSessionSpec:
    return WorkerSessionSpec(
        project_id=str(value["project_id"]), session_id=str(value["session_id"]),
        task_id=str(value["task_id"]), role_id=str(value["role_id"]),
        profile_id=str(value["profile_id"]), cwd=Path(str(value["cwd"])),
        model_binding=str(value["model_binding"]), context_ref=str(value["context_ref"]),
        permissions=tuple(map(str, value.get("permissions", ()))),
        lease_token=int(value["lease_token"]), event_cursor=int(value.get("event_cursor", 0)),
        allowed_mcp=tuple(map(str, value.get("allowed_mcp", ()))),
        allowed_skills=tuple(map(str, value.get("allowed_skills", ()))),
        environment_refs=tuple(map(str, value.get("environment_refs", ()))),
        output_contract_ref=str(value.get("output_contract_ref", "return-package-v1")),
        worktree_ref=None if value.get("worktree_ref") is None else str(value["worktree_ref"]),
        gateway_url=None if value.get("gateway_url") is None else str(value["gateway_url"]),
    )
