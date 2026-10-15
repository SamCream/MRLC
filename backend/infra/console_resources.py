from __future__ import annotations

import copy
import json
import os
import threading
import time
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener

from gateway.ccr.client import gateway_provider, gateway_type
from gateway.ccr.providers.openai_compatible import ProviderTransportError
from infra.endpoints import ccr_base_url, strata_url


def _now():
    return datetime.now(timezone.utc).isoformat()


class GatewayCatalog:
    """Read-only CCR catalog probe for console display.

    Only the unauthenticated ``/health`` endpoint and the already-configured provider list call are
    used; the console must not invent a second model source or expose credentials to browsers.
    """

    def __init__(self, provider=None, ttl=30):
        self.provider = provider or gateway_provider(
            "ccr", ccr_base_url(), api_key_env="CCR_API_KEY", timeout_seconds=3)
        self.ttl = ttl
        self.lock = threading.Lock()
        self.cached = None
        self.expires = 0

    def snapshot(self):
        with self.lock:
            now = time.monotonic()
            if self.cached is not None and now < self.expires:
                return copy.deepcopy(self.cached)
            collected_at = _now()
            result = {"source": "CCR /health + /v1/models", "collected_at": collected_at,
                      "models": [], "endpoint": self.provider.base_url}
            try:
                # CCR /health reports its own state, not an OpenAI-compatible "ok" (see Router/ccr contract).
                health = self.provider._json_request("GET", "/health")
                result["status"] = "ONLINE"
                result["gateway_status"] = str(health.get("status") or "unknown")
            except ProviderTransportError as error:
                result.update({"status": "OFFLINE", "gateway_status": None,
                               "reason": _gateway_reason(error)})
            except Exception:
                result.update({"status": "OFFLINE", "gateway_status": None,
                               "reason": "CCR 健康检查失败；检查网关进程与端点配置"})
            else:
                try:
                    result["models"] = list(self.provider.models())
                except ProviderTransportError as error:
                    result["status"] = "DEGRADED"
                    result["reason"] = _gateway_reason(error)
                except Exception:
                    result["status"] = "DEGRADED"
                    result["reason"] = "CCR 模型列表读取失败；能力仍未确认"
                if result["status"] == "ONLINE" and not result["models"]:
                    result["reason"] = "CCR 在线，但未返回任何模型"
            self.expires = now + self.ttl
            self.cached = copy.deepcopy(result)
            return copy.deepcopy(self.cached)


def _gateway_reason(error: ProviderTransportError) -> str:
    return {
        "AUTHENTICATION_FAILED": "CCR 需要有效网关凭据；配置后重试",
        "RATE_LIMITED": "CCR 限流；稍后重试",
        "TIMEOUT_BEFORE_ACCEPT": "CCR 未在超时内响应",
        "INVALID_REQUEST": "CCR 拒绝了探测请求",
    }.get(str(getattr(error, "code", "")), "CCR 不可达；检查网关进程与端点配置")


class ConsoleResources:
    def __init__(self, provider=None, ttl=10, telemetry_client=None):
        self.provider = provider or gateway_provider(
            "strata", strata_url(), api_key_env="MRLC_STRATA_API_KEY", timeout_seconds=3)
        self.ttl = ttl
        self.telemetry_client = telemetry_client
        if self.telemetry_client is None and isinstance(self.provider, gateway_type()):
            self.telemetry_client = _StrataTelemetryClient(
                self.provider.base_url, self.provider.api_key_env, self.provider.timeout_seconds)
        self.lock = threading.Lock()
        self.cached = None
        self.last_success = None
        self.expires = 0

    def snapshot(self):
        with self.lock:
            now = time.monotonic()
            if self.cached is not None and now < self.expires:
                return copy.deepcopy(self.cached)
            attempted_at = _now()
            try:
                models = list(self.provider.models())
            except Exception:
                if self.last_success is not None:
                    result = copy.deepcopy(self.last_success)
                    result.update({"status": "STALE", "last_attempt_at": attempted_at,
                                   "stale_age_seconds": round(now - self.last_success["_observed_monotonic"], 1),
                                   "reason": "Strata 不可达；显示上次成功采集的数据"})
                    result.pop("_observed_monotonic", None)
                    native = result.get("native_slots", {})
                    if native.get("status") in {"ONLINE", "DEGRADED"}:
                        native["status"] = "STALE"
                        native["reason"] = "Strata 遥测不可达；显示上次成功采集的数据"
                else:
                    result = self._unavailable("Strata 模型发现失败；检查连接、服务和服务端凭据",
                                               "OFFLINE", attempted_at)
            else:
                result = {"source": "Strata /v1/models + /slots + /metrics", "status": "ONLINE",
                          "models": models, "collected_at": attempted_at,
                          "native_slots": {"status": "UNSUPPORTED", "occupied": None, "slots": [],
                                           "queued": None, "reason": "未配置 Strata 原生槽位遥测客户端"},
                          "reason": "Strata 在线，但当前没有发现已加载模型" if not models else None}
                if self.telemetry_client is not None:
                    try:
                        result["native_slots"] = self.telemetry_client.snapshot()
                    except Exception:
                        result["native_slots"] = {"status": "OFFLINE", "occupied": None, "capacity": None,
                                                  "slots": [], "queued": None,
                                                  "reason": "Strata 原生遥测采集异常"}
                    if result["native_slots"]["status"] in {"OFFLINE", "UNAVAILABLE", "STALE"}:
                        result["status"] = "DEGRADED"
                result["_observed_monotonic"] = now
                self.last_success = copy.deepcopy(result)
            self.expires = now + self.ttl
            self.cached = copy.deepcopy(result)
            self.cached.pop("_observed_monotonic", None)
            return copy.deepcopy(self.cached)

    @staticmethod
    def _unavailable(reason, status, attempted_at):
        return {"source": "Strata /v1/models + /slots + /metrics", "status": status,
                "models": [], "collected_at": attempted_at,
                "native_slots": {"status": status, "occupied": None, "slots": [], "queued": None,
                                 "reason": reason}, "reason": reason}



def _as_count(value):
    """Telemetry counts are only trusted when they are real non-negative integers."""
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _as_number(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _as_text(value):
    return value if isinstance(value, str) and value else None


def _engine_facts(engine, live):
    """Describe the loaded engine without inventing defaults for fields it did not report."""
    engine = engine if isinstance(engine, dict) else {}
    facts = {
        "model": _as_text(engine.get("model")),
        "version": _as_text(engine.get("version")),
        "context": _as_number(engine.get("context")) or _as_number(engine.get("max_context")),
        "batch_slots": _as_count(engine.get("batch_slots")),
        "kv": _as_text(engine.get("kv")),
        "kv_resident": _as_number(engine.get("kv_resident")),
        "expert_slots": _as_count(engine.get("expert_slots")),
        "expert_cache_mib": _as_number(engine.get("expert_cache_mib")),
        "spec": _as_count(engine.get("spec")),
        "vram_free_mib": _as_number(engine.get("vram_free_mib")),
        "arena_mib": _as_number(engine.get("arena_mib")),
        "pool_workers": _as_count(engine.get("pool_workers")),
        "images": engine.get("images") if isinstance(engine.get("images"), bool) else None,
    }
    parallel = _as_count(live.get("parallel"))
    if parallel is not None:
        facts["parallel"] = parallel
    return facts if any(value is not None for value in facts.values()) else None


def _live_progress(live, queued):
    """In-flight generation progress; every field stays None when the backend is not reporting."""
    return {
        "state": _as_text(live.get("state")),
        "phase": _as_text(live.get("phase")),
        "prompt_tokens": _as_count(live.get("prompt_tokens")),
        "prompt_read": _as_count(live.get("prompt_read")),
        "generated": _as_count(live.get("generated")),
        "max_tokens": _as_count(live.get("max_tokens")),
        "elapsed_s": _as_number(live.get("elapsed_s")),
        "tok_s": _as_number(live.get("tok_s")),
        "tok_s_mean": _as_number(live.get("tok_s_mean")),
        "prefill_tok_s_mean": _as_number(live.get("prefill_tok_s_mean")),
        "waiting": _as_count(live.get("waiting")),
        "queued": _as_count(queued),
    }


def _hardware(hardware, hardware_static):
    """Shared host/GPU telemetry. Nothing here belongs to one logical slot."""
    hardware = hardware if isinstance(hardware, dict) else {}
    hardware_static = hardware_static if isinstance(hardware_static, dict) else {}
    facts = {
        "gpu_name": _as_text(hardware_static.get("gpu_name")),
        "gpu_count": _as_count(hardware_static.get("gpu_count")),
        "cpu_name": _as_text(hardware_static.get("cpu_name")),
        "cores": _as_count(hardware_static.get("cores")),
        "threads": _as_count(hardware_static.get("threads")),
        "gpu_util": _as_number(hardware.get("gpu_util")),
        "gpu_mem_used": _as_number(hardware.get("gpu_mem_used")),
        "gpu_mem_total": _as_number(hardware.get("gpu_mem_total")),
        "gpu_temp": _as_number(hardware.get("gpu_temp")),
        "gpu_power": _as_number(hardware.get("gpu_power")),
        "gpu_power_limit": _as_number(hardware.get("gpu_power_limit")),
        "gpu_pcie_rx_mb": _as_number(hardware.get("gpu_pcie_rx_mb")),
        "gpu_pcie_tx_mb": _as_number(hardware.get("gpu_pcie_tx_mb")),
        "cpu": _as_number(hardware.get("cpu")),
        "ram_used": _as_number(hardware.get("ram_used")),
        "ram_total": _as_number(hardware.get("ram_total")),
        "disk_read_mb": _as_number(hardware.get("disk_read_mb")),
        "disk_write_mb": _as_number(hardware.get("disk_write_mb")),
    }
    return facts if any(value is not None for value in facts.values()) else None


def _history(history):
    """Bounded real sample series. A series with fewer than two points is not a trend and is
    dropped, so the UI can never draw a curve that the backend did not actually record."""
    history = history if isinstance(history, dict) else {}
    series = {}
    for key, values in history.items():
        if not isinstance(values, list):
            continue
        points = [_as_number(value) for value in values]
        points = [point for point in points if point is not None]
        if len(points) >= 2:
            series[key] = points
    return series


def _totals(totals):
    totals = totals if isinstance(totals, dict) else {}
    return {
        "since": _as_number(totals.get("since")),
        "requests": _as_count(totals.get("requests")),
        "prompt_tokens": _as_count(totals.get("prompt_tokens")),
        "reused": _as_count(totals.get("reused")),
        "output_tokens": _as_count(totals.get("output_tokens")),
        "prompt_ms": _as_number(totals.get("prompt_ms")),
        "decode_ms": _as_number(totals.get("decode_ms")),
    }


def _recent_requests(requests, limit=20):
    """Real finished-request history: used for the trend sparkline, never for fabricated points."""
    if not isinstance(requests, list):
        return []
    rows = []
    for entry in requests[:limit]:
        if not isinstance(entry, dict):
            continue
        rows.append({
            "time": _as_number(entry.get("time")),
            "duration_s": _as_number(entry.get("duration_s")),
            "prompt_tokens": _as_count(entry.get("prompt_tokens")),
            "output_tokens": _as_count(entry.get("output_tokens")),
            "decode_tok_s": _as_number(entry.get("decode_tok_s")),
            "prefill_ms": _as_number(entry.get("prompt_ms")),
            "hit_rate": _as_number(entry.get("hit_rate")),
            "finish": _as_text(entry.get("finish")),
            "drafts_offered": _as_count(entry.get("drafts_offered")),
            "drafts_accepted": _as_count(entry.get("drafts_accepted")),
        })
    return rows


class _StrataTelemetryClient:
    def __init__(self, base_url, api_key_env, timeout_seconds):
        self.base_url = base_url.rstrip("/")
        self.api_key_env = api_key_env
        self.timeout_seconds = timeout_seconds
        self.opener = build_opener(ProxyHandler({}))
        self.last_success = None

    def _get(self, path):
        headers = {"Accept": "application/json"}
        if self.api_key_env and os.environ.get(self.api_key_env):
            headers["Authorization"] = "Bearer " + os.environ[self.api_key_env]
        request = Request(self.base_url + path, headers=headers)
        with self.opener.open(request, timeout=self.timeout_seconds) as response:
            return json.loads(response.read())

    def snapshot(self):
        collected_at = _now()
        slots_payload = None
        metrics_payload = None
        errors = []
        unsupported = []
        inaccessible = []
        for path in ("/slots", "/metrics"):
            try:
                payload = self._get(path)
                if path == "/slots":
                    if not isinstance(payload, list) or any(not isinstance(slot, dict) for slot in payload):
                        errors.append("/slots returned an invalid response")
                    else:
                        slots_payload = payload
                elif isinstance(payload, dict):
                    metrics_payload = payload
                else:
                    errors.append("/metrics returned an invalid response")
            except HTTPError as error:
                if error.code == 404:
                    unsupported.append(path)
                elif error.code in {401, 403}:
                    inaccessible.append(path)
                else:
                    errors.append(f"{path} returned HTTP {error.code}")
            except (URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError):
                errors.append(f"{path} could not be collected")

        result = {"status": "UNKNOWN", "occupied": None, "capacity": None, "slots": [], "queued": None,
                  "running_requests": None, "outside_slots": None,
                  "collected_at": collected_at, "metrics_collected_at": None, "metrics_state": None,
                  "engine": None, "progress": None, "requests": [],
                  "hardware": None, "history": {}, "totals": None, "requests_kept": None,
                  "reason": None}
        live = metrics_payload.get("live") if metrics_payload else None
        if not isinstance(live, dict):
            live = {}
        if slots_payload is not None:
            result["slots"] = slots_payload
            flags = [slot.get("is_processing") for slot in slots_payload]
            if all(isinstance(flag, bool) for flag in flags):
                result["occupied"] = sum(flags)
                result["capacity"] = len(slots_payload)
        if metrics_payload is not None:
            states = live.get("slots")
            queued = live.get("queued", live.get("waiting"))
            result["queued"] = queued if isinstance(queued, int) and not isinstance(queued, bool) and queued >= 0 else None
            running = live.get("running")
            result["running_requests"] = running if isinstance(running, int) and not isinstance(running, bool) and running >= 0 else None
            outside = live.get("outside_slots")
            result["outside_slots"] = outside if isinstance(outside, int) and not isinstance(outside, bool) and outside >= 0 else None
            if isinstance(states, list) and all(isinstance(slot, dict) and isinstance(slot.get("state"), str)
                                                for slot in states):
                result["slots"] = states
                result["occupied"] = sum(slot["state"] != "idle" for slot in states)
                parallel = live.get("parallel")
                result["capacity"] = (parallel if isinstance(parallel, int) and not isinstance(parallel, bool)
                                       and parallel >= 0 else len(states))
            result["metrics_state"] = live.get("state") if isinstance(live.get("state"), str) else None
            result["metrics_collected_at"] = metrics_payload.get("time")
            result["engine"] = _engine_facts(metrics_payload.get("engine"), live)
            result["progress"] = _live_progress(live, result["queued"])
            result["requests"] = _recent_requests(metrics_payload.get("requests"))
            result["hardware"] = _hardware(metrics_payload.get("hardware"),
                                          metrics_payload.get("hardware_static"))
            result["history"] = _history(metrics_payload.get("history"))
            result["totals"] = _totals(metrics_payload.get("totals"))
            result["requests_kept"] = _as_count(metrics_payload.get("requests_kept"))

        if slots_payload is not None or metrics_payload is not None:
            result["status"] = "DEGRADED" if errors or inaccessible or unsupported else "ONLINE"
            if result["occupied"] is None:
                result["status"] = "DEGRADED"
                result["reason"] = "Strata 返回遥测，但无法确认槽位占用"
            elif errors or inaccessible:
                result["reason"] = "部分 Strata 遥测不可读取；显示可确认的字段"
            elif unsupported:
                result["reason"] = "部分 Strata 遥测接口不支持；显示可确认的字段"
            if result["status"] in {"ONLINE", "DEGRADED"} and result["occupied"] is not None:
                result["_observed_monotonic"] = time.monotonic()
                self.last_success = copy.deepcopy(result)
        elif len(unsupported) == 2:
            result.update({"status": "UNSUPPORTED", "reason": "Strata 未提供 /slots 或 /metrics 遥测接口"})
        elif inaccessible:
            result.update({"status": "UNAVAILABLE", "reason": "Strata 遥测权限不足"})
        else:
            result.update({"status": "OFFLINE", "reason": "Strata 遥测暂不可用"})

        if result["status"] == "OFFLINE" and self.last_success is not None:
            stale = copy.deepcopy(self.last_success)
            stale.update({"status": "STALE", "source_status": "OFFLINE", "last_attempt_at": collected_at,
                          "stale_age_seconds": round(time.monotonic() - stale["_observed_monotonic"], 1),
                          "reason": "Strata 遥测不可达；显示上次成功采集的数据"})
            stale.pop("_observed_monotonic", None)
            return stale
        result.pop("_observed_monotonic", None)
        return result
