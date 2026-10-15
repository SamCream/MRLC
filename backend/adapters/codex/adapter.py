from __future__ import annotations

import json
import re
import subprocess
import tempfile
import threading
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


class CodexAdapter:
    def __init__(self, binary: str | Path, *, state_store: WorkerRunStore | None = None,
                 timeout_seconds: float = 180) -> None:
        self._binary = str(binary)
        self._timeout = timeout_seconds
        self._state_store = state_store
        self._threads: dict[str, threading.Thread] = {}
        self._results: dict[str, AdapterResult] = {}
        self._errors: dict[str, BaseException] = {}

    def capabilities(self) -> WorkerCapabilities:
        return WorkerCapabilities(
            "codex", ("read-only", "workspace-write", "danger-full-access")
        )

    def start(self, spec: WorkerSessionSpec, task: TaskPackage | None = None) -> str:
        if self._state_store is None:
            raise RuntimeError("worker state store is not configured")
        handle = f"codex:{spec.session_id}:{spec.task_id}"
        if task is None:
            self._store().register_session(
                "codex", spec, handle, {"spec": worker_spec_to_dict(spec)}
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
        artifact_dir = Path(tempfile.mkdtemp(prefix="mrlc-codex-adapter."))
        final_path = artifact_dir / "return-package.json"
        log_path = artifact_dir / "events.jsonl"
        diagnostics_path = artifact_dir / "diagnostics.log"
        schema_path = artifact_dir / "return-schema.json"
        schema_path.write_text(json.dumps(self._return_schema()))
        prompt = redact_value(self._prompt(spec, task))
        if existing_handle is None:
            command = [
                self._binary,
                "exec",
                "--ephemeral",
                "--json",
                "--skip-git-repo-check",
                "-C",
                str(spec.cwd),
            ]
        else:
            command = [
                self._binary,
                "exec",
                "resume",
                "--ephemeral",
                "--json",
                "--skip-git-repo-check",
            ]
        if "danger-full-access" in spec.permissions:
            command.append("--dangerously-bypass-approvals-and-sandbox")
        elif existing_handle is not None:
            command.extend([
                "-c",
                'sandbox_mode="workspace-write"',
                "-c",
                'approval_policy="never"',
            ])
        else:
            command.extend(["-s", "workspace-write", "-c", 'approval_policy="never"'])
        if spec.gateway_url is not None:
            # Route every model call through the run's budget meter; it holds the credential.
            command.extend([
                "-c", 'model_provider="mrlc_budget"',
                "-c", "model_providers.mrlc_budget={name=\"MRLC budget meter\",base_url="
                + json.dumps(spec.gateway_url) + ",wire_api=\"responses\"}",
            ])
        command.extend([
            "-m",
            spec.model_binding,
            "--output-schema",
            str(schema_path),
            "-o",
            str(final_path),
        ])
        if existing_handle is not None:
            command.append(existing_handle)
        command.append(prompt)
        if _managed_handle is None:
            process = subprocess.Popen(
                command, cwd=spec.cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
            )
            captured = capture_process(process, timeout=self._timeout)
        else:
            stdout_writer = log_path.open("w")
            stderr_writer = diagnostics_path.open("w")
            process = subprocess.Popen(
                command, cwd=spec.cwd, stdout=stdout_writer, stderr=stderr_writer,
                text=True, start_new_session=True,
            )
            stdout_writer.close()
            stderr_writer.close()
            self._store().register_process(
                "codex", spec, _managed_handle, process.pid,
                {
                    "log_path": str(log_path), "diagnostics_path": str(diagnostics_path),
                    "final_path": str(final_path), "cwd": str(spec.cwd),
                    "before_files": sorted(before), "spec": worker_spec_to_dict(spec),
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
        events, handle = self._normalize(stdout, spec)
        after = self._files(spec.cwd)
        changed_files = tuple(sorted(after - before))
        payload = redact_value(self._read_final(final_path, stdout))
        if final_path.is_file():
            final_path.write_text(json.dumps(payload, sort_keys=True))
        tests = tuple(
            TestResult(str(item.get("command", "")), bool(item.get("passed")), str(item.get("output", "")))
            for item in payload.get("tests", [])
        )
        passed_commands = {item.command for item in tests if item.passed}
        terminal_failure = any(
            event.event_type in {"FAILED", "PERMISSION_DENIED"} for event in events
        )
        accepted = (
            bool(payload)
            and returncode >= 0
            and returncode != 124
            and not terminal_failure
            and all(command in passed_commands for command in task.acceptance_commands)
        )
        blocked = _managed_handle is not None and self._store().has_event_type_after(
            _managed_handle, spec.lease_token, "TASK_BLOCKED", spec.event_cursor
        )
        status = "READY_FOR_REVIEW" if accepted else ("TASK_BLOCKED" if blocked else "FAILED")
        if status == "READY_FOR_REVIEW":
            events.append(
                self._event(
                    spec,
                    spec.event_cursor + len(events) + 1,
                    status,
                    {"changed_files": list(changed_files), "returncode": returncode},
                )
            )
        else:
            events.append(
                self._event(
                    spec,
                    spec.event_cursor + len(events) + 1,
                    status,
                    {"returncode": returncode},
                )
            )
        package = ReturnPackage(
            status, str(payload.get("summary", "")), changed_files, tests, str(log_path),
            artifact_refs=(str(log_path), str(diagnostics_path), str(final_path)),
        )
        result = AdapterResult(_managed_handle or handle, tuple(events), package)
        if _managed_handle is not None:
            self._store().finalize(
                _managed_handle, spec.lease_token, package,
                {"changed_files": list(changed_files)} if accepted else {"returncode": returncode},
            )
            result = AdapterResult(
                _managed_handle, self._store().events(_managed_handle, 0, spec.lease_token), package
            )
        elif self._state_store is not None:
            self._state_store.record("codex", spec, result)
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
        if event_type == "thread.started":
            self._store().observe_event(
                handle, fencing_token, event_id, "STARTED",
                {"handle": str(payload.get("thread_id", ""))},
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
        final_path = Path(str(metadata["final_path"]))
        payload = self._read_final(final_path, output)
        tests = tuple(
            TestResult(str(item.get("command", "")), bool(item.get("passed")), str(item.get("output", "")))
            for item in payload.get("tests", []) if isinstance(item, dict)
        )
        passed = {item.command for item in tests if item.passed}
        accepted = bool(payload) and all(command in passed for command in task.acceptance_commands)
        before = {str(item) for item in metadata.get("before_files", [])}
        changed_files = tuple(sorted(self._files(spec.cwd) - before))
        status = "READY_FOR_REVIEW" if accepted else "FAILED"
        package = ReturnPackage(
            status, str(payload.get("summary", "")), changed_files, tests, str(log_path),
            artifact_refs=(str(log_path), str(metadata["diagnostics_path"]), str(final_path)),
        )
        self._store().finalize(
            handle, fencing_token, package,
            {"changed_files": list(changed_files)} if accepted else {"reason": "reconciliation failed"},
        )
        return AdapterResult(handle, self.events(handle, cursor=0, fencing_token=fencing_token), package)

    @staticmethod
    def _files(cwd: Path) -> set[str]:
        return {str(path.relative_to(cwd)) for path in cwd.rglob("*") if path.is_file()}

    @staticmethod
    def _return_schema() -> dict[str, Any]:
        return {
            "type": "object", "additionalProperties": False,
            "required": ["summary", "tests"],
            "properties": {
                "summary": {"type": "string"},
                "tests": {"type": "array", "items": {
                    "type": "object", "additionalProperties": False,
                    "required": ["command", "passed", "output"],
                    "properties": {
                        "command": {"type": "string"}, "passed": {"type": "boolean"},
                        "output": {"type": "string"},
                    },
                }},
            },
        }

    @staticmethod
    def _prompt(spec: WorkerSessionSpec, task: TaskPackage) -> str:
        commands = "\n".join(f"- {command}" for command in task.acceptance_commands)
        return (
            f"Task {spec.task_id}: {task.objective}\n"
            f"Run and report these acceptance commands:\n{commands}\n"
            "Complete the work in the current directory. Your final message must be only this JSON object, "
            'no prose or code fences: {"summary": "<what changed>", "tests": [{"command": "<exact command>", '
            '"passed": true|false, "output": "<short output>"}]} with one tests entry per acceptance command. '
            "Do not commit. The result is for parent review."
        )

    def _normalize(self, output: str, spec: WorkerSessionSpec) -> tuple[list[StandardWorkerEvent], str]:
        events: list[StandardWorkerEvent] = []
        handle = ""
        for line in output.splitlines():
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            kind = item.get("type")
            if kind == "thread.started":
                handle = str(item.get("thread_id", ""))
                events.append(
                    self._event(
                        spec, spec.event_cursor + len(events) + 1, "STARTED", {"handle": handle}
                    )
                )
            elif kind == "turn.started":
                events.append(
                    self._event(
                        spec,
                        spec.event_cursor + len(events) + 1,
                        "PROGRESS",
                        {"phase": "turn_started"},
                    )
                )
            elif kind == "item.completed":
                private = item.get("item", {})
                if private.get("type") in {"agent_message", "command_execution", "error"}:
                    events.append(
                        self._event(
                            spec,
                            spec.event_cursor + len(events) + 1,
                            "PROGRESS",
                            {"item_type": private.get("type")},
                        )
                    )
            elif kind == "mrlc.workflow":
                workflow_event = item.get("event")
                workflow_payload = item.get("payload")
                if isinstance(workflow_event, str) and isinstance(workflow_payload, dict):
                    events.append(
                        self._event(
                            spec,
                            spec.event_cursor + len(events) + 1,
                            workflow_event,
                            workflow_payload,
                        )
                    )
        return events, handle

    @staticmethod
    def _read_final(path: Path, output: str) -> dict[str, Any]:
        if path.is_file():
            text = path.read_text()
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return CodexAdapter._embedded_package(text) or {"summary": text, "tests": []}
        return {"summary": output, "tests": []}

    @staticmethod
    def _embedded_package(text: str) -> dict[str, Any] | None:
        """Accept a Return Package the model wrapped in a fenced block when the gateway ignores the schema."""
        for block in reversed(re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)):
            try:
                value = json.loads(block)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict) and isinstance(value.get("summary"), str) and isinstance(value.get("tests"), list):
                return value
        return None

    @staticmethod
    def _event(spec: WorkerSessionSpec, sequence: int, event_type: str, payload: dict[str, Any]) -> StandardWorkerEvent:
        return StandardWorkerEvent(
            f"{spec.session_id}:{spec.task_id}:{sequence}",
            sequence,
            event_type,
            payload,
            spec.lease_token,
        )
