from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from infra.continuity import ContinuityCheckpoint, ContinuityStore
from infra.sessions import Session, SessionRegistry, SessionSpec


@dataclass(frozen=True, slots=True)
class RolloverResult:
    archived_session: Session
    new_session: Session
    checkpoint: ContinuityCheckpoint


class RuntimeRolloverService:
    def __init__(
        self, sessions: SessionRegistry, continuity: ContinuityStore
    ) -> None:
        self._sessions = sessions
        self._continuity = continuity

    def rollover(
        self,
        session_id: str,
        *,
        profile_version: str,
        policy_version: str,
        context_delta: dict[str, Any],
        project_directory: str,
        client: str,
        completed: tuple[str, ...] = (),
        blockers: tuple[str, ...] = (),
        next_steps: tuple[str, ...] = (),
        modified_files: tuple[str, ...] = (),
    ) -> RolloverResult:
        original = self._sessions.get(session_id)
        snapshot = {
            "rollover_from": original.session_id,
            "profile_version": profile_version,
            "policy_version": policy_version,
            "context_delta": context_delta,
        }
        self._sessions.snapshot(original.session_id, snapshot)
        checkpoint = self._continuity.create(
            project_id=original.project_id,
            session_id=original.session_id,
            project_directory=project_directory,
            client=client,
            git_branch=None,
            git_head_sha=None,
            git_dirty=bool(modified_files),
            objective=(str(context_delta.get("task_id")) if context_delta.get("task_id") else None),
            completed=completed,
            blockers=blockers,
            next_steps=next_steps,
            modified_files=modified_files,
            verification_status="ROLLOVER_READY",
            created_at=datetime.now(UTC),
        )
        archived = self._sessions.archive(
            original.session_id, expected_version=original.version
        )
        replacement = self._sessions.create(
            SessionSpec(
                project_id=original.project_id,
                application_id=original.application_id,
                role_id=original.role_id,
            )
        )
        if original.runtime_adapter and original.model_binding:
            replacement = self._sessions.bind_runtime(
                replacement.session_id, original.runtime_adapter, original.model_binding
            )
        replacement = self._sessions.snapshot(replacement.session_id, snapshot)
        if (
            replacement.project_id != original.project_id
            or replacement.role_id != original.role_id
            or replacement.application_id != original.application_id
        ):
            raise RuntimeError("rollover continuity check failed")
        return RolloverResult(archived, replacement, checkpoint)
