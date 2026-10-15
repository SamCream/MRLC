"""Provider adapter boundary backed by the migrated CCR/OpenAI-compatible provider.

The public FastAPI contract stays unchanged; runtime-specific checkpoint/pause APIs remain
capability-gated and are never fabricated.
"""
from __future__ import annotations
import asyncio
import os
from uuid import uuid4

from gateway.ccr.providers.openai_compatible import (
    OpenAICompatibleProvider,
    ProviderRequest,
    ProviderTransportError,
)


class ExecutorNotConfigured(RuntimeError):
    pass


class OpenAIExecutor:
    def __init__(self):
        raw_base=os.getenv('MRLC_MODEL_BASE_URL','').rstrip('/')
        self.base_url=raw_base[:-3] if raw_base.endswith('/v1') else raw_base
        self.model=os.getenv('MRLC_MODEL_ID','')
        self.timeout=float(os.getenv('MRLC_MODEL_TIMEOUT_SECONDS','180'))
        self.input_price=float(os.getenv('MRLC_MODEL_INPUT_USD_PER_1M','0') or 0)
        self.output_price=float(os.getenv('MRLC_MODEL_OUTPUT_USD_PER_1M','0') or 0)
        if self.base_url and not self.base_url.startswith(('http://','https://')):
            raise ValueError('MRLC_MODEL_BASE_URL must be http(s)')
        self.provider=(
            OpenAICompatibleProvider(
                'mrlc-default',
                self.base_url,
                api_key_env='MRLC_MODEL_API_KEY' if os.getenv('MRLC_MODEL_API_KEY') else None,
                timeout_seconds=self.timeout,
            )
            if self.base_url else None
        )

    @property
    def available(self):
        return bool(self.provider and self.model)

    @property
    def capabilities(self):
        return {
            'chatCompletions':self.available,
            'usageAccounting':True,
            'streaming':True,
            'checkpoint':False,
            'pauseResume':False,
            'physicalSlotControl':False,
            'providerBoundary':'gateway.ccr.providers.openai_compatible',
        }

    async def complete_result(self,system_prompt:str,messages:list[dict]):
        if not self.available:
            raise ExecutorNotConfigured('MRLC_MODEL_BASE_URL / MRLC_MODEL_ID not configured')
        invocation_id='inv-'+uuid4().hex
        request=ProviderRequest(
            invocation_id=invocation_id,
            model=self.model,
            messages=[{'role':'system','content':system_prompt},*messages],
            max_tokens=max(1,int(os.getenv('MRLC_MODEL_MAX_TOKENS','2048'))),
            temperature=float(os.getenv('MRLC_MODEL_TEMPERATURE','0')),
        )
        try:
            response=await asyncio.to_thread(self.provider.invoke,request)
        except ProviderTransportError:
            raise
        value=response.content
        if not isinstance(value,str) or not value.strip():
            raise ValueError('Provider returned empty content')
        inp=int(response.usage_input or 0)
        out=int(response.usage_output or 0)
        cost=(inp*self.input_price+out*self.output_price)/1_000_000 if (self.input_price or self.output_price) else None
        return {
            'content':value,
            'model':response.model or self.model,
            'inputTokens':inp or None,
            'outputTokens':out or None,
            'costUsd':cost,
            'usageReported':bool(inp or out or response.usage_total),
            'providerRequestId':response.provider_request_id,
            'finishReason':response.finish_reason,
            'reasoningContent':response.reasoning_content,
            'invocationId':response.invocation_id,
        }

    async def complete(self,system_prompt:str,messages:list[dict]):
        return (await self.complete_result(system_prompt,messages))['content']
