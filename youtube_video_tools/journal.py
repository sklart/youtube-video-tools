"""Video Journal implementation."""

import json
from datetime import UTC, datetime
from pathlib import Path

JOURNAL_DIR_NAME = ".video-tools"
JOURNAL_FILE_NAME = "operations.jsonl"


def journal_path(root: Path) -> Path:
    return root / JOURNAL_DIR_NAME / JOURNAL_FILE_NAME


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
    with path.open("a", encoding="utf-8") as journal:
        journal.write(json.dumps(record, ensure_ascii=False) + "\n")
        journal.flush()


def read_journal(root: Path) -> list[dict]:
    path = journal_path(root)
    if not path.exists():
        return []

    events = []
    with path.open("r", encoding="utf-8") as journal:
        for line_number, line in enumerate(journal, start=1):
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError as error:
                raise ValueError(f"Повреждён журнал, строка {line_number}: {error}") from error
            if isinstance(event, dict):
                events.append(event)
    return events
