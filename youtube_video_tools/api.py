"""Small reusable command boundary, independent of argv and environment mutation."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import state_directory
from .console import Console
from .settings import Settings


@dataclass(frozen=True)
class AppContext:
    root: Path
    config: Settings = field(default_factory=Settings)
    console: Console = field(default_factory=Console)
    services: dict[str, Any] = field(default_factory=dict)

    @property
    def state_dir(self) -> Path:
        return state_directory(self.root)


@dataclass(frozen=True)
class CommandResult:
    exit_code: int = 0
    changed: bool = False
    warnings: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()

    @property
    def success(self) -> bool:
        return self.exit_code == 0
