from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from infra.memory import EvidenceItem, MemoryItem, MemoryStore
from infra.retrieval.memory import MemoryRetriever
from infra.retrieval.pipeline import RetrievalDocument, RetrievalPipeline


def test_retrieval_filters_scope_and_combines_bm25_temporal_confidence() -> None:
    now = datetime(2026, 10, 7, tzinfo=UTC)
    documents = [
        RetrievalDocument("old", "prj-1", "shared", "lease recovery procedure", now - timedelta(days=30), 0.9, ("evt-old",)),
        RetrievalDocument("new", "prj-1", "engineer", "lease recovery runbook", now - timedelta(hours=1), 0.8, ("evt-new",)),
        RetrievalDocument("pm", "prj-1", "pm", "lease recovery budget", now, 1.0, ("evt-pm",)),
        RetrievalDocument("other", "prj-2", "shared", "lease recovery", now, 1.0, ("evt-other",)),
        RetrievalDocument("future", "prj-1", "shared", "lease recovery future", now + timedelta(days=1), 1.0, ("evt-future",)),
    ]

    hits = RetrievalPipeline().search(
        query="lease recovery", project_id="prj-1", role_id="engineer",
        as_of=now, documents=documents, limit=10,
    )

    assert [hit.document.document_id for hit in hits] == ["new", "old"]
    assert hits[0].component_ranks.keys() == {"bm25", "temporal", "confidence"}
    assert hits[0].document.source_refs == ("evt-new",)
    assert hits[0].score > 0


def test_retrieval_is_deterministic_and_excludes_superseded_documents() -> None:
    now = datetime(2026, 10, 7, tzinfo=UTC)
    documents = [
        RetrievalDocument("b", "prj-1", "shared", "provider unknown reconciliation", now, 0.7, ("evt-b",)),
        RetrievalDocument("a", "prj-1", "shared", "provider unknown reconciliation", now, 0.7, ("evt-a",)),
        RetrievalDocument("gone", "prj-1", "shared", "provider unknown reconciliation", now, 1.0, ("evt-gone",), status="superseded"),
    ]
    pipeline = RetrievalPipeline()

    first = pipeline.search("provider unknown", "prj-1", "engineer", now, documents, 10)
    second = pipeline.search("provider unknown", "prj-1", "engineer", now, reversed(documents), 10)

    assert first == second
    assert [hit.document.document_id for hit in first] == ["a", "b"]


def test_memory_retriever_preserves_memory_provenance(tmp_path: Path) -> None:
    now = datetime(2026, 10, 7, tzinfo=UTC)
    store = MemoryStore(tmp_path / "infra.sqlite")
    store.put(
        MemoryItem("mem-1", "prj-1", "shared", "lesson", "provider unknown reconciliation", ("evt-1", "evt-2"), now, 0.9),
        (EvidenceItem("ev-1", "evt-1", "supports", "trace:1", 0.9),),
    )

    hits = MemoryRetriever(store).search(
        query="provider reconciliation", project_id="prj-1", role_id="engineer",
        as_of=now + timedelta(seconds=1), limit=5,
    )

    assert hits[0].document.document_id == "memory:mem-1"
    assert hits[0].document.source_refs == ("evt-1", "evt-2")
