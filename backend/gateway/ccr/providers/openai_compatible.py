from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import json
import os
from typing import Any, Iterator
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener


@dataclass(frozen=True, slots=True)
class ProviderRequest:
    invocation_id: str
    model: str
    messages: list[dict[str, str]]
    max_tokens: int = 256
    temperature: float = 0.0


@dataclass(frozen=True, slots=True)
class ProviderResponse:
    invocation_id: str
    provider_request_id: str
    model: str
    content: str
    reasoning_content: str | None
    finish_reason: str
    usage_input: int
    usage_output: int
    usage_total: int


@dataclass(frozen=True, slots=True)
class ProviderStreamEvent:
    invocation_id: str
    provider_request_id: str
    model: str
    content_delta: str = ""
    reasoning_delta: str = ""
    finish_reason: str | None = None
    usage_input: int = 0
    usage_output: int = 0
    usage_total: int = 0


class ProviderErrorCode(StrEnum):
    AUTHENTICATION_FAILED = "AUTHENTICATION_FAILED"
    INVALID_REQUEST = "INVALID_REQUEST"
    PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
    RATE_LIMITED = "RATE_LIMITED"
    TIMEOUT_BEFORE_ACCEPT = "TIMEOUT_BEFORE_ACCEPT"


class ProviderTransportError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        code: ProviderErrorCode = ProviderErrorCode.PROVIDER_UNAVAILABLE,
        retryable: bool = True,
        retry_after_seconds: int | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.retry_after_seconds = retry_after_seconds


class OpenAICompatibleProvider:
    def __init__(
        self,
        provider_id: str,
        base_url: str,
        *,
        api_key_env: str | None = None,
        timeout_seconds: float = 120.0,
    ) -> None:
        self.provider_id = provider_id
        self.base_url = base_url.rstrip("/")
        self.api_key_env = api_key_env
        self.timeout_seconds = timeout_seconds
        self._opener = build_opener(ProxyHandler({}))

    def health(self) -> dict[str, Any]:
        payload = self._json_request("GET", "/health")
        if payload.get("status") != "ok":
            raise ProviderTransportError(f"provider health is not ok: {self.provider_id}")
        return payload

    def models(self) -> tuple[str, ...]:
        payload = self._json_request("GET", "/v1/models")
        return tuple(str(item["id"]) for item in payload.get("data", []) if item.get("id"))

    def invoke(self, request: ProviderRequest) -> ProviderResponse:
        payload = self._json_request(
            "POST",
            "/v1/chat/completions",
            {
                "model": request.model,
                "messages": request.messages,
                "max_tokens": request.max_tokens,
                "temperature": request.temperature,
                "stream": False,
            },
        )
        choice = payload["choices"][0]
        message = choice.get("message", {})
        usage = payload.get("usage", {})
        return ProviderResponse(
            invocation_id=request.invocation_id,
            provider_request_id=str(payload.get("id", request.invocation_id)),
            model=str(payload.get("model", request.model)),
            content=str(message.get("content") or ""),
            reasoning_content=(None if message.get("reasoning_content") is None else str(message["reasoning_content"])),
            finish_reason=str(choice.get("finish_reason") or "unknown"),
            usage_input=int(usage.get("prompt_tokens", 0)),
            usage_output=int(usage.get("completion_tokens", 0)),
            usage_total=int(usage.get("total_tokens", 0)),
        )

    def stream(self, request: ProviderRequest) -> Iterator[ProviderStreamEvent]:
        http_request = self._request(
            "POST",
            "/v1/chat/completions",
            {
                "model": request.model,
                "messages": request.messages,
                "max_tokens": request.max_tokens,
                "temperature": request.temperature,
                "stream": True,
            },
        )
        terminal_event_seen = False
        try:
            with self._opener.open(http_request, timeout=self.timeout_seconds) as response:
                for raw_line in response:
                    line = raw_line.decode("utf-8").strip()
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    payload = json.loads(data)
                    choice = payload.get("choices", [{}])[0]
                    delta = choice.get("delta", {})
                    usage = payload.get("usage", {})
                    if choice.get("finish_reason") is not None:
                        terminal_event_seen = True
                    yield ProviderStreamEvent(
                        invocation_id=request.invocation_id,
                        provider_request_id=str(payload.get("id", request.invocation_id)),
                        model=str(payload.get("model", request.model)),
                        content_delta=str(delta.get("content") or ""),
                        reasoning_delta=str(delta.get("reasoning_content") or ""),
                        finish_reason=choice.get("finish_reason"),
                        usage_input=int(usage.get("prompt_tokens", 0)),
                        usage_output=int(usage.get("completion_tokens", 0)),
                        usage_total=int(usage.get("total_tokens", 0)),
                    )
            if not terminal_event_seen:
                raise ProviderTransportError(
                    f"provider stream ended without a terminal event: {self.provider_id}",
                    code=ProviderErrorCode.PROVIDER_UNAVAILABLE,
                )
        except HTTPError as error:
            raise self._http_error(error, "stream") from error
        except (TimeoutError, OSError) as error:
            raise ProviderTransportError(
                f"provider stream timed out: {self.provider_id}: {error}",
                code=ProviderErrorCode.TIMEOUT_BEFORE_ACCEPT,
            ) from error
        except URLError as error:
            raise ProviderTransportError(f"provider stream failed: {self.provider_id}: {error}") from error

    def _json_request(
        self, method: str, path: str, payload: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        try:
            with self._opener.open(
                self._request(method, path, payload), timeout=self.timeout_seconds
            ) as response:
                result = json.loads(response.read())
        except HTTPError as error:
            raise self._http_error(error, "request") from error
        except (TimeoutError, OSError) as error:
            raise ProviderTransportError(
                f"provider request timed out: {self.provider_id}: {error}",
                code=ProviderErrorCode.TIMEOUT_BEFORE_ACCEPT,
            ) from error
        except URLError as error:
            raise ProviderTransportError(f"provider request failed: {self.provider_id}: {error}") from error
        except json.JSONDecodeError as error:
            raise ProviderTransportError(
                f"provider returned invalid JSON: {self.provider_id}",
                code=ProviderErrorCode.INVALID_REQUEST,
                retryable=False,
            ) from error
        if not isinstance(result, dict):
            raise ProviderTransportError(f"provider returned a non-object: {self.provider_id}")
        return result

    def _request(
        self, method: str, path: str, payload: dict[str, Any] | None = None
    ) -> Request:
        headers = {"Accept": "application/json"}
        if payload is not None:
            headers["Content-Type"] = "application/json"
        if self.api_key_env:
            api_key = os.environ.get(self.api_key_env)
            if not api_key:
                raise ProviderTransportError(f"missing credential reference: {self.api_key_env}")
            headers["Authorization"] = f"Bearer {api_key}"
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        return Request(f"{self.base_url}{path}", data=body, headers=headers, method=method)

    def _http_error(self, error: HTTPError, operation: str) -> ProviderTransportError:
        if error.code == 429:
            retry_after = error.headers.get("Retry-After")
            return ProviderTransportError(
                f"provider {operation} rate limited: {self.provider_id}",
                code=ProviderErrorCode.RATE_LIMITED,
                retry_after_seconds=(int(retry_after) if retry_after and retry_after.isdigit() else None),
            )
        if error.code in {401, 403}:
            return ProviderTransportError(
                f"provider {operation} authentication failed: {self.provider_id}",
                code=ProviderErrorCode.AUTHENTICATION_FAILED,
                retryable=False,
            )
        if 400 <= error.code < 500:
            return ProviderTransportError(
                f"provider {operation} rejected request: {self.provider_id}: HTTP {error.code}",
                code=ProviderErrorCode.INVALID_REQUEST,
                retryable=False,
            )
        return ProviderTransportError(
            f"provider {operation} unavailable: {self.provider_id}: HTTP {error.code}"
        )
