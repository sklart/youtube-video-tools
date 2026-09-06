"""Adapter for yt-dlp metadata queries."""

from __future__ import annotations

from pathlib import Path

from .process import ProcessResult, run, run_stream


class YtDlpClient:
    def __init__(self, executable: str = "yt-dlp", *, cookies_file: Path | None = None) -> None:
        self.executable = executable
        self.cookies_file = cookies_file

    def run(self, arguments: list[str], *, timeout: float = 30) -> ProcessResult:
        command = [self.executable]
        if self.cookies_file and self.cookies_file.is_file():
            command.extend(["--cookies", str(self.cookies_file)])
        command.extend(arguments)
        return run(command, timeout=timeout)

    def version(self) -> ProcessResult:
        return self.run(["--version"], timeout=15)

    def stream(
        self, arguments: list[str], *, timeout: float | None = None, on_line=None
    ) -> ProcessResult:
        command = [self.executable]
        if self.cookies_file and self.cookies_file.is_file():
            command.extend(["--cookies", str(self.cookies_file)])
        command.extend(arguments)
        return run_stream(command, timeout=timeout, on_line=on_line)
