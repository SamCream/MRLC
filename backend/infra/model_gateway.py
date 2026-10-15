from __future__ import annotations

from dataclasses import dataclass
import sqlite3
from pathlib import Path


@dataclass(frozen=True, slots=True)
class ModelRequest:
    capability: str
    mode: str
    project_id: str
    idempotency_key: str
    estimated_cost: int = 0


@dataclass(frozen=True, slots=True)
class ModelResult:
    project_id: str
    model: str
    mode: str
    invocation_id: str
    fallback_reason: str | None = None
    status: str = "COMPLETED"
    provider_request_id: str | None = None
    usage_input: int = 0
    usage_output: int = 0


class ModelGateway:
    def __init__(
        self,
        routes: dict[str, list[str]],
        *,
        unavailable_models: set[str] | None = None,
        hard_budget: int | None = None,
        database_path: str | Path | None = None,
    ) -> None:
        self._routes = {capability: tuple(models) for capability, models in routes.items()}
        self._unavailable_models = unavailable_models or set()
        self._hard_budget = hard_budget
        self._database_path = str(database_path) if database_path is not None else None
        self.project_state_writes: list[object] = []
        if self._database_path is not None:
            self._initialize_persistence()

    def invoke(self, request: ModelRequest) -> ModelResult:
        existing = self._load(request.idempotency_key)
        if existing is not None:
            if existing.status == "UNKNOWN":
                raise RuntimeError("invocation outcome requires reconciliation")
            return existing
        if self._hard_budget is not None and request.estimated_cost > self._hard_budget:
            raise RuntimeError("budget rejected before invocation")
        try:
            candidates = self._routes[request.capability]
        except (KeyError, IndexError) as error:
            raise RuntimeError(f"no route for capability: {request.capability}") from error
        model = next((candidate for candidate in candidates if candidate not in self._unavailable_models), None)
        if model is None:
            raise RuntimeError(f"no available model for capability: {request.capability}")
        fallback_reason = None
        if candidates[0] != model:
            fallback_reason = f"{candidates[0]} unavailable"
        result = ModelResult(request.project_id, model, request.mode, request.idempotency_key, fallback_reason)
        self._persist(request.idempotency_key, result)
        return result

    def _connect(self) -> sqlite3.Connection:
        if self._database_path is None:
            raise RuntimeError("gateway persistence is not configured")
        connection = sqlite3.connect(self._database_path, timeout=30.0)
        connection.execute("PRAGMA busy_timeout = 30000")
        return connection

    def _initialize_persistence(self) -> None:
        with self._connect() as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS gateway_invocations ("
                "idempotency_key TEXT PRIMARY KEY, project_id TEXT NOT NULL, model TEXT NOT NULL, "
                "mode TEXT NOT NULL, invocation_id TEXT NOT NULL, fallback_reason TEXT, "
                "status TEXT NOT NULL DEFAULT 'COMPLETED', provider_request_id TEXT, "
                "usage_input INTEGER NOT NULL DEFAULT 0, usage_output INTEGER NOT NULL DEFAULT 0)"
            )
            columns = {row[1] for row in connection.execute("PRAGMA table_info(gateway_invocations)")}
            for name, definition in (
                ("status", "TEXT NOT NULL DEFAULT 'COMPLETED'"),
                ("provider_request_id", "TEXT"),
                ("usage_input", "INTEGER NOT NULL DEFAULT 0"),
                ("usage_output", "INTEGER NOT NULL DEFAULT 0"),
            ):
                if name not in columns:
                    connection.execute(f"ALTER TABLE gateway_invocations ADD COLUMN {name} {definition}")

    def _persist(self, key: str, result: ModelResult,
                 connection: sqlite3.Connection | None = None) -> None:
        if self._database_path is None:
            return
        statement = (
            "INSERT OR REPLACE INTO gateway_invocations "
            "(idempotency_key, project_id, model, mode, invocation_id, fallback_reason, status, "
            "provider_request_id, usage_input, usage_output) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
        )
        values = (
            key, result.project_id, result.model, result.mode, result.invocation_id,
            result.fallback_reason, result.status, result.provider_request_id,
            result.usage_input, result.usage_output,
        )
        if connection is not None:
            connection.execute(statement, values)
            return
        with self._connect() as own:
            own.execute(statement, values)

    def _load(self, key: str) -> ModelResult | None:
        if self._database_path is None:
            return None
        with self._connect() as connection:
            row = connection.execute(
                "SELECT project_id, model, mode, invocation_id, fallback_reason, status, "
                "provider_request_id, usage_input, usage_output "
                "FROM gateway_invocations WHERE idempotency_key = ?", (key,)
            ).fetchone()
        if row is None:
            return None
        return ModelResult(
            str(row[0]), str(row[1]), str(row[2]), str(row[3]),
            None if row[4] is None else str(row[4]), str(row[5]),
            None if row[6] is None else str(row[6]), int(row[7]), int(row[8]),
        )

    def record(self, result: ModelResult, *, idempotency_key: str | None = None,
               connection: sqlite3.Connection | None = None) -> ModelResult:
        """Record a completed gateway call in the usage ledger without re-routing it."""
        if self._database_path is None:
            raise RuntimeError("gateway persistence is required to record usage")
        key = idempotency_key or result.invocation_id
        self._persist(key, result, connection)
        return result

    def mark_unknown(
        self, idempotency_key: str, *, provider_request_id: str, reason: str,
        project_id: str = "unknown", mode: str = "unknown",
    ) -> ModelResult:
        if self._database_path is None:
            raise RuntimeError("gateway persistence is required for UNKNOWN reconciliation")
        existing = self._load(idempotency_key)
        if existing is not None:
            if existing.provider_request_id not in {None, provider_request_id}:
                raise ValueError("provider request id conflicts with invocation")
            if existing.status == "UNKNOWN":
                return existing
            project_id = existing.project_id
            mode = existing.mode
            model = existing.model
            fallback_reason = existing.fallback_reason
        else:
            model = "unknown"
            fallback_reason = reason
        result = ModelResult(
            project_id, model, mode, idempotency_key, fallback_reason,
            "UNKNOWN", provider_request_id, 0, 0,
        )
        self._persist(idempotency_key, result)
        return result

    def reconcile(
        self, idempotency_key: str, *, status: str, usage_input: int,
        usage_output: int, provider_request_id: str,
    ) -> ModelResult:
        existing = self._load(idempotency_key)
        if existing is None:
            raise KeyError(f"unknown invocation: {idempotency_key}")
        if existing.status != "UNKNOWN":
            if existing.provider_request_id != provider_request_id:
                raise ValueError("provider request id conflicts with invocation")
            return existing
        if existing.provider_request_id != provider_request_id:
            raise ValueError("provider request id conflicts with UNKNOWN invocation")
        if status not in {"COMPLETED", "FAILED"}:
            raise ValueError("reconciliation status must be COMPLETED or FAILED")
        if usage_input < 0 or usage_output < 0:
            raise ValueError("usage cannot be negative")
        result = ModelResult(
            existing.project_id, existing.model, existing.mode, existing.invocation_id,
            existing.fallback_reason, status, provider_request_id, usage_input, usage_output,
        )
        self._persist(idempotency_key, result)
        return result
