from __future__ import annotations

from datetime import datetime

from infra.memory import MemoryStore
from infra.retrieval.pipeline import RetrievalDocument, RetrievalHit, RetrievalPipeline


class MemoryRetriever:
    def __init__(self, memory: MemoryStore, pipeline: RetrievalPipeline | None = None) -> None:
        self._memory = memory
        self._pipeline = pipeline or RetrievalPipeline()

    def search(
        self, *, query: str, project_id: str, role_id: str,
        as_of: datetime, limit: int,
    ) -> list[RetrievalHit]:
        documents = [
            RetrievalDocument(
                document_id=f"memory:{item.memory_id}",
                project_id=item.project_id,
                role_scope=item.role_scope,
                content=item.content,
                occurred_at=item.valid_from,
                confidence=item.confidence,
                source_refs=item.source_event_refs,
                status=item.status,
                valid_to=item.valid_to,
            )
            for item in self._memory.active(project_id, role_id, as_of)
        ]
        return self._pipeline.search(
            query, project_id, role_id, as_of, documents, limit
        )
