from __future__ import annotations

from typing import Protocol
from dataclasses import replace

from adapters.contract import DispatchReceipt, TaskPackage, WorkerSessionSpec
from infra.scheduler import ParkDecision, Scheduler, SessionMeta


class WorkerAdapter(Protocol):
    def start(self, spec: WorkerSessionSpec, task: TaskPackage | None = None) -> str: ...

    def send_task(self, handle: str, task: TaskPackage, *, fencing_token: int) -> DispatchReceipt: ...


class RuntimeWorkerBridge:
    def __init__(self, scheduler: Scheduler, adapter: WorkerAdapter) -> None:
        self._scheduler = scheduler
        self._adapter = adapter

    def dispatch(
        self,
        session: SessionMeta,
        spec: WorkerSessionSpec,
        task: TaskPackage,
        *,
        now: int = 0,
    ) -> DispatchReceipt | None:
        self._scheduler.enqueue(session)
        selected = self._scheduler.dispatch(now=now)
        if selected is None or selected.session_id != session.session_id:
            return None
        spec = replace(spec, lease_token=selected.fencing_token)
        try:
            handle = self._adapter.start(spec)
            return self._adapter.send_task(handle, task, fencing_token=spec.lease_token)
        except BaseException:
            self._scheduler.park(session.session_id, ParkDecision.ACCEPT, "worker dispatch failed")
            raise
