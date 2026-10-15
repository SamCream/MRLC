from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path


@dataclass(frozen=True, slots=True)
class EvidenceItem:
    evidence_id: str
    event_id: str
    stance: str
    source_ref: str
    confidence: float


@dataclass(frozen=True, slots=True)
class MemoryItem:
    memory_id: str
    project_id: str
    role_scope: str
    kind: str
    content: str
    source_event_refs: tuple[str, ...]
    valid_from: datetime
    confidence: float
    status: str = "active"
    valid_to: datetime | None = None


class MemoryStore:
    def __init__(self, database_path: str | Path) -> None:
        self._database_path = str(database_path)
        with sqlite3.connect(self._database_path) as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS memories "
                "(memory_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, role_scope TEXT NOT NULL, "
                "kind TEXT NOT NULL, content TEXT NOT NULL, source_event_refs TEXT NOT NULL, "
                "valid_from TEXT NOT NULL, valid_to TEXT, confidence REAL NOT NULL, status TEXT NOT NULL)"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS memory_evidence "
                "(memory_id TEXT NOT NULL, evidence_id TEXT NOT NULL, event_id TEXT NOT NULL, "
                "stance TEXT NOT NULL, source_ref TEXT NOT NULL, confidence REAL NOT NULL, "
                "PRIMARY KEY (memory_id, evidence_id))"
            )

    @staticmethod
    def _validate(item: MemoryItem, evidence: tuple[EvidenceItem, ...]) -> None:
        if not item.source_event_refs:
            raise ValueError("memory requires at least one source event")
        if not evidence:
            raise ValueError("memory requires supporting evidence")
        if not 0.0 <= item.confidence <= 1.0:
            raise ValueError("memory confidence must be between zero and one")
        if any(not 0.0 <= entry.confidence <= 1.0 for entry in evidence):
            raise ValueError("memory evidence confidence must be between zero and one")

    def put(self, item: MemoryItem, evidence: tuple[EvidenceItem, ...]) -> None:
        with sqlite3.connect(self._database_path) as connection:
            self.put_in_connection(connection,item,evidence)

    def put_in_connection(
        self, connection: sqlite3.Connection, item: MemoryItem,
        evidence: tuple[EvidenceItem, ...],
    ) -> None:
        """Transactional variant used by Evented application layers."""
        self._validate(item,evidence)
        connection.execute(
            "INSERT OR REPLACE INTO memories "
            "(memory_id, project_id, role_scope, kind, content, source_event_refs, valid_from, valid_to, confidence, status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                item.memory_id,
                item.project_id,
                item.role_scope,
                item.kind,
                item.content,
                json.dumps(item.source_event_refs),
                item.valid_from.isoformat(),
                item.valid_to.isoformat() if item.valid_to else None,
                item.confidence,
                item.status,
            ),
        )
        connection.executemany(
            "INSERT OR REPLACE INTO memory_evidence "
            "(memory_id, evidence_id, event_id, stance, source_ref, confidence) VALUES (?, ?, ?, ?, ?, ?)",
            [(item.memory_id, e.evidence_id, e.event_id, e.stance, e.source_ref, e.confidence) for e in evidence],
        )

    def get(self, project_id: str, memory_id: str) -> MemoryItem:
        with sqlite3.connect(self._database_path) as connection:
            row = connection.execute("SELECT * FROM memories WHERE memory_id = ?", (memory_id,)).fetchone()
        if row is None:
            raise KeyError(f"unknown memory: {memory_id}")
        if row[1] != project_id:
            raise PermissionError("memory access crosses project boundary")
        return self._from_row(row)

    def evidence(self, memory_id: str) -> tuple[EvidenceItem, ...]:
        with sqlite3.connect(self._database_path) as connection:
            rows = connection.execute(
                "SELECT evidence_id, event_id, stance, source_ref, confidence "
                "FROM memory_evidence WHERE memory_id = ? ORDER BY evidence_id", (memory_id,)
            ).fetchall()
        return tuple(EvidenceItem(*row) for row in rows)

    def query(self, project_id: str, text: str, role_id: str, as_of: datetime) -> list[MemoryItem]:
        with sqlite3.connect(self._database_path) as connection:
            rows = connection.execute(
                "SELECT * FROM memories WHERE project_id = ? AND status = 'active' "
                "AND (role_scope = 'shared' OR role_scope = ?) AND valid_from <= ? "
                "AND (valid_to IS NULL OR valid_to > ?) AND content LIKE ? ORDER BY valid_from",
                (project_id, role_id, as_of.isoformat(), as_of.isoformat(), f"%{text}%"),
            ).fetchall()
        return [self._from_row(row) for row in rows]

    def active(self, project_id: str, role_id: str, as_of: datetime) -> list[MemoryItem]:
        with sqlite3.connect(self._database_path) as connection:
            rows = connection.execute(
                "SELECT * FROM memories WHERE project_id = ? AND status = 'active' "
                "AND (role_scope = 'shared' OR role_scope = ?) AND valid_from <= ? "
                "AND (valid_to IS NULL OR valid_to > ?) ORDER BY valid_from, memory_id",
                (project_id, role_id, as_of.isoformat(), as_of.isoformat()),
            ).fetchall()
        return [self._from_row(row) for row in rows]

    def supersede(self, project_id: str, old_memory_id: str, new_memory_id: str) -> None:
        with sqlite3.connect(self._database_path) as connection:
            self.supersede_in_connection(connection,project_id,old_memory_id,new_memory_id)

    def supersede_in_connection(
        self, connection: sqlite3.Connection, project_id: str,
        old_memory_id: str, new_memory_id: str,
    ) -> None:
        old=connection.execute(
            "SELECT project_id,status FROM memories WHERE memory_id=?",(old_memory_id,)
        ).fetchone()
        new=connection.execute(
            "SELECT project_id FROM memories WHERE memory_id=?",(new_memory_id,)
        ).fetchone()
        if old is None:
            raise KeyError(f"unknown memory: {old_memory_id}")
        if new is None:
            raise KeyError(f"unknown memory: {new_memory_id}")
        if old[0] != project_id or new[0] != project_id:
            raise PermissionError("memory access crosses project boundary")
        connection.execute(
            "UPDATE memories SET status='superseded' WHERE memory_id=?",(old_memory_id,)
        )

    @staticmethod
    def _from_row(row: tuple[object, ...]) -> MemoryItem:
        return MemoryItem(
            memory_id=row[0],
            project_id=row[1],
            role_scope=row[2],
            kind=row[3],
            content=row[4],
            source_event_refs=tuple(json.loads(row[5])),
            valid_from=datetime.fromisoformat(row[6]),
            valid_to=datetime.fromisoformat(row[7]) if row[7] else None,
            confidence=row[8],
            status=row[9],
        )
