from __future__ import annotations

from dataclasses import dataclass, replace
import sqlite3
from pathlib import Path


@dataclass(frozen=True, slots=True)
class Observation:
    observation_id: str
    actor_id: str
    content: str
    evidence_ref: str
    confidence: float


@dataclass(frozen=True, slots=True)
class BeliefCandidate:
    candidate_id: str
    actor_id: str
    statement: str
    evidence_refs: tuple[str, ...]
    confidence: float
    status: str = "CANDIDATE"
    reviewer: str | None = None
    policy_version: str | None = None
    counterevidence: tuple[str, ...] = ()
    uncertainty: tuple[str, ...] = ()
    project_id: str | None = None

    def __post_init__(self) -> None:
        if not self.evidence_refs:
            raise ValueError("belief candidate requires evidence")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("belief confidence must be between zero and one")


@dataclass(frozen=True, slots=True)
class DecisionPacket:
    current_observation: str
    relevant_memory_refs: tuple[str, ...]
    self_state_ref: str | None
    other_state_ref: str | None
    relationship_state_ref: str | None
    candidate_hypotheses: tuple[str, ...]
    candidate_actions: tuple[str, ...]
    known_uncertainty: tuple[str, ...]
    question: str
    desired_output: str


@dataclass(frozen=True, slots=True)
class RolloverState:
    identity_version: str
    current_goals: tuple[str, ...]
    relationship_state_refs: tuple[str, ...]
    unresolved_candidates: tuple[str, ...]
    recent_episodes: tuple[str, ...]
    policy_version: str


class A2CMPlugin:
    def __init__(self, database_path: str | Path | None = None) -> None:
        self._database_path = str(database_path) if database_path is not None else None
        self._candidates: dict[str, BeliefCandidate] = {}
        if self._database_path is not None:
            self._initialize_persistence()
            self._load_candidates()

    def observe(self, observation: Observation, source: str, *,
                project_id: str | None = None) -> BeliefCandidate:
        if source not in {"fast_tool", "primary", "frontier"}:
            raise ValueError(f"unsupported observation source: {source}")
        candidate = BeliefCandidate(
            candidate_id=f"cand_{observation.observation_id}",
            actor_id=observation.actor_id,
            statement=observation.content,
            evidence_refs=(observation.evidence_ref,),
            confidence=observation.confidence,
            project_id=project_id,
        )
        self._candidates[candidate.candidate_id] = candidate
        self._persist(candidate)
        return candidate

    @staticmethod
    def observe_in_connection(
        connection: sqlite3.Connection,
        observation: Observation,
        source: str,
        *,
        project_id: str,
    ) -> BeliefCandidate:
        """Persist an evidence-backed candidate inside the caller's transaction."""
        import json

        if source not in {"fast_tool", "primary", "frontier"}:
            raise ValueError(f"unsupported observation source: {source}")
        if not project_id.strip():
            raise ValueError("belief candidate requires project scope")
        candidate=BeliefCandidate(
            candidate_id=f"cand_{observation.observation_id}",
            actor_id=observation.actor_id,
            statement=observation.content,
            evidence_refs=(observation.evidence_ref,),
            confidence=observation.confidence,
            project_id=project_id,
        )
        connection.execute(
            """
            INSERT INTO a2cm_candidates(
                candidate_id,actor_id,statement,evidence_refs_json,confidence,status,
                reviewer,policy_version,counterevidence_json,uncertainty_json,project_id
            ) VALUES(?,?,?,?,?,'CANDIDATE',NULL,NULL,'[]','[]',?)
            """,
            (
                candidate.candidate_id,candidate.actor_id,candidate.statement,
                json.dumps(candidate.evidence_refs),candidate.confidence,project_id,
            ),
        )
        return candidate

    def promote(
        self,
        candidate_id: str,
        actor: str,
        *,
        policy_version: str = "a2cm.policy.v1",
        counterevidence: tuple[str, ...] = (),
        uncertainty: tuple[str, ...] = (),
        project_id: str | None = None,
    ) -> BeliefCandidate:
        if actor != "primary":
            raise PermissionError("only Primary may promote belief candidates")
        if not policy_version.strip():
            raise ValueError("promotion requires a policy version")
        try:
            candidate = self._candidates[candidate_id]
        except KeyError as error:
            raise KeyError(f"unknown belief candidate: {candidate_id}") from error
        if project_id is not None and candidate.project_id != project_id:
            raise PermissionError("belief candidate crosses project boundary")
        promoted = replace(
            candidate,
            status="PROMOTED",
            reviewer=actor,
            policy_version=policy_version,
            counterevidence=tuple(counterevidence),
            uncertainty=tuple(uncertainty),
        )
        self._candidates[candidate_id] = promoted
        self._persist(promoted)
        return promoted

    REVIEW_OUTCOMES = {"PROMOTE": "PROMOTED", "REJECT": "REJECTED", "DEFER": "DEFERRED"}

    @classmethod
    def review_in_connection(
        cls,
        connection: sqlite3.Connection,
        project_id: str,
        candidate_id: str,
        decision: str,
        *,
        policy_version: str,
        counterevidence: tuple[str, ...] = (),
        uncertainty: tuple[str, ...] = (),
    ) -> dict[str, object]:
        """Primary review inside the caller's transaction; only reviewable candidates change."""
        import json

        if decision not in cls.REVIEW_OUTCOMES:
            raise ValueError("decision must be PROMOTE, REJECT or DEFER")
        if not policy_version.strip():
            raise ValueError("review requires a policy version")
        row = connection.execute(
            "SELECT project_id, status, confidence FROM a2cm_candidates WHERE candidate_id = ?",
            (candidate_id,)).fetchone()
        if row is None:
            raise KeyError("unknown belief candidate")
        if row[0] != project_id:
            raise PermissionError("belief candidate crosses project boundary")
        if row[1] not in {"CANDIDATE", "DEFERRED"}:
            raise ValueError("belief candidate is not awaiting review")
        status = cls.REVIEW_OUTCOMES[decision]
        updated = connection.execute(
            "UPDATE a2cm_candidates SET status = ?, reviewer = 'primary', policy_version = ?, "
            "counterevidence_json = ?, uncertainty_json = ? WHERE candidate_id = ? AND status = ?",
            (status, policy_version.strip(), json.dumps(list(counterevidence)),
             json.dumps(list(uncertainty)), candidate_id, row[1])).rowcount
        if updated != 1:
            raise ValueError("belief candidate changed during review")
        return {"candidate_id": candidate_id, "previous_status": row[1], "status": status,
                "reviewer": "primary", "policy_version": policy_version.strip(),
                "confidence": row[2], "counterevidence_count": len(counterevidence),
                "uncertainty_count": len(uncertainty)}

    @staticmethod
    def prepare_frontier_packet(packet: DecisionPacket) -> DecisionPacket:
        return replace(packet, self_state_ref=None, other_state_ref=None, relationship_state_ref=None)

    @staticmethod
    def rollover(state: RolloverState) -> RolloverState:
        return state

    def _connect(self) -> sqlite3.Connection:
        if self._database_path is None:
            raise RuntimeError("A2CM persistence is not configured")
        connection = sqlite3.connect(self._database_path, timeout=30.0)
        connection.execute("PRAGMA busy_timeout = 30000")
        return connection

    def _initialize_persistence(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS a2cm_candidates (
                    candidate_id TEXT PRIMARY KEY,
                    actor_id TEXT NOT NULL,
                    statement TEXT NOT NULL,
                    evidence_refs_json TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    status TEXT NOT NULL,
                    reviewer TEXT,
                    policy_version TEXT,
                    counterevidence_json TEXT NOT NULL DEFAULT '[]',
                    uncertainty_json TEXT NOT NULL DEFAULT '[]',
                    project_id TEXT
                )
                """
            )
            columns = {row[1] for row in connection.execute("PRAGMA table_info(a2cm_candidates)")}
            for name, definition in (
                ("reviewer", "TEXT"),
                ("policy_version", "TEXT"),
                ("counterevidence_json", "TEXT NOT NULL DEFAULT '[]'"),
                ("uncertainty_json", "TEXT NOT NULL DEFAULT '[]'"),
                ("project_id", "TEXT"),
            ):
                if name not in columns:
                    connection.execute(f"ALTER TABLE a2cm_candidates ADD COLUMN {name} {definition}")

    def _persist(self, candidate: BeliefCandidate) -> None:
        if self._database_path is None:
            return
        import json

        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO a2cm_candidates(
                    candidate_id, actor_id, statement, evidence_refs_json, confidence, status,
                    reviewer, policy_version, counterevidence_json, uncertainty_json, project_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(candidate_id) DO UPDATE SET
                    status = excluded.status,
                    reviewer = excluded.reviewer,
                    policy_version = excluded.policy_version,
                    counterevidence_json = excluded.counterevidence_json,
                    uncertainty_json = excluded.uncertainty_json
                """,
                (
                    candidate.candidate_id, candidate.actor_id, candidate.statement,
                    json.dumps(candidate.evidence_refs), candidate.confidence, candidate.status,
                    candidate.reviewer, candidate.policy_version,
                    json.dumps(candidate.counterevidence), json.dumps(candidate.uncertainty),
                    candidate.project_id,
                ),
            )

    def _load_candidates(self) -> None:
        import json

        with self._connect() as connection:
            rows = connection.execute(
                "SELECT candidate_id, actor_id, statement, evidence_refs_json, confidence, status, "
                "reviewer, policy_version, counterevidence_json, uncertainty_json, project_id "
                "FROM a2cm_candidates"
            ).fetchall()
        self._candidates = {
            str(row[0]): BeliefCandidate(
                candidate_id=str(row[0]), actor_id=str(row[1]), statement=str(row[2]),
                evidence_refs=tuple(json.loads(str(row[3]))), confidence=float(row[4]),
                status=str(row[5]),
                reviewer=None if row[6] is None else str(row[6]),
                policy_version=None if row[7] is None else str(row[7]),
                counterevidence=tuple(json.loads(row[8] or "[]")),
                uncertainty=tuple(json.loads(row[9] or "[]")),
                project_id=None if row[10] is None else str(row[10]),
            )
            for row in rows
        }
