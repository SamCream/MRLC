"""Loopback budget meter between a Worker and CCR.

Each DELEGATE run gets an unguessable path prefix. Before a model call is forwarded the meter
checks ``used + estimate <= budget``; otherwise it answers 402 ``BUDGET_REJECTED`` and nothing
reaches the provider. Calls already forwarded finish and are metered from the provider usage.
The meter injects the CCR credential; adapters do not pass it to the Child environment.
"""
from __future__ import annotations

import http.client
import json
import math
import secrets
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

HOP_HEADERS = {"connection", "keep-alive", "transfer-encoding", "te", "trailer", "upgrade",
               "proxy-authorization", "proxy-authenticate", "host", "content-length", "authorization",
               "accept-encoding"}


@dataclass
class RunBudget:
    budget: int
    used_input: int = 0
    used_output: int = 0
    estimated: int = 0
    calls: int = 0
    unobserved_calls: int = 0
    rejected_calls: int = 0
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    @property
    def used(self) -> int:
        return self.used_input + self.used_output

    def snapshot(self) -> dict:
        with self.lock:
            return {"budget": self.budget, "input": self.used_input, "output": self.used_output,
                    "total": self.used, "estimated": self.estimated, "calls": self.calls,
                    "unobserved_calls": self.unobserved_calls, "rejected_calls": self.rejected_calls,
                    "observed": self.calls > 0 and self.unobserved_calls == 0}


def estimate_tokens(body: bytes) -> int:
    """Conservative pre-call estimate: about four bytes of request per input token."""
    return max(1, math.ceil(len(body) / 4))


def extract_usage(content_type: str, data: bytes) -> tuple[int, int] | None:
    """Usage from a Responses/Chat completion body, streamed (SSE) or not."""
    if "text/event-stream" in content_type:
        found = None
        for line in data.decode("utf-8", "replace").splitlines():
            if not line.startswith("data:"):
                continue
            try:
                item = json.loads(line[5:].strip())
            except json.JSONDecodeError:
                continue
            usage = _usage_of(item)
            if usage is not None:
                found = usage
        return found
    try:
        return _usage_of(json.loads(data or b"{}"))
    except json.JSONDecodeError:
        return None


def _usage_of(item) -> tuple[int, int] | None:
    if not isinstance(item, dict):
        return None
    usage = item.get("usage")
    if usage is None and isinstance(item.get("response"), dict):
        usage = item["response"].get("usage")
    if not isinstance(usage, dict):
        return None
    if "input_tokens" in usage or "output_tokens" in usage:
        return int(usage.get("input_tokens") or 0), int(usage.get("output_tokens") or 0)
    if "prompt_tokens" in usage or "completion_tokens" in usage:
        return int(usage.get("prompt_tokens") or 0), int(usage.get("completion_tokens") or 0)
    return None


class BudgetMeter:
    def __init__(self, upstream: str, api_key: str | None, *, timeout: float = 600) -> None:
        parts = urlsplit(upstream.rstrip("/"))
        if parts.scheme not in {"http", "https"} or not parts.hostname:
            raise ValueError("budget meter upstream must be an http(s) URL")
        self._upstream = parts
        self._api_key = api_key
        self._timeout = timeout
        self._runs: dict[str, RunBudget] = {}
        self._lock = threading.Lock()
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self._server.daemon_threads = True
        self._thread = threading.Thread(target=self._server.serve_forever, name="budget-meter", daemon=True)
        self._thread.start()

    @property
    def port(self) -> int:
        return self._server.server_address[1]

    def open_run(self, budget: int) -> str:
        """Register a run and return its private base URL (ending in /v1)."""
        if type(budget) is not int or budget < 1:
            raise ValueError("budget must be a positive integer")
        token = secrets.token_urlsafe(24)
        with self._lock:
            self._runs[token] = RunBudget(budget)
        return f"http://127.0.0.1:{self.port}/run/{token}/v1"

    def close_run(self, base_url: str) -> dict:
        token = self._token(base_url)
        with self._lock:
            run = self._runs.pop(token)
        return run.snapshot()

    def usage(self, base_url: str) -> dict:
        with self._lock:
            run = self._runs[self._token(base_url)]
        return run.snapshot()

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()

    @staticmethod
    def _token(base_url: str) -> str:
        return urlsplit(base_url).path.split("/")[2]

    def _handler(self):
        meter = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                pass

            def do_GET(self):
                meter._forward(self, b"", metered=False)

            def do_POST(self):
                length = int(self.headers.get("content-length") or 0)
                meter._forward(self, self.rfile.read(length) if length else b"", metered=True)

        return Handler

    def _reply(self, handler, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode()
        handler.send_response(status)
        handler.send_header("content-type", "application/json")
        handler.send_header("content-length", str(len(body)))
        handler.end_headers()
        handler.wfile.write(body)

    def _forward(self, handler, body: bytes, *, metered: bool) -> None:
        segments = handler.path.split("/", 3)
        with self._lock:
            run = self._runs.get(segments[2]) if len(segments) > 3 and segments[1] == "run" else None
        if run is None:
            self._reply(handler, 404, {"error": {"message": "unknown budget run", "code": "NOT_FOUND"}})
            return
        estimate = estimate_tokens(body) if metered else 0
        if metered:
            with run.lock:
                if run.used + estimate > run.budget:
                    run.rejected_calls += 1
                    used = run.used
                    rejected = True
                else:
                    run.estimated += estimate
                    run.calls += 1
                    rejected = False
            if rejected:
                # Hard budget: the provider is never contacted for this call.
                self._reply(handler, 402, {"error": {
                    "type": "budget_rejected", "code": "BUDGET_REJECTED",
                    "message": f"run budget {run.budget} tokens; used {used}, next call estimated {estimate}"}})
                return
        path = (self._upstream.path or "") + "/" + segments[3]
        headers = {k: v for k, v in handler.headers.items() if k.lower() not in HOP_HEADERS}
        headers["accept-encoding"] = "identity"
        if self._api_key:
            headers["authorization"] = "Bearer " + self._api_key
        if body:
            headers["content-length"] = str(len(body))
        connection_type = (http.client.HTTPSConnection if self._upstream.scheme == "https"
                           else http.client.HTTPConnection)
        upstream = connection_type(self._upstream.hostname, self._upstream.port, timeout=self._timeout)
        captured = bytearray()
        responded = False
        try:
            upstream.request(handler.command, path, body=body or None, headers=headers)
            response = upstream.getresponse()
            responded = True
            handler.send_response(response.status)
            for name, value in response.getheaders():
                if name.lower() not in HOP_HEADERS:
                    handler.send_header(name, value)
            handler.send_header("transfer-encoding", "chunked")
            handler.end_headers()
            while True:
                chunk = response.read1(65536)
                if not chunk:
                    break
                if metered:
                    captured.extend(chunk)
                handler.wfile.write(b"%x\r\n%s\r\n" % (len(chunk), chunk))
                handler.wfile.flush()
            handler.wfile.write(b"0\r\n\r\n")
            content_type = response.getheader("content-type") or ""
        except (OSError, http.client.HTTPException):
            content_type = ""
            if not responded:
                self._reply(handler, 502, {"error": {"message": "gateway unreachable", "code": "PROVIDER_UNAVAILABLE"}})
            handler.close_connection = True
        finally:
            upstream.close()
        if metered:
            usage = extract_usage(content_type, bytes(captured)) if captured else None
            with run.lock:
                if usage is None:
                    # Possibly billed but unmeasured: charge the estimate so the cap stays conservative.
                    run.unobserved_calls += 1
                    run.used_input += estimate
                else:
                    run.used_input += usage[0]
                    run.used_output += usage[1]
