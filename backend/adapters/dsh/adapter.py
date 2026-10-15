from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import threading
import urllib.error
import urllib.request
from dataclasses import replace
from pathlib import Path
from typing import Any

from adapters.contract import (
    AdapterResult,
    AttachResult,
    CancelResult,
    DispatchReceipt,
    ParkResult,
    ReturnPackage,
    StandardWorkerEvent,
    TaskPackage,
    TestResult,
    WorkerHealth,
    WorkerCapabilities,
    WorkerSessionSpec,
    worker_spec_from_dict,
    worker_spec_to_dict,
)
from adapters.process_io import capture_file_process, capture_process
from infra.worker_runtime_state import WorkerRunStore
from infra.audit.redaction import redact_value


class DshAdapter:
    def __init__(
        self,
        binary: str | Path,
        *,
        ccr_base_url: str,
        ccr_token: str | None = None,
        infra_base_url: str | None = None,
        state_store: WorkerRunStore | None = None,
        timeout_seconds: float = 300,
    ) -> None:
        self._binary = str(binary)
        self._timeout = timeout_seconds
        self._ccr_base_url = ccr_base_url.rstrip("/")
        self._ccr_token = ccr_token
        self._infra_base_url = None if infra_base_url is None else infra_base_url.rstrip("/")
        self._state_store = state_store
        self._threads: dict[str, threading.Thread] = {}
        self._results: dict[str, AdapterResult] = {}
        self._errors: dict[str, BaseException] = {}

    def capabilities(self) -> WorkerCapabilities:
        return WorkerCapabilities(
            "dsh", ("read-only", "workspace-write", "danger-full-access")
        )

    def start(self, spec: WorkerSessionSpec, task: TaskPackage | None = None) -> str:
        if self._state_store is None:
            raise RuntimeError("worker state store is not configured")
        handle = f"dsh:{spec.session_id}:{spec.task_id}"
        if task is None:
            self._store().register_session(
                "dsh", spec, handle, {"spec": worker_spec_to_dict(spec)}
            )
            return handle
        self._start_thread(handle, spec, task)
        return handle

    def send_task(
        self, handle: str, task: TaskPackage, *, fencing_token: int
    ) -> DispatchReceipt:
        metadata = self._store().metadata(handle, fencing_token)
        spec_value = metadata.get("spec")
        if not isinstance(spec_value, dict):
            raise RuntimeError(f"worker session {handle} has no persisted spec")
        spec = worker_spec_from_dict(spec_value)
        cursor = self._store().attach(handle, fencing_token).last_sequence
        spec = replace(spec, event_cursor=cursor)
        self._start_thread(
            handle, spec, task, existing_handle=self._store().native_handle(handle, fencing_token)
        )
        return DispatchReceipt(handle, True, cursor)

    def _start_thread(
        self, handle: str, spec: WorkerSessionSpec, task: TaskPackage,
        *, existing_handle: str | None = None,
    ) -> None:
        thread = threading.Thread(
            target=self._run_managed, args=(handle, spec, task, existing_handle), daemon=True
        )
        self._threads[handle] = thread
        thread.start()

    def wait(self, handle: str, *, timeout: float | None = None) -> AdapterResult:
        thread = self._threads.get(handle)
        if thread is None:
            raise KeyError(handle)
        thread.join(timeout)
        if thread.is_alive():
            raise TimeoutError(handle)
        if handle in self._errors:
            raise self._errors[handle]
        return self._results[handle]

    def _run_managed(
        self, handle: str, spec: WorkerSessionSpec, task: TaskPackage,
        existing_handle: str | None = None,
    ) -> None:
        try:
            result = self.run(
                spec, task, existing_handle=existing_handle, _managed_handle=handle
            )
            attached = self._store().attach(handle, spec.lease_token)
            if attached.status == "CANCELLED":
                result = AdapterResult(
                    handle,
                    self._store().events(handle, 0, spec.lease_token),
                    self._store().collect_result(handle, spec.lease_token),
                )
            self._results[handle] = result
        except BaseException as error:
            self._errors[handle] = error

    def run(
        self,
        spec: WorkerSessionSpec,
        task: TaskPackage,
        *,
        existing_handle: str | None = None,
        _managed_handle: str | None = None,
    ) -> AdapterResult:
        before = self._files(spec.cwd)
        artifact_dir = Path(tempfile.mkdtemp(prefix="mrlc-dsh-adapter."))
        log_path = artifact_dir / "events.jsonl"
        diagnostics_path = artifact_dir / "diagnostics.log"
        patch_path = artifact_dir / "ccr.patch.yml"
        patch_path.write_text(self._ccr_patch(spec.gateway_url))
        if spec.model_binding != "router-managed":
            try:
                self._bind_model(spec)
            except (OSError, urllib.error.HTTPError, urllib.error.URLError, ValueError) as error:
                diagnostics_path.write_text(redact_value(str(error)))
                event = self._event(
                    spec,
                    1,
                    "FAILED",
                    {"phase": "model_binding", "diagnostics_ref": str(diagnostics_path)},
                )
                package = ReturnPackage("FAILED", str(error), (), (), str(log_path))
                return AdapterResult("", (event,), package)
        command = [
            self._binary,
            "--profile",
            "headless",
            "--patch",
            str(patch_path),
            "--json",
        ]
        if existing_handle is not None:
            command.extend(["--session-id", existing_handle])
        command.append(redact_value(self._prompt(spec, task)))
        environment = os.environ.copy()
        environment["CCR_BASE_URL"] = self._ccr_base_url
        environment["DSH_CCR_LLM_ADAPTER"] = "pi-ai"
        environment["CCR_CLIENT"] = "dsh"
        environment["CCR_SESSION_ID"] = spec.session_id
        environment["MRLC_PROJECT_ID"] = spec.project_id
        environment["MRLC_INFRA_AUTHORITY"] = "required"
        environment["DSH_PERMISSION_MODE"] = self._permission_mode(spec.permissions)
        if self._infra_base_url is not None:
            environment["MRLC_INFRA_BASE_URL"] = self._infra_base_url
        if spec.gateway_url is not None:
            # The budget meter injects the credential; the Child only sees its private meter URL.
            environment["CCR_BASE_URL"] = spec.gateway_url.removesuffix("/v1")
            environment.pop("CCR_TOKEN", None)
            environment["CCR_API_KEY"] = "mrlc-budget-meter"
        elif self._ccr_token is not None:
            environment["CCR_TOKEN"] = self._ccr_token
            environment["CCR_API_KEY"] = self._ccr_token
        if _managed_handle is None:
            process = subprocess.Popen(
                command, cwd=spec.cwd, env=environment,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            )
            captured = capture_process(process, timeout=self._timeout)
        else:
            stdout_writer = log_path.open("w")
            stderr_writer = diagnostics_path.open("w")
            process = subprocess.Popen(
                command, cwd=spec.cwd, env=environment,
                stdout=stdout_writer, stderr=stderr_writer, text=True,
                start_new_session=True,
            )
            stdout_writer.close()
            stderr_writer.close()
            self._store().register_process(
                "dsh", spec, _managed_handle, process.pid,
                {
                    "log_path": str(log_path), "diagnostics_path": str(diagnostics_path),
                    "cwd": str(spec.cwd), "before_files": sorted(before),
                    "spec": worker_spec_to_dict(spec),
                    "process_group_id": process.pid,
                },
            )
            captured = capture_file_process(
                process, timeout=self._timeout, stdout_path=log_path, stderr_path=diagnostics_path,
                on_stdout_line=lambda index, line: self._observe_line(
                    _managed_handle, spec.lease_token, spec.event_cursor, index, line
                ),
            )
        stdout = captured.stdout
        stderr = captured.stderr
        returncode = captured.returncode
        if _managed_handle is not None:
            attached = self._store().attach(_managed_handle, spec.lease_token)
            if attached.status == "CANCELLED":
                return AdapterResult(
                    _managed_handle,
                    self._store().events(_managed_handle, 0, spec.lease_token),
                    self._store().collect_result(_managed_handle, spec.lease_token),
                )
        if _managed_handle is None:
            log_path.write_text(redact_value(stdout))
            diagnostics_path.write_text(redact_value(stderr))
        events, handle, final_text = self._normalize(stdout, spec)
        after = self._files(spec.cwd)
        changed_files = tuple(sorted(after - before))
        payload = redact_value(self._read_final(final_text))
        tests = tuple(
            TestResult(str(item.get("command", "")), bool(item.get("passed")), str(item.get("output", "")))
            for item in payload.get("tests", [])
            if isinstance(item, dict)
        )
        passed_commands = {item.command for item in tests if item.passed}
        accepted = returncode == 0 and all(command in passed_commands for command in task.acceptance_commands)
        blocked = _managed_handle is not None and self._store().has_event_type_after(
            _managed_handle, spec.lease_token, "TASK_BLOCKED", spec.event_cursor
        )
        status = "READY_FOR_REVIEW" if accepted else ("TASK_BLOCKED" if blocked else "FAILED")
        terminal_payload: dict[str, Any]
        if status == "READY_FOR_REVIEW":
            terminal_payload = {"changed_files": list(changed_files)}
        else:
            terminal_payload = {"returncode": returncode, "diagnostics_ref": str(diagnostics_path)}
        events.append(
            self._event(
                spec, spec.event_cursor + len(events) + 1, status, terminal_payload
            )
        )
        package = ReturnPackage(
            status,
            str(payload.get("summary", final_text)),
            changed_files,
            tests,
            str(log_path),
            artifact_refs=(str(log_path), str(diagnostics_path), str(patch_path)),
        )
        result = AdapterResult(_managed_handle or handle or existing_handle or "", tuple(events), package)
        if _managed_handle is not None:
            self._store().finalize(_managed_handle, spec.lease_token, package, terminal_payload)
            result = AdapterResult(
                _managed_handle, self._store().events(_managed_handle, 0, spec.lease_token), package
            )
        elif self._state_store is not None:
            self._state_store.record("dsh", spec, result)
        return result

    def resume(
        self, spec: WorkerSessionSpec, task: TaskPackage, existing_handle: str
    ) -> AdapterResult:
        return self.run(spec, task, existing_handle=existing_handle)

    def attach(self, handle: str, *, fencing_token: int) -> AttachResult:
        return self._store().attach(handle, fencing_token)

    def events(
        self, handle: str, *, cursor: int, fencing_token: int
    ) -> tuple[StandardWorkerEvent, ...]:
        return self._store().events(handle, cursor, fencing_token)

    def collect_result(self, handle: str, *, fencing_token: int) -> ReturnPackage:
        return self._store().collect_result(handle, fencing_token)

    def advance_fence(self, handle: str, *, fencing_token: int) -> None:
        self._store().advance_fence(handle, fencing_token)

    def health(self, handle: str, *, fencing_token: int) -> WorkerHealth:
        return self._store().health(handle, fencing_token)

    def request_park(
        self, handle: str, *, deadline_seconds: int, fencing_token: int
    ) -> ParkResult:
        return self._store().request_park(handle, deadline_seconds, fencing_token)

    def resume_parked(self, handle: str, *, fencing_token: int) -> WorkerHealth:
        return self._store().resume_parked(handle, fencing_token)

    def cancel(self, handle: str, *, reason: str, fencing_token: int) -> CancelResult:
        return self._store().cancel(handle, reason, fencing_token)

    def _store(self) -> WorkerRunStore:
        if self._state_store is None:
            raise RuntimeError("worker state store is not configured")
        return self._state_store

    def _observe_line(
        self, handle: str, fencing_token: int, event_cursor: int, line_index: int, line: str
    ) -> None:
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            return
        if not isinstance(payload, dict):
            return
        event_id = f"{handle}:cursor:{event_cursor}:line:{line_index}"
        event_type = payload.get("type")
        if event_type == "mrlc.workflow":
            workflow_event = payload.get("event")
            workflow_payload = payload.get("payload")
            if isinstance(workflow_event, str) and isinstance(workflow_payload, dict):
                self._store().observe_event(
                    handle, fencing_token, event_id, workflow_event, workflow_payload
                )
            return
        if event_type == "session":
            self._store().observe_event(
                handle, fencing_token, event_id, "STARTED",
                {"handle": str(payload.get("sessionId", ""))},
            )
            return
        if event_type not in {"checkpoint", "checkpointed"}:
            return
        checkpoint_ref = payload.get("checkpointRef", payload.get("checkpoint_ref"))
        if isinstance(checkpoint_ref, str) and checkpoint_ref:
            self._store().observe_checkpoint(
                handle, fencing_token, checkpoint_ref, event_id=event_id
            )

    def reconcile(
        self, handle: str, *, spec: WorkerSessionSpec, task: TaskPackage, fencing_token: int
    ) -> AdapterResult:
        health = self.health(handle, fencing_token=fencing_token)
        if health.alive:
            raise RuntimeError(f"worker {handle} is still alive")
        if health.status in {"CANCELLED", "FAILED", "READY_FOR_REVIEW", "COMPLETED"}:
            return AdapterResult(
                handle, self.events(handle, cursor=0, fencing_token=fencing_token),
                self.collect_result(handle, fencing_token=fencing_token),
            )
        metadata = self._store().metadata(handle, fencing_token)
        log_path = Path(str(metadata["log_path"]))
        output = log_path.read_text()
        for index, line in enumerate(output.splitlines(keepends=True)):
            self._observe_line(handle, fencing_token, spec.event_cursor, index, line)
        _events, _native_handle, final_text = self._normalize(output, spec)
        payload = self._read_final(final_text)
        tests = tuple(
            TestResult(str(item.get("command", "")), bool(item.get("passed")), str(item.get("output", "")))
            for item in payload.get("tests", []) if isinstance(item, dict)
        )
        passed = {item.command for item in tests if item.passed}
        accepted = bool(final_text) and all(command in passed for command in task.acceptance_commands)
        before = {str(item) for item in metadata.get("before_files", [])}
        changed_files = tuple(sorted(self._files(spec.cwd) - before))
        status = "READY_FOR_REVIEW" if accepted else "FAILED"
        package = ReturnPackage(
            status, str(payload.get("summary", final_text)), changed_files, tests, str(log_path),
            artifact_refs=(
                str(log_path), str(metadata["diagnostics_path"]),
            ),
        )
        self._store().finalize(
            handle, fencing_token, package,
            {"changed_files": list(changed_files)} if accepted else {"reason": "reconciliation failed"},
        )
        return AdapterResult(handle, self.events(handle, cursor=0, fencing_token=fencing_token), package)

    @staticmethod
    def _permission_mode(permissions: tuple[str, ...]) -> str:
        if "danger-full-access" in permissions:
            return "danger-full-access"
        if "workspace-write" in permissions:
            return "workspace-write"
        return "read-only"

    def _bind_model(self, spec: WorkerSessionSpec) -> None:
        body = json.dumps({
            "sessionKey": f"dsh:{spec.session_id}",
            "targetModel": spec.model_binding,
            "reason": f"MRLC task {spec.task_id} model binding",
        }).encode()
        headers = {
            "content-type": "application/json",
            "x-cc-switch-client": "dsh",
            "x-ccr-session-id": spec.session_id,
        }
        if self._ccr_token is not None:
            headers["authorization"] = f"Bearer {self._ccr_token}"
        request = urllib.request.Request(
            f"{self._ccr_base_url}/__ccr/model-control/v1/switch",
            data=body,
            headers=headers,
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=15) as response:
            payload = json.loads(response.read().decode())
        if not isinstance(payload, dict) or payload.get("success") is not True:
            raise ValueError(f"CCR rejected model binding: {payload}")

    @staticmethod
    def _files(cwd: Path) -> set[str]:
        return {str(path.relative_to(cwd)) for path in cwd.rglob("*") if path.is_file()}

    def _ccr_patch(self, gateway_url: str | None = None) -> str:
        base_url = gateway_url or f"{self._ccr_base_url}/v1"
        return (
            "- id: session-persistence-jsonl\n"
            "  disabled: true\n"
            "\n"
            "- id: agent-default-model\n"
            "  config:\n"
            "    provider: ccr\n"
            "    model: router-managed\n"
            "\n"
            "- id: llm-pi-ai\n"
            "  name: '@deepseek-ai/dsh-llm-pi-ai'\n"
            "  config:\n"
            "    providers:\n"
            "      ccr:\n"
            "        displayName: CCR\n"
            "        apiKeyEnv: CCR_API_KEY\n"
            "        api: openai-completions\n"
            f"        baseURL: {base_url}\n"
            "        models:\n"
            "          - id: router-managed\n"
            "            name: router-managed\n"
        )

    @staticmethod
    def _prompt(spec: WorkerSessionSpec, task: TaskPackage) -> str:
        commands = "\n".join(f"- {command}" for command in task.acceptance_commands)
        return (
            f"Task {spec.task_id}: {task.objective}\n"
            f"Run and report these acceptance commands:\n{commands}\n"
            "Complete the work in the current directory. Do not commit. The result is for parent review. "
            "Your final answer must be only JSON with keys summary and tests. Each tests item must contain "
            "command, passed, and output."
        )

    def _normalize(
        self,
        output: str,
        spec: WorkerSessionSpec,
    ) -> tuple[list[StandardWorkerEvent], str, str]:
        events: list[StandardWorkerEvent] = []
        handle = ""
        final_text = ""
        for line in output.splitlines():
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(item, dict):
                continue
            kind = item.get("type")
            if kind == "session":
                handle = str(item.get("sessionId", ""))
                events.append(
                    self._event(
                        spec, spec.event_cursor + len(events) + 1, "STARTED", {"handle": handle}
                    )
                )
            elif kind == "status":
                events.append(
                    self._event(
                        spec,
                        spec.event_cursor + len(events) + 1,
                        "PROGRESS",
                        self._private_payload(item),
                    )
                )
            elif kind == "tool_call":
                events.append(
                    self._event(
                        spec,
                        spec.event_cursor + len(events) + 1,
                        "TOOL_CALL_STARTED",
                        self._private_payload(item),
                    )
                )
            elif kind == "tool_result":
                events.append(
                    self._event(
                        spec,
                        spec.event_cursor + len(events) + 1,
                        "TOOL_CALL_FINISHED",
                        self._private_payload(item),
                    )
                )
            elif kind in {"thinking", "text"}:
                events.append(
                    self._event(
                        spec,
                        spec.event_cursor + len(events) + 1,
                        "PROGRESS",
                        self._private_payload(item),
                    )
                )
            elif kind == "final":
                final_text = str(item.get("text", ""))
        return events, handle, final_text

    @staticmethod
    def _private_payload(item: dict[str, Any]) -> dict[str, Any]:
        return {key: value for key, value in item.items() if key != "type"}

    @staticmethod
    def _read_final(text: str) -> dict[str, Any]:
        stripped = text.strip()
        candidates = [stripped]
        candidates.extend(
            match.group(1).strip()
            for match in re.finditer(r"```(?:json)?\s*(\{.*?\})\s*```", stripped, re.DOTALL)
        )
        for candidate in candidates:
            try:
                payload = json.loads(candidate)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                return payload
        return {"summary": text, "tests": []}

    @staticmethod
    def _event(
        spec: WorkerSessionSpec,
        sequence: int,
        event_type: str,
        payload: dict[str, Any],
    ) -> StandardWorkerEvent:
        return StandardWorkerEvent(
            f"{spec.session_id}:{spec.task_id}:{sequence}",
            sequence,
            event_type,
            payload,
            spec.lease_token,
        )
