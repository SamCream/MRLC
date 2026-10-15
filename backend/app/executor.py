"""Provider adapter boundary.

OpenAI-compatible endpoints (CCR gateways, vLLM, many local runtimes) work directly.
Runtime-specific checkpoint/pause APIs remain capability-gated and are never fabricated.
"""
from __future__ import annotations
import os
import httpx

class ExecutorNotConfigured(RuntimeError):pass

class OpenAIExecutor:
    def __init__(self):
        self.base_url=os.getenv('MRLC_MODEL_BASE_URL','').rstrip('/')
        self.model=os.getenv('MRLC_MODEL_ID','')
        self.token=os.getenv('MRLC_MODEL_API_KEY','')
        self.timeout=float(os.getenv('MRLC_MODEL_TIMEOUT_SECONDS','180'))
        self.input_price=float(os.getenv('MRLC_MODEL_INPUT_USD_PER_1M','0') or 0)
        self.output_price=float(os.getenv('MRLC_MODEL_OUTPUT_USD_PER_1M','0') or 0)
        if self.base_url and not self.base_url.startswith(('http://','https://')):
            raise ValueError('MRLC_MODEL_BASE_URL must be http(s)')

    @property
    def available(self): return bool(self.base_url and self.model)

    @property
    def capabilities(self):
        return {'chatCompletions':self.available,'usageAccounting':True,'checkpoint':False,'pauseResume':False,'physicalSlotControl':False}

    async def complete_result(self, system_prompt:str, messages:list[dict]):
        if not self.available: raise ExecutorNotConfigured('MRLC_MODEL_BASE_URL / MRLC_MODEL_ID not configured')
        url=self.base_url + ('/chat/completions' if self.base_url.endswith('/v1') else '/v1/chat/completions')
        headers={'Content-Type':'application/json'}
        if self.token:headers['Authorization']='Bearer '+self.token
        async with httpx.AsyncClient(timeout=self.timeout,follow_redirects=False) as client:
            result=await client.post(url,headers=headers,json={'model':self.model,'messages':[{'role':'system','content':system_prompt},*messages],'stream':False})
            result.raise_for_status();doc=result.json();value=doc['choices'][0]['message']['content']
            if not isinstance(value,str) or not value.strip():raise ValueError('Provider returned empty content')
            usage=doc.get('usage') or {}
            inp=int(usage.get('prompt_tokens') or usage.get('input_tokens') or 0)
            out=int(usage.get('completion_tokens') or usage.get('output_tokens') or 0)
            cost=(inp*self.input_price+out*self.output_price)/1_000_000 if (self.input_price or self.output_price) else None
            return {'content':value,'model':doc.get('model') or self.model,'inputTokens':inp or None,'outputTokens':out or None,'costUsd':cost,'usageReported':bool(usage)}

    async def complete(self,system_prompt:str,messages:list[dict]):
        return (await self.complete_result(system_prompt,messages))['content']
