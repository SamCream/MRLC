from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from gateway.ccr.providers.openai_compatible import ProviderRequest, ProviderResponse
from infra.scheduler import Scheduler, SessionMeta


class CognitionProvider(Protocol):
    def invoke(self, request: ProviderRequest) -> ProviderResponse: ...


@dataclass(frozen=True, slots=True)
class FastCognitionResult:
    invocation_id: str
    session_id: str
    content: str
    candidate_only: bool
    model: str
    resource_domain: str
    provider_request_id: str
    fencing_token: int
    usage_total: int


class FastCognitionService:
    def __init__(
        self, scheduler: Scheduler, provider: CognitionProvider, *, model: str
    ) -> None:
        self._scheduler = scheduler
        self._provider = provider
        self._model = model

    def invoke(
        self,
        *,
        project_id: str,
        parent_session_id: str,
        invocation_id: str,
        prompt: str,
        now: int,
    ) -> FastCognitionResult:
        session_id = f"fast:{parent_session_id}:{invocation_id}"
        self._scheduler.enqueue(
            SessionMeta(session_id, project_id, priority=0, task_state="RUNNABLE"),
            now=now,
        )
        selected = self._scheduler.dispatch(now=now)
        if selected is None or selected.session_id != session_id:
            raise RuntimeError("fast cognition is waiting for a local model slot")
        try:
            response = self._provider.invoke(
                ProviderRequest(
                    invocation_id,
                    self._model.removeprefix("strata/"),
                    [{"role": "user", "content": prompt}],
                    max_tokens=128,
                )
            )
        except BaseException:
            self._scheduler.finish(session_id, task_state="FAILED")
            raise
        self._scheduler.finish(session_id, task_state="COMPLETED")
        return FastCognitionResult(
            invocation_id=invocation_id,
            session_id=session_id,
            content=response.content,
            candidate_only=True,
            model=self._model,
            resource_domain="local_176b",
            provider_request_id=response.provider_request_id,
            fencing_token=selected.fencing_token,
            usage_total=response.usage_total,
        )
