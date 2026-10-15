from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from enum import IntEnum
from pathlib import Path
from uuid import uuid4


class CutoverStage(IntEnum):
    C0_BASELINE = 0
    C1_OBSERVE = 1
    C2_CANARY = 2
    C3_AUTHORITY_SWITCH = 3
    C4_FULL_TRAFFIC = 4
    C5_LEGACY_READ_ONLY = 5
    C6_REMOVAL = 6


@dataclass(frozen=True, slots=True)
class CutoverPlan:
    plan_id: str
    component: str
    owner: str
    rollback_ref: str
    stage: CutoverStage = CutoverStage.C0_BASELINE
    evidence_refs: tuple[str, ...] = ()
    difference_count: int = 0


@dataclass(frozen=True, slots=True)
class MigrationBatch:
    batch_id: str
    component: str
    source_high_water_mark: str
    source_count: int
    target_count: int
    source_hash: str
    target_hash: str
    resumable_cursor: str
    quarantine_refs: tuple[str, ...] = ()


class CutoverStore:
    def __init__(self, database_path: str | Path) -> None:
        self._database_path = str(database_path)
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS cutover_plans (
                    plan_id TEXT PRIMARY KEY,
                    component TEXT NOT NULL UNIQUE,
                    owner TEXT NOT NULL,
                    rollback_ref TEXT NOT NULL,
                    stage INTEGER NOT NULL,
                    evidence_refs_json TEXT NOT NULL,
                    difference_count INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS cutover_transitions (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    plan_id TEXT NOT NULL,
                    from_stage INTEGER NOT NULL,
                    to_stage INTEGER NOT NULL,
                    evidence_refs_json TEXT NOT NULL,
                    difference_count INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS cutover_write_audit (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    plan_id TEXT NOT NULL,
                    writer TEXT NOT NULL,
                    operation_id TEXT NOT NULL,
                    result TEXT NOT NULL,
                    UNIQUE(plan_id, operation_id)
                );
                CREATE TABLE IF NOT EXISTS migration_batches (
                    operation_id TEXT PRIMARY KEY,
                    batch_id TEXT NOT NULL,
                    component TEXT NOT NULL,
                    source_high_water_mark TEXT NOT NULL,
                    source_count INTEGER NOT NULL,
                    target_count INTEGER NOT NULL,
                    source_hash TEXT NOT NULL,
                    target_hash TEXT NOT NULL,
                    resumable_cursor TEXT NOT NULL,
                    quarantine_refs_json TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );
                """
            )

    def create_plan(self, component: str, *, owner: str, rollback_ref: str) -> CutoverPlan:
        if not component.strip() or not owner.strip() or not rollback_ref.strip():
            raise ValueError("cutover plan requires component, owner, and rollback reference")
        plan = CutoverPlan(f"cut_{uuid4().hex}", component, owner, rollback_ref)
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO cutover_plans VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    plan.plan_id,
                    plan.component,
                    plan.owner,
                    plan.rollback_ref,
                    int(plan.stage),
                    "[]",
                    0,
                ),
            )
        return plan

    def get(self, plan_id: str) -> CutoverPlan:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT plan_id, component, owner, rollback_ref, stage, evidence_refs_json, difference_count "
                "FROM cutover_plans WHERE plan_id = ?",
                (plan_id,),
            ).fetchone()
        if row is None:
            raise KeyError(f"unknown cutover plan: {plan_id}")
        return self._plan(row)

    def transition(
        self,
        plan_id: str,
        target: CutoverStage,
        *,
        evidence_refs: tuple[str, ...],
        difference_count: int = 0,
    ) -> CutoverPlan:
        if not evidence_refs:
            raise ValueError("cutover transition requires evidence")
        if difference_count < 0:
            raise ValueError("difference count cannot be negative")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT plan_id, component, owner, rollback_ref, stage, evidence_refs_json, difference_count "
                "FROM cutover_plans WHERE plan_id = ?",
                (plan_id,),
            ).fetchone()
            if row is None:
                raise KeyError(f"unknown cutover plan: {plan_id}")
            current = self._plan(row)
            if int(target) != int(current.stage) + 1:
                raise ValueError("cutover transitions must be sequential")
            if target >= CutoverStage.C3_AUTHORITY_SWITCH and difference_count != 0:
                raise ValueError("authority switch requires zero difference")
            connection.execute(
                "UPDATE cutover_plans SET stage = ?, evidence_refs_json = ?, difference_count = ? "
                "WHERE plan_id = ?",
                (int(target), json.dumps(evidence_refs), difference_count, plan_id),
            )
            connection.execute(
                "INSERT INTO cutover_transitions(plan_id, from_stage, to_stage, evidence_refs_json, difference_count) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    plan_id,
                    int(current.stage),
                    int(target),
                    json.dumps(evidence_refs),
                    difference_count,
                ),
            )
        return self.get(plan_id)

    def assert_write_allowed(self, plan_id: str, *, writer: str, operation_id: str) -> None:
        if not operation_id.strip():
            raise ValueError("cutover write requires operation id")
        plan = self.get(plan_id)
        allowed = plan.stage < CutoverStage.C3_AUTHORITY_SWITCH or writer == plan.owner
        with self._connect() as connection:
            try:
                connection.execute(
                    "INSERT INTO cutover_write_audit(plan_id, writer, operation_id, result) VALUES (?, ?, ?, ?)",
                    (plan_id, writer, operation_id, "ALLOWED" if allowed else "DENIED"),
                )
            except sqlite3.IntegrityError as error:
                row = connection.execute(
                    "SELECT writer, result FROM cutover_write_audit WHERE plan_id = ? AND operation_id = ?",
                    (plan_id, operation_id),
                ).fetchone()
                if row is None or row[0] != writer or row[1] != ("ALLOWED" if allowed else "DENIED"):
                    raise ValueError("operation id conflict") from error
        if not allowed:
            raise PermissionError(
                f"legacy writer is disabled after authority switch: {writer}"
            )

    def record_batch(self, batch: MigrationBatch, *, operation_id: str) -> MigrationBatch:
        if not operation_id.strip():
            raise ValueError("migration batch requires operation id")
        if min(batch.source_count, batch.target_count) < 0:
            raise ValueError("migration counts cannot be negative")
        payload = {
            "batch_id": batch.batch_id,
            "component": batch.component,
            "source_high_water_mark": batch.source_high_water_mark,
            "source_count": batch.source_count,
            "target_count": batch.target_count,
            "source_hash": batch.source_hash,
            "target_hash": batch.target_hash,
            "resumable_cursor": batch.resumable_cursor,
            "quarantine_refs": list(batch.quarantine_refs),
        }
        serialized = json.dumps(payload, sort_keys=True)
        with self._connect() as connection:
            existing = connection.execute(
                "SELECT payload_json FROM migration_batches WHERE operation_id = ?",
                (operation_id,),
            ).fetchone()
            if existing is not None:
                if str(existing[0]) != serialized:
                    raise ValueError("operation id conflict")
                return batch
            connection.execute(
                "INSERT INTO migration_batches VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    operation_id,
                    batch.batch_id,
                    batch.component,
                    batch.source_high_water_mark,
                    batch.source_count,
                    batch.target_count,
                    batch.source_hash,
                    batch.target_hash,
                    batch.resumable_cursor,
                    json.dumps(batch.quarantine_refs),
                    serialized,
                ),
            )
        return batch

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._database_path, timeout=30.0)
        connection.row_factory = sqlite3.Row
        return connection

    @staticmethod
    def _plan(row: sqlite3.Row) -> CutoverPlan:
        return CutoverPlan(
            plan_id=str(row["plan_id"]),
            component=str(row["component"]),
            owner=str(row["owner"]),
            rollback_ref=str(row["rollback_ref"]),
            stage=CutoverStage(int(row["stage"])),
            evidence_refs=tuple(json.loads(str(row["evidence_refs_json"]))),
            difference_count=int(row["difference_count"]),
        )
