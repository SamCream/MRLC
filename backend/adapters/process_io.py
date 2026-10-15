from __future__ import annotations

import subprocess
import threading
import time
from dataclasses import dataclass
from typing import Callable, TextIO
from pathlib import Path


@dataclass(frozen=True, slots=True)
class CapturedProcess:
    stdout: str
    stderr: str
    returncode: int


def capture_process(
    process: subprocess.Popen[str],
    *,
    timeout: float,
    on_stdout_line: Callable[[str], None] | None = None,
) -> CapturedProcess:
    stdout_lines: list[str] = []
    stderr_lines: list[str] = []
    callback_errors: list[BaseException] = []

    def drain(
        stream: TextIO | None, sink: list[str], callback: Callable[[str], None] | None
    ) -> None:
        if stream is None:
            return
        for line in stream:
            sink.append(line)
            if callback is not None:
                try:
                    callback(line)
                except BaseException as error:
                    callback_errors.append(error)

    stdout_thread = threading.Thread(
        target=drain, args=(process.stdout, stdout_lines, on_stdout_line), daemon=True
    )
    stderr_thread = threading.Thread(
        target=drain, args=(process.stderr, stderr_lines, None), daemon=True
    )
    stdout_thread.start()
    stderr_thread.start()
    try:
        returncode = process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
        returncode = 124
    stdout_thread.join()
    stderr_thread.join()
    if callback_errors:
        raise callback_errors[0]
    return CapturedProcess("".join(stdout_lines), "".join(stderr_lines), returncode)


def capture_file_process(
    process: subprocess.Popen[str],
    *,
    stdout_path: Path,
    stderr_path: Path,
    timeout: float,
    on_stdout_line: Callable[[int, str], None] | None = None,
) -> CapturedProcess:
    deadline = time.monotonic() + timeout
    line_index = 0
    with stdout_path.open() as stdout_stream:
        while True:
            line = stdout_stream.readline()
            if line:
                if on_stdout_line is not None:
                    on_stdout_line(line_index, line)
                line_index += 1
                continue
            if process.poll() is not None:
                for remaining in stdout_stream:
                    if on_stdout_line is not None:
                        on_stdout_line(line_index, remaining)
                    line_index += 1
                break
            if time.monotonic() >= deadline:
                process.kill()
                process.wait()
                return CapturedProcess(
                    stdout_path.read_text(), stderr_path.read_text(), 124
                )
            time.sleep(0.01)
    return CapturedProcess(
        stdout_path.read_text(), stderr_path.read_text(), int(process.returncode)
    )
