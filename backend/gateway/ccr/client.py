"""Client construction for the CCR gateway boundary.

Provider clients are built here so that callers outside ``gateway/`` can request a configured
client without importing or instantiating a Provider implementation themselves.
"""
from __future__ import annotations

from .providers import OpenAICompatibleProvider


def gateway_type() -> type:
    """The provider client type, for callers that must recognise a configured client."""
    return OpenAICompatibleProvider


def gateway_provider(provider_id: str, base_url: str, *, api_key_env: str | None = None,
                     timeout_seconds: float = 120.0) -> OpenAICompatibleProvider:
    return OpenAICompatibleProvider(provider_id, base_url, api_key_env=api_key_env,
                                    timeout_seconds=timeout_seconds)
