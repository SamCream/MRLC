from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class AcceptanceContract:
    commands: tuple[str, ...]
    behaviors: tuple[str, ...]
    operational: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class CodingTask:
    task_id: str
    objective: str
    include_paths: tuple[str, ...]
    exclude_paths: tuple[str, ...]
    base_revision: str
    acceptance: AcceptanceContract
    owner_session_id: str
    target_refs: tuple[str, ...] = ()
    issue_refs: tuple[str, ...] = ()
    decision_refs: tuple[str, ...] = ()
    change_intent: str = ""
    risk: str = ""

    def __post_init__(self) -> None:
        if not self.objective.strip():
            raise ValueError("coding task objective is required")
        if not self.acceptance.commands and not self.acceptance.behaviors:
            raise ValueError("coding task acceptance is required")

    def validate_for_execution(self) -> None:
        if not (
            self.target_refs
            and self.issue_refs
            and self.decision_refs
            and self.change_intent.strip()
            and self.risk.strip()
            and self.acceptance.commands
            and self.acceptance.behaviors
            and self.acceptance.operational
        ):
            raise ValueError("coding task execution requires complete traceability")


@dataclass(frozen=True, slots=True)
class TestEvidence:
    __test__ = False

    command: str
    passed: bool
    output: str


@dataclass(frozen=True, slots=True)
class AcceptanceResult:
    accepted: bool
    provenance: str | None = None
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class HarnessEvent:
    event_type: str
    payload: dict[str, Any]


@dataclass(frozen=True, slots=True)
class DevEvent:
    event_type: str
    payload: dict[str, Any]


class DevAuthorityPolicy:
    _ALLOWED = {
        "pm": frozenset({"define_requirement", "functional_accept", "ask_user"}),
        "engineer": frozenset({"technical_plan", "technical_accept", "integrate"}),
        "functional_qa": frozenset({"functional_verify"}),
        "child": frozenset({"implement_scoped_task", "return_artifacts"}),
    }

    def authorize(self, role: str, action: str) -> bool:
        if action not in self._ALLOWED.get(role, frozenset()):
            raise PermissionError(f"{role} is not authorized for {action}")
        return True


class DevPlugin:
    def technical_accept(
        self, task: CodingTask, evidence: tuple[TestEvidence, ...], provenance: str
    ) -> AcceptanceResult:
        if not provenance.strip():
            raise ValueError("technical acceptance requires provenance")
        required = set(task.acceptance.commands)
        passed = {item.command for item in evidence if item.passed}
        missing = {
            command
            for command in required
            if not any(self._command_matches(command, observed) for observed in passed)
        }
        if missing:
            return AcceptanceResult(False, provenance, f"missing test evidence: {sorted(missing)}")
        return AcceptanceResult(True, provenance)

    @staticmethod
    def _command_matches(required: str, observed: str) -> bool:
        return required == observed or observed in required.split()

    def functional_accept(self, task: CodingTask, observed_behaviors: tuple[str, ...]) -> AcceptanceResult:
        observed = set(observed_behaviors)
        missing = set(task.acceptance.behaviors) - observed
        if missing:
            if not observed_behaviors:
                raise ValueError("functional acceptance requires behavior evidence")
            return AcceptanceResult(False, reason=f"missing behavior evidence: {sorted(missing)}")
        return AcceptanceResult(True)

    def normalize_harness_event(self, event: HarnessEvent) -> DevEvent:
        private_fields = {"codex_private", "dsh_private", "harness_metadata"}
        return DevEvent(event.event_type, {key: value for key, value in event.payload.items() if key not in private_fields})
