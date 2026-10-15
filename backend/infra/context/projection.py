from __future__ import annotations

from datetime import datetime

from infra.context.bundle import ContextAssembler, ContextBundle, ContextCandidate
from infra.memory import MemoryStore


class MemoryContextProjection:
    def __init__(self, memory: MemoryStore, assembler: ContextAssembler | None = None) -> None:
        self._memory = memory
        self._assembler = assembler or ContextAssembler()

    def build(
        self, *, project_id: str, profile_id: str, role_id: str,
        as_of: datetime, token_budget: int,
    ) -> ContextBundle:
        candidates = [
            ContextCandidate(
                source_ref=f"memory:{item.memory_id}",
                project_id=item.project_id,
                content=item.content,
                token_count=max(1, len(item.content.split())),
                priority=round(item.confidence * 100),
                occurred_at=item.valid_from,
                role_scopes=(item.role_scope,),
                sensitivity="project",
            )
            for item in self._memory.active(project_id, role_id, as_of)
        ]
        return self._assembler.build(
            project_id, profile_id, role_id, token_budget, candidates
        )
