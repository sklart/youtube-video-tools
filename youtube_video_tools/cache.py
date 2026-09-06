"""Video Metadata Cache implementation."""

import copy
import json
from pathlib import Path

from .state import atomic_write_json

CACHE_FILE_NAME = "yt-dlp-cache.json"
JOURNAL_DIR_NAME = ".video-tools"


def cache_path(root: Path) -> Path:
    return root / JOURNAL_DIR_NAME / CACHE_FILE_NAME


def load_cache(root: Path) -> dict:
    path = cache_path(root)
    if not path.exists():
        return {"entries": {}, "dirty": False}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"entries": {}, "dirty": False}
    entries = payload if isinstance(payload, dict) else {}
    normalized = {str(key): value for key, value in entries.items() if isinstance(value, dict)}
    return {"entries": normalized, "dirty": False}


def inspect_cache(root: Path) -> tuple[int | None, str | None]:
    """Return the entry count, or an error without hiding malformed JSON."""
    path = cache_path(root)
    if not path.exists():
        return 0, None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        return None, str(error)
    if not isinstance(payload, dict):
        return None, "корень JSON должен быть объектом"
    return len(payload), None


def get_value(
    state: dict,
    source_type: str,
    source_id: str | None,
    field: str,
) -> object | None:
    if not source_id:
        return None
    entry = state["entries"].get(f"{source_type}:{source_id}", {})
    if field not in entry:
        return None
    return copy.deepcopy(entry[field])


def get_field(
    state: dict,
    source_type: str,
    source_id: str | None,
    field: str,
) -> str | None:
    value = get_value(state, source_type, source_id, field)
    return value if isinstance(value, str) and value else None


def set_value(
    state: dict,
    source_type: str,
    source_id: str | None,
    field: str,
    value: object | None,
) -> None:
    if not source_id or value is None:
        return
    key = f"{source_type}:{source_id}"
    entry = state["entries"].setdefault(key, {})
    if entry.get(field) != value:
        entry[field] = copy.deepcopy(value)
        state["dirty"] = True


def set_field(
    state: dict,
    source_type: str,
    source_id: str | None,
    field: str,
    value: str | None,
) -> None:
    if not value:
        return
    set_value(state, source_type, source_id, field, value)


def save_cache(root: Path, state: dict) -> None:
    if not state.get("dirty"):
        return
    path = cache_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(path, state["entries"])
    state["dirty"] = False
