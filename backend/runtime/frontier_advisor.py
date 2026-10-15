from __future__ import annotations

import os
import sqlite3
from typing import Callable
from uuid import uuid4

from gateway.ccr.client import gateway_provider
from gateway.ccr.providers.openai_compatible import ProviderRequest
from infra.endpoints import ccr_base_url
from infra.frontier import FrontierAdvisorUnavailable, FrontierRequest
from infra.model_gateway import ModelGateway
from infra.model_gateway import ModelResult

CONSULT_SYSTEM_PROMPT = (
    "你是 MRLC 的 Frontier 咨询角色。仅返回针对该 capability 的分析建议。"
    "这是只读咨询：不得声称已创建 Child Session、已执行工具、已合并分支或已写入正式信念。"
    "不确定时明确说明需要 Parent Review。"
)


class FrontierAdvisor:
    """Bound CONSULT executor: routes frontier advice through the CCR model gateway."""

    def __init__(self, provider: object | None = None,
                 model_binding: str | None = None,
                 gateway: ModelGateway | None = None) -> None:
        self.provider = provider or gateway_provider(
            "ccr-frontier", ccr_base_url(), api_key_env="CCR_API_KEY",
            timeout_seconds=int(os.environ.get("MRLC_FRONTIER_TIMEOUT_SECONDS", "120")))
        self.model_binding = model_binding or os.environ.get(
            "MRLC_FRONTIER_MODEL_BINDING", "router-managed")
        self.gateway = gateway

    def consult(self, request: FrontierRequest,
                connection: sqlite3.Connection | None = None) -> str:
        """Answer a CONSULT request. `connection` joins the caller's Infra transaction."""
        if self.gateway is None:
            raise FrontierAdvisorUnavailable("frontier advisor requires the model gateway ledger")
        payload = ProviderRequest(
            f"frontier_consult:{request.project_id}:{request.parent_task_id}:{uuid4().hex}",
            self.model_binding,
            [{"role": "system", "content": CONSULT_SYSTEM_PROMPT},
             {"role": "user", "content": f"capability: {request.capability}\n"
                                         f"budget: {request.budget}\ndepth: {request.depth}"}],
            max_tokens=1024)
        try:
            response = self.provider.invoke(payload)
        except Exception as error:
            # Never present a gateway failure as returned advice.
            # The call may still have been billed, so it is UNKNOWN rather than absent.
            entry = ModelResult(
                project_id=request.project_id, model="unknown", mode="CONSULT",
                invocation_id=payload.invocation_id, fallback_reason=type(error).__name__,
                status="UNKNOWN", provider_request_id=None)
            failure = FrontierAdvisorUnavailable(f"frontier advisor call failed: {type(error).__name__}")
            if connection is None:
                self.gateway.record(entry)
            else:
                # The caller's transaction rolls back on this error; it must record the entry afterwards.
                failure.ledger_entry = entry
                failure.ledger = self.gateway
            raise failure from error
        content = (response.content or "").strip()
        if not content:
            raise FrontierAdvisorUnavailable("frontier advisor returned empty advice")
        self.gateway.record(ModelResult(
            project_id=request.project_id, model=response.model, mode="CONSULT",
            invocation_id=payload.invocation_id, fallback_reason=response.finish_reason,
            status="COMPLETED", provider_request_id=response.provider_request_id,
            usage_input=response.usage_input, usage_output=response.usage_output),
            connection=connection)
        return content

    def bind(self, connection: sqlite3.Connection | None):
        """Return an advisor callable that writes usage inside the given transaction."""
        def answer(request: FrontierRequest) -> str:
            return self.consult(request, connection)
        return answer

    def __call__(self, request: FrontierRequest) -> str:
        return self.consult(request)


def advisor_or_none() -> Callable[[FrontierRequest], str] | None:
    """A frontier advisor is only available when the gateway credential is configured."""
    if not os.environ.get("CCR_API_KEY"):
        return None
    return FrontierAdvisor()
