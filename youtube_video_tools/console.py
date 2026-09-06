"""Consistent console output without runtime dependencies."""

from __future__ import annotations

import sys
from contextlib import contextmanager
from contextvars import ContextVar
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


_active_console: ContextVar[Console | None] = ContextVar("active_console", default=None)


@contextmanager
def use_console(console: Console):
    token = _active_console.set(console)
    try:
        yield
    finally:
        _active_console.reset(token)


def console_print(*values: object, sep: str = " ", end: str = "\n", **_: object) -> None:
    """Compatibility output function for commands during the Console migration."""
    message = sep.join(str(value) for value in values)
    console = _active_console.get()
    if console is None:
        try:
            print(message, end=end)
        except UnicodeEncodeError:
            encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
            safe = message.encode(encoding, errors="replace").decode(encoding)
            print(safe, end=end)
        return
    level = Level.INFO
    if message.startswith("[ERROR]"):
        level = Level.ERROR
    elif message.startswith(("[WARN]", "[WARNING]")):
        level = Level.WARNING
    elif message.startswith("[OK]"):
        level = Level.SUCCESS
    elif message.startswith(("[PROCESS", "[CHECK]", "[APPLY]", "[DOWNLOAD]")):
        level = Level.PROGRESS
    console.emit(level, message, end=end)


def configure_stdout() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
        except (OSError, ValueError):
            pass
