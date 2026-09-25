"""Consistent console output without runtime dependencies."""

from __future__ import annotations

import os
import shutil
import sys
import unicodedata
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
        self._live_width = 0

    @property
    def is_interactive(self) -> bool:
        return bool(getattr(sys.stdout, "isatty", lambda: False)()) and not (
            os.environ.get("CI") or os.environ.get("TERM") == "dumb"
        )

    @property
    def terminal_width(self) -> int:
        return max(2, shutil.get_terminal_size(fallback=(80, 24)).columns)

    def write_live(self, message: str) -> None:
        if self.quiet:
            return
        if not self.is_interactive:
            self.progress(message)
            return
        self.clear_live()
        message = truncate_display(message, self.terminal_width - 1)
        _write_text("\r" + message, end="")
        self._live_width = display_width(message)
        sys.stdout.flush()

    def clear_live(self) -> None:
        if self._live_width:
            _write_text("\r" + " " * min(self._live_width, self.terminal_width - 1) + "\r", end="")
            self._live_width = 0
            sys.stdout.flush()

    def finish_live(self, message: str) -> None:
        self.clear_live()
        self.success(message)

    def emit(self, level: Level, message: str, *, end: str = "\n") -> None:
        if self.quiet and level not in {Level.WARNING, Level.ERROR}:
            return
        if level is Level.VERBOSE and not self.verbose_enabled:
            return
        self.clear_live()
        _write_text(f"[{level.value}] {message}", end=end)

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
        console.clear_live()
        _active_console.reset(token)


def display_width(text: str) -> int:
    return sum(
        0
        if unicodedata.combining(char) or unicodedata.category(char) == "Cf"
        else 2
        if unicodedata.east_asian_width(char) in {"W", "F"}
        else 1
        for char in text
    )


def truncate_display(text: str, width: int) -> str:
    if width <= 0:
        return ""
    if display_width(text) <= width:
        return text
    result = ""
    for char in text:
        if display_width(result + char) > width - 1:
            break
        result += char
    return result + "…"


def get_console() -> Console:
    """Return the CLI console, or a normal console for direct module calls."""
    return _active_console.get() or Console()


def console_print(*values: object, sep: str = " ", end: str = "\n", **_: object) -> None:
    """Deprecated raw compatibility helper; it never infers a log level."""
    message = sep.join(str(value) for value in values)
    get_console().emit(Level.INFO, message, end=end)


def _write_text(message: str, *, end: str) -> None:
    try:
        print(message, end=end)
    except UnicodeEncodeError:
        reconfigure = getattr(sys.stdout, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8", errors="replace")
                print(message, end=end)
                return
            except (OSError, ValueError, UnicodeEncodeError):
                pass
        encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
        safe = message.encode(encoding, errors="replace").decode(encoding)
        print(safe, end=end)


def configure_stdout() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
        except (OSError, ValueError):
            pass
