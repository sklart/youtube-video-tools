"""UTF-8 subprocess primitives shared by media-tool adapters."""

from __future__ import annotations

import subprocess
from collections.abc import Sequence
from dataclasses import dataclass


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
