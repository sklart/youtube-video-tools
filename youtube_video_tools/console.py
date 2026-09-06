"""Consistent console output without runtime dependencies."""

from __future__ import annotations

import sys
from enum import StrEnum


class Level(StrEnum):
    INFO = "INFO"
    WARNING = "WARN"
    ERROR = "ERROR"
    SUCCESS = "OK"
    PROGRESS = "PROGRESS"
    VERBOSE = "VERBOSE"


class Console:
    def __init__(self, *, quiet: bool = False, verbose: bool = False) -> None:
        self.quiet = quiet
        self.verbose_enabled = verbose

    def emit(self, level: Level, message: str, *, end: str = "\n") -> None:
        if self.quiet and level not in {Level.WARNING, Level.ERROR}:
            return
        if level is Level.VERBOSE and not self.verbose_enabled:
            return
        print(f"[{level.value}] {message}", end=end)

    def info(self, message: str) -> None:
        self.emit(Level.INFO, message)

    def warning(self, message: str) -> None:
        self.emit(Level.WARNING, message)

    def error(self, message: str) -> None:
        self.emit(Level.ERROR, message)

    def success(self, message: str) -> None:
        self.emit(Level.SUCCESS, message)

    def progress(self, message: str, *, end: str = "\n") -> None:
        self.emit(Level.PROGRESS, message, end=end)

    def verbose(self, message: str) -> None:
        self.emit(Level.VERBOSE, message)


def configure_stdout() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
