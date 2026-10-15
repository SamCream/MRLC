"""Compatibility re-export of the Infra-owned worker run store.

Adapters must not own formal state: `WorkerRunStore` is defined in `infra/`, and this module
keeps the historical `adapters.worker_state` import path working during the G10 cutover.
New code should import from `infra.worker_runtime_state` directly.
"""
from infra.worker_runtime_state import CursorGap, FencingViolation, WorkerRunStore

__all__ = ("CursorGap", "FencingViolation", "WorkerRunStore")
