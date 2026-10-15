from datetime import datetime, timezone

import pytest

from infra.memory import EvidenceItem, MemoryItem, MemoryStore


def test_memory_requires_provenance_and_supporting_evidence(tmp_path) -> None:
    store = MemoryStore(tmp_path / "infra.sqlite3")
    evidence = EvidenceItem("evd_1", "evt_1", "supports", "event:evt_1", 0.9)
    item = MemoryItem(
        memory_id="mem_1",
        project_id="prj_one",
        role_scope="dev.engineer",
        kind="semantic",
        content="SQLite replay is deterministic",
        source_event_refs=("evt_1",),
        valid_from=datetime(2026, 1, 1, tzinfo=timezone.utc),
        confidence=0.9,
    )

    store.put(item, (evidence,))

    assert store.get("prj_one", "mem_1") == item
    assert store.evidence("mem_1") == (evidence,)


def test_memory_query_filters_project_role_and_time(tmp_path) -> None:
    store = MemoryStore(tmp_path / "infra.sqlite3")
    for memory_id, role_scope, date in (
        ("mem_a", "dev.engineer", datetime(2026, 1, 1, tzinfo=timezone.utc)),
        ("mem_b", "a2cm.primary", datetime(2026, 2, 1, tzinfo=timezone.utc)),
    ):
        store.put(
            MemoryItem(memory_id, "prj_one", role_scope, "semantic", "replay event", ("evt",), date, 0.8),
            (EvidenceItem(f"evd_{memory_id}", "evt", "supports", "synthetic", 0.8),),
        )

    results = store.query("prj_one", "replay", "dev.engineer", datetime(2026, 1, 15, tzinfo=timezone.utc))

    assert [item.memory_id for item in results] == ["mem_a"]


def test_memory_supersedes_without_deleting_history(tmp_path) -> None:
    store = MemoryStore(tmp_path / "infra.sqlite3")
    old = MemoryItem("mem_old", "prj_one", "shared", "semantic", "old route", ("evt",), datetime(2026, 1, 1, tzinfo=timezone.utc), 0.5)
    new = MemoryItem("mem_new", "prj_one", "shared", "semantic", "new route", ("evt2",), datetime(2026, 2, 1, tzinfo=timezone.utc), 0.9)
    evidence = (EvidenceItem("evd", "evt", "supports", "synthetic", 0.5),)
    store.put(old, evidence)
    store.put(new, evidence)

    store.supersede("prj_one", "mem_old", "mem_new")

    assert store.get("prj_one", "mem_old").status == "superseded"
    assert store.get("prj_one", "mem_new").status == "active"


def test_memory_rejects_missing_provenance(tmp_path) -> None:
    store = MemoryStore(tmp_path / "infra.sqlite3")
    item = MemoryItem("mem_1", "prj_one", "shared", "semantic", "orphan", (), datetime.now(timezone.utc), 0.5)

    with pytest.raises(ValueError, match="source event"):
        store.put(item, ())


def test_memory_rejects_cross_project_read(tmp_path) -> None:
    store = MemoryStore(tmp_path / "infra.sqlite3")
    item = MemoryItem("mem_1", "prj_one", "shared", "semantic", "private", ("evt",), datetime.now(timezone.utc), 0.5)
    store.put(item, (EvidenceItem("evd", "evt", "supports", "synthetic", 0.5),))

    with pytest.raises(PermissionError):
        store.get("prj_two", "mem_1")
