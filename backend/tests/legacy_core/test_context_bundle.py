from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from infra.context.bundle import ContextAssembler, ContextCandidate
from infra.context.projection import MemoryContextProjection
from infra.memory import EvidenceItem, MemoryItem, MemoryStore


def test_context_bundle_filters_projects_roles_deduplicates_and_budgets() -> None:
    now = datetime(2026, 10, 7, tzinfo=UTC)
    candidates = [
        ContextCandidate("src-1", "prj-1", "shared fact", 3, 90, now, ("engineer",)),
        ContextCandidate("src-2", "prj-1", " shared   fact ", 3, 80, now + timedelta(seconds=1), ("engineer",)),
        ContextCandidate("src-3", "prj-1", "important decision", 4, 100, now, ("engineer",)),
        ContextCandidate("src-4", "prj-2", "other project", 1, 100, now, ("engineer",)),
        ContextCandidate("src-5", "prj-1", "pm private", 1, 100, now, ("pm",)),
    ]

    bundle = ContextAssembler().build(
        project_id="prj-1", profile_id="dev", role_id="engineer",
        token_budget=7, candidates=candidates,
    )

    assert [item.source_ref for item in bundle.items] == ["src-3", "src-1"]
    assert bundle.total_tokens == 7
    assert bundle.manifest.excluded == {
        "src-2": "duplicate_content",
        "src-4": "project_denied",
        "src-5": "role_denied",
    }


def test_context_bundle_rebuild_is_deterministic() -> None:
    now = datetime(2026, 10, 7, tzinfo=UTC)
    candidates = [
        ContextCandidate("b", "prj-1", "second", 2, 10, now, ("shared",)),
        ContextCandidate("a", "prj-1", "first", 2, 10, now, ("shared",)),
    ]
    assembler = ContextAssembler()

    first = assembler.build("prj-1", "dev", "engineer", 10, candidates)
    second = assembler.build("prj-1", "dev", "engineer", 10, reversed(candidates))

    assert first == second
    assert first.bundle_id == second.bundle_id
    assert [item.source_ref for item in first.items] == ["a", "b"]


def test_memory_projection_builds_bundle_from_authoritative_store(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "infra.sqlite")
    now = datetime(2026, 10, 7, tzinfo=UTC)
    evidence = (EvidenceItem("ev-1", "evt-1", "supports", "event:1", 1.0),)
    store.put(
        MemoryItem("mem-1", "prj-1", "shared", "fact", "release uses dev", ("evt-1",), now, 0.9),
        evidence,
    )
    store.put(
        MemoryItem("mem-2", "prj-1", "pm", "fact", "private pm note", ("evt-2",), now, 0.8),
        (EvidenceItem("ev-2", "evt-2", "supports", "event:2", 1.0),),
    )

    bundle = MemoryContextProjection(store).build(
        project_id="prj-1", profile_id="dev", role_id="engineer",
        as_of=now + timedelta(seconds=1), token_budget=20,
    )

    assert [item.source_ref for item in bundle.items] == ["memory:mem-1"]
    assert bundle.manifest.project_id == "prj-1"
