"""Video Journal implementation."""

import json
import os
from datetime import UTC, datetime
from pathlib import Path

from .config import state_directory
from .console import get_console

JOURNAL_FILE_NAME = "operations.jsonl"


def journal_path(root: Path) -> Path:
    return state_directory(root) / JOURNAL_FILE_NAME


def relative_path(root: Path, path: Path) -> str:
    return str(path.resolve().relative_to(root.resolve()))


def path_from_journal(root: Path, value: str) -> Path:
    candidate = (root / value).resolve()
    candidate.relative_to(root.resolve())
    return candidate


def write_journal_event(root: Path, event: dict) -> None:
    path = journal_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "timestamp": datetime.now(UTC).isoformat(),
        **event,
    }
    read_journal(root)  # Refuse to append past corruption in the middle.
    with path.open("a+b") as journal:
        journal.seek(0)
        data = journal.read()
        if data and not data.endswith(b"\n"):
            tail = data.rsplit(b"\n", 1)[-1]
            try:
                json.loads(tail)
            except (ValueError, UnicodeDecodeError):
                journal.truncate(len(data) - len(tail))
            else:
                journal.write(b"\n")
        journal.write((json.dumps(record, ensure_ascii=False) + "\n").encode("utf-8"))
        journal.flush()
        os.fsync(journal.fileno())


def read_journal(root: Path) -> list[dict]:
    path = journal_path(root)
    if not path.exists():
        return []

    events = []
    lines = path.read_bytes().splitlines(keepends=True)
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except (ValueError, UnicodeDecodeError) as error:
            if line_number == len(lines) and not line.endswith(b"\n"):
                get_console().warning(
                    f"Журнал: оборванная последняя строка {line_number} пропущена."
                )
                break
            raise ValueError(f"Повреждён журнал, строка {line_number}: {error}") from error
        if isinstance(event, dict):
            events.append(event)
    return events
