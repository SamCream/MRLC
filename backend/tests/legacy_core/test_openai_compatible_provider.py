from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
import time

import pytest

from gateway.ccr.providers.openai_compatible import (
    OpenAICompatibleProvider,
    ProviderErrorCode,
    ProviderRequest,
    ProviderTransportError,
)


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path == "/health":
            self._json({"status": "ok", "model": "local-176b", "loaded": True})
            return
        if self.path == "/v1/models":
            self._json({"object": "list", "data": [{"id": "local-176b"}]})
            return
        self.send_error(404)

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        request = json.loads(self.rfile.read(length))
        content = request.get("messages", [{}])[-1].get("content")
        if content == "rate-limit":
            self.send_response(429)
            self.send_header("Content-Type", "application/json")
            self.send_header("Retry-After", "2")
            self.end_headers()
            self.wfile.write(b'{"error":{"message":"limited"}}')
            return
        if content == "timeout":
            time.sleep(0.2)
            return
        if request.get("stream"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            if content == "stream-cut":
                payload = {
                    "id": "req-cut",
                    "model": "local-176b",
                    "choices": [{"delta": {"content": "PARTIAL"}, "finish_reason": None}],
                }
                self.wfile.write(f"data: {json.dumps(payload)}\n\n".encode())
                self.wfile.flush()
                self.close_connection = True
                return
            for payload in (
                {"id": "req-stream", "model": "local-176b", "choices": [{"delta": {"content": "LOCAL"}, "finish_reason": None}]},
                {"id": "req-stream", "model": "local-176b", "choices": [{"delta": {"content": "_OK"}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5}},
            ):
                self.wfile.write(f"data: {json.dumps(payload)}\n\n".encode())
            self.wfile.write(b"data: [DONE]\n\n")
            return
        self._json(
            {
                "id": "req-1",
                "model": request["model"],
                "choices": [{"message": {"content": "LOCAL_OK"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 4, "completion_tokens": 2, "total_tokens": 6},
            }
        )

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def _json(self, payload: dict[str, object]) -> None:
        encoded = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


def _server() -> tuple[ThreadingHTTPServer, str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    Thread(target=server.serve_forever, daemon=True).start()
    host, port = server.server_address
    return server, f"http://{host}:{port}"


def test_provider_discovers_and_invokes_local_model() -> None:
    server, base_url = _server()
    try:
        provider = OpenAICompatibleProvider("strata", base_url, timeout_seconds=5)

        assert provider.health()["loaded"] is True
        assert provider.models() == ("local-176b",)
        result = provider.invoke(
            ProviderRequest("inv-1", "local-176b", [{"role": "user", "content": "reply"}])
        )

        assert result.provider_request_id == "req-1"
        assert result.content == "LOCAL_OK"
        assert result.finish_reason == "stop"
        assert result.usage_total == 6
    finally:
        server.shutdown()


def test_provider_normalizes_stream_events_and_usage() -> None:
    server, base_url = _server()
    try:
        provider = OpenAICompatibleProvider("strata", base_url, timeout_seconds=5)
        events = tuple(
            provider.stream(
                ProviderRequest("inv-stream", "local-176b", [{"role": "user", "content": "reply"}])
            )
        )

        assert "".join(event.content_delta for event in events) == "LOCAL_OK"
        assert events[-1].finish_reason == "stop"
        assert events[-1].usage_total == 5
    finally:
        server.shutdown()


def test_provider_rejects_stream_without_terminal_event() -> None:
    server, base_url = _server()
    try:
        provider = OpenAICompatibleProvider("strata", base_url, timeout_seconds=5)
        stream = provider.stream(
            ProviderRequest(
                "inv-cut",
                "local-176b",
                [{"role": "user", "content": "stream-cut"}],
            )
        )

        assert next(stream).content_delta == "PARTIAL"
        with pytest.raises(ProviderTransportError, match="without a terminal event"):
            next(stream)
    finally:
        server.shutdown()


def test_provider_classifies_rate_limit_with_retry_after() -> None:
    server, base_url = _server()
    try:
        provider = OpenAICompatibleProvider("strata", base_url, timeout_seconds=5)
        with pytest.raises(ProviderTransportError) as raised:
            provider.invoke(
                ProviderRequest("inv-rate", "local-176b", [{"role": "user", "content": "rate-limit"}])
            )

        assert raised.value.code is ProviderErrorCode.RATE_LIMITED
        assert raised.value.retry_after_seconds == 2
        assert raised.value.retryable is True
    finally:
        server.shutdown()


def test_provider_classifies_timeout_before_accept() -> None:
    server, base_url = _server()
    try:
        provider = OpenAICompatibleProvider("strata", base_url, timeout_seconds=0.05)
        with pytest.raises(ProviderTransportError) as raised:
            provider.invoke(
                ProviderRequest("inv-timeout", "local-176b", [{"role": "user", "content": "timeout"}])
            )

        assert raised.value.code is ProviderErrorCode.TIMEOUT_BEFORE_ACCEPT
        assert raised.value.retryable is True
    finally:
        server.shutdown()
