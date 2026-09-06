"""UTF-8 subprocess primitives shared by media-tool adapters."""

from __future__ import annotations

import subprocess
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from queue import Empty, Queue
from threading import Thread


@dataclass(frozen=True)
class ProcessResult:
    command: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str


class ExternalToolError(RuntimeError):
    pass


def run(command: Sequence[str], *, timeout: float = 30) -> ProcessResult:
    rendered = tuple(str(part) for part in command)
    try:
        completed = subprocess.run(
            rendered,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except FileNotFoundError as error:
        raise ExternalToolError(f"не найдена программа: {rendered[0]}") from error
    except subprocess.TimeoutExpired as error:
        raise ExternalToolError(f"таймаут ({timeout:g} с): {rendered[0]}") from error
    return ProcessResult(rendered, completed.returncode, completed.stdout, completed.stderr)


def run_stream(
    command: Sequence[str],
    *,
    timeout: float | None = None,
    on_line: Callable[[str], None] | None = None,
) -> ProcessResult:
    """Run a tool with merged UTF-8 output while optionally reporting each line."""
    rendered = tuple(str(part) for part in command)
    try:
        process = subprocess.Popen(
            rendered,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
    except FileNotFoundError as error:
        raise ExternalToolError(f"не найдена программа: {rendered[0]}") from error

    output: Queue[str | None] = Queue()

    def read_output() -> None:
        assert process.stdout is not None
        for line in process.stdout:
            output.put(line)
        output.put(None)

    reader = Thread(target=read_output, daemon=True)
    reader.start()
    deadline = time.monotonic() + timeout if timeout is not None else None
    lines: list[str] = []
    try:
        while True:
            if deadline is not None and time.monotonic() >= deadline:
                process.kill()
                raise ExternalToolError(f"таймаут ({timeout:g} с): {rendered[0]}")
            try:
                line = output.get(timeout=0.1)
            except Empty:
                continue
            if line is None:
                break
            lines.append(line)
            if on_line:
                on_line(line)
        returncode = process.wait(timeout=1)
    except subprocess.TimeoutExpired as error:
        process.kill()
        raise ExternalToolError(f"таймаут ({timeout:g} с): {rendered[0]}") from error
    return ProcessResult(rendered, returncode, "".join(lines), "")
