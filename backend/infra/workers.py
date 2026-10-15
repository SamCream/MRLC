from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class WorkerEvent:
    event_type: str
    payload: dict[str, Any]


_WORKFLOW_EVENTS = frozenset({"REQUEST_ROLE", "REQUEST_EXPERT", "REQUEST_USER", "TASK_BLOCKED", "READY_FOR_REVIEW", "COMPLETE", "FAILED"})


def is_workflow_event(event: WorkerEvent) -> bool:
    return event.event_type in _WORKFLOW_EVENTS

