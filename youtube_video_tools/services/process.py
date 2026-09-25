"""UTF-8 subprocess primitives shared by media-tool adapters."""

from __future__ import annotations

import os
import signal
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


def terminate_tree(process: subprocess.Popen) -> None:
    if os.name == "nt":
        if process.poll() is None:
            try:
                subprocess.run(
                    ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                    capture_output=True,
                    timeout=10,
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired):
                process.kill()
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    if process.poll() is None:
        process.kill()


def run(command: Sequence[str], *, timeout: float = 30) -> ProcessResult:
    rendered = tuple(str(part) for part in command)
    try:
        process = subprocess.Popen(
            rendered,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            start_new_session=os.name != "nt",
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
        )
    except FileNotFoundError as error:
        raise ExternalToolError(f"не найдена программа: {rendered[0]}") from error
    try:
        stdout, stderr = process.communicate(timeout=timeout)
        return ProcessResult(rendered, process.returncode, stdout, stderr)
    except subprocess.TimeoutExpired as error:
        raise ExternalToolError(f"таймаут ({timeout:g} с): {rendered[0]}") from error
    finally:
        try:
            if process.poll() is None:
                terminate_tree(process)
        finally:
            if process.poll() is None:
                process.kill()
            process.communicate()
            process.wait()
            for pipe in (process.stdout, process.stderr):
                if pipe is not None:
                    pipe.close()


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
            start_new_session=os.name != "nt",
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
        )
    except FileNotFoundError as error:
        raise ExternalToolError(f"не найдена программа: {rendered[0]}") from error

    output: Queue[str | None] = Queue()

    def read_output() -> None:
        assert process.stdout is not None
        try:
            for line in process.stdout:
                output.put(line)
        finally:
            output.put(None)

    reader = Thread(target=read_output, daemon=True)
    reader.start()
    deadline = time.monotonic() + timeout if timeout is not None else None
    lines: list[str] = []
    try:
        while True:
            if deadline is not None and time.monotonic() >= deadline:
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
        raise ExternalToolError(f"таймаут ({timeout:g} с): {rendered[0]}") from error
    finally:
        try:
            if process.poll() is None or reader.is_alive():
                terminate_tree(process)
        finally:
            if process.poll() is None:
                process.kill()
            process.wait()
            reader.join(timeout=10)
            if process.stdout is not None:
                process.stdout.close()
    return ProcessResult(rendered, returncode, "".join(lines), "")
