"""Shared helpers for YouTube Video Tools."""

import fnmatch
import json
import os
import re
from datetime import datetime
from pathlib import Path

WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{number}" for number in range(1, 10)),
    *(f"LPT{number}" for number in range(1, 10)),
}
INVALID_WINDOWS_CHARS = '<>:"/\\|?*'
FOLDER_FILTER_ENV = "VIDEO_TOOLS_FOLDERS"
DEFAULT_VIDEO_EXTENSIONS = {
    ".mp4",
    ".mkv",
    ".webm",
    ".mov",
    ".avi",
    ".flv",
    ".wmv",
}
DEFAULT_SUBTITLE_EXTENSIONS = {".vtt", ".srt", ".ass"}


def active_folder_filter() -> tuple[str, ...]:
    value = os.environ.get(FOLDER_FILTER_ENV)
    if not value:
        return ()
    try:
        folders = json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return ()
    return tuple(str(folder) for folder in folders)


def path_selected(root: Path, path: Path) -> bool:
    """Return whether a path belongs to the active top-level folder filter."""
    folders = active_folder_filter()
    if not folders:
        return True
    try:
        relative = path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    selected = {folder.casefold() for folder in folders}
    return len(relative.parts) > 1 and relative.parts[0].casefold() in selected


def resolve_folder_filter(
    root: Path,
    patterns: list[str] | None,
    folder_file: Path | None,
    *,
    all_folders: bool,
) -> tuple[str, ...]:
    if all_folders:
        return ()

    requested = list(patterns or [])
    if folder_file:
        for line in folder_file.read_text(encoding="utf-8-sig").splitlines():
            value = line.strip()
            if value and not value.startswith("#"):
                requested.append(value)
    if not requested:
        return ()

    available = sorted(
        (path.name for path in root.iterdir() if path.is_dir()),
        key=str.casefold,
    )
    selected = []
    for pattern in requested:
        matches = [
            folder
            for folder in available
            if fnmatch.fnmatchcase(folder.casefold(), pattern.casefold())
        ]
        if not matches:
            raise ValueError(f"папки по маске {pattern!r} не найдены")
        for folder in matches:
            if folder not in selected:
                selected.append(folder)
    return tuple(selected)


def normalize_windows_name(
    name: str,
    *,
    preserve_extension: bool = False,
    max_length: int = 120,
    fallback: str = "Unknown",
) -> str:
    """Return a Windows-safe file or directory name component."""
    translation = str.maketrans({character: "_" for character in INVALID_WINDOWS_CHARS})
    cleaned = (
        "".join(
            "_" if ord(character) < 32 else character for character in name.translate(translation)
        )
        .strip()
        .rstrip(" .")
    )

    extension = ""
    stem = cleaned
    if preserve_extension:
        suffix = Path(cleaned).suffix
        if suffix and len(suffix) < max_length:
            extension = suffix
            stem = cleaned[: -len(suffix)].rstrip(" .")

    if not stem:
        stem = fallback
    if stem.upper() in WINDOWS_RESERVED_NAMES:
        stem = f"_{stem}"

    available = max(1, max_length - len(extension))
    stem = stem[:available].rstrip(" .") or fallback[:available]
    return f"{stem}{extension}"


def extract_filename_date(filename: str) -> tuple[str | None, bool]:
    """Return (date text, valid calendar date) from a standard video name."""
    match = re.search(
        r"_(\d{2}\.\d{2}\.\d{4})(?=\s*\[[^\]]+\](?:\.[^.]+)+$)",
        filename,
    )
    if not match:
        return None, False
    value = match.group(1)
    try:
        datetime.strptime(value, "%d.%m.%Y")
    except ValueError:
        return value, False
    return value, True


def is_affirmative_reply(answer: str, *, default: bool = True) -> bool:
    value = answer.strip()
    if not value:
        return default
    return value[:1].casefold() in {"y", "д"}
