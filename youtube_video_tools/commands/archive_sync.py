"""Sync Download Archive implementation."""

import argparse
import os
import shutil
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .. import config as video_config
from ..console import console_print as print
from ..core import is_affirmative_reply, path_selected
from . import inventory

VIDEO_EXTENSIONS = inventory.VIDEO_EXTENSIONS
extract_source = inventory.extract_source
BASE_DIR = video_config.BASE_DIR


@dataclass(frozen=True)
class ArchiveSyncPlan:
    original_lines: list[str]
    kept_lines: list[str]
    removed_ids: list[str]
    local_ids: set[str]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=("Удаляет из yt-dlp-archive.txt ID, для которых локального видео больше нет.")
    )
    parser.add_argument("--root", type=Path, default=BASE_DIR)
    parser.add_argument(
        "--archive",
        type=Path,
        help="Путь к архиву. По умолчанию <root>/yt-dlp-archive.txt.",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--apply",
        action="store_true",
        help="Записать изменения с предварительным созданием резервной копии.",
    )
    mode.add_argument(
        "--dry-run",
        action="store_true",
        help="Только показать устаревшие ID (режим по умолчанию).",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Не запрашивать подтверждение при --apply.",
    )
    return parser.parse_args()


def collect_local_youtube_ids(root: Path) -> set[str]:
    identifiers = set()
    for path in root.rglob("*"):
        if (
            not path.is_file()
            or not path_selected(root, path)
            or path.suffix.lower() not in VIDEO_EXTENSIONS
        ):
            continue
        source_type, source_id = extract_source(path.name)
        if source_type == "youtube":
            identifiers.add(source_id)
    return identifiers


def parse_archive_entry(line: str) -> tuple[str, str] | None:
    parts = line.strip().split()
    if len(parts) != 2:
        return None
    extractor, identifier = parts
    if not extractor or not identifier:
        return None
    return extractor, identifier


def build_sync_plan(root: Path, archive_path: Path) -> ArchiveSyncPlan:
    original_lines = (
        archive_path.read_text(encoding="utf-8").splitlines() if archive_path.exists() else []
    )
    local_ids = collect_local_youtube_ids(root)
    kept_lines = []
    removed_ids = []

    for line in original_lines:
        entry = parse_archive_entry(line)
        if entry and entry[0] == "youtube" and entry[1] not in local_ids:
            removed_ids.append(entry[1])
            continue
        kept_lines.append(line)

    return ArchiveSyncPlan(
        original_lines=original_lines,
        kept_lines=kept_lines,
        removed_ids=removed_ids,
        local_ids=local_ids,
    )


def backup_path(archive_path: Path) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    return archive_path.with_name(f"{archive_path.name}.backup-{timestamp}")


def apply_sync_plan(
    archive_path: Path,
    plan: ArchiveSyncPlan,
) -> Path | None:
    if not plan.removed_ids:
        return None

    backup = backup_path(archive_path)
    shutil.copy2(archive_path, backup)

    file_descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{archive_path.name}.",
        suffix=".tmp",
        dir=archive_path.parent,
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(file_descriptor, "w", encoding="utf-8", newline="\n") as file:
            if plan.kept_lines:
                file.write("\n".join(plan.kept_lines) + "\n")
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary_path, archive_path)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise
    return backup


def print_plan(plan: ArchiveSyncPlan) -> None:
    print(
        f"[SUMMARY] Локальных YouTube ID: {len(plan.local_ids)}; "
        f"строк архива: {len(plan.original_lines)}; "
        f"к удалению: {len(plan.removed_ids)}"
    )
    for identifier in plan.removed_ids:
        print(f"[REMOVE] youtube {identifier}")


def synchronize_archive(
    root: Path,
    archive_path: Path,
    *,
    apply: bool,
    assume_yes: bool,
    input_fn=input,
) -> int:
    if not root.is_dir():
        print(f"[ERROR] Корневая папка не найдена: {root}")
        return 2
    try:
        plan = build_sync_plan(root, archive_path)
    except OSError as error:
        print(f"[ERROR] Не удалось построить план: {error}")
        return 2

    print_plan(plan)
    if not apply or not plan.removed_ids:
        return 0

    if not assume_yes:
        try:
            answer = input_fn("Удалить перечисленные ID из архива? [Y/n]: ")
        except (EOFError, KeyboardInterrupt):
            print("\n[CANCEL] Архив не изменялся.")
            return 0
        if not is_affirmative_reply(answer):
            print("[CANCEL] Архив не изменялся.")
            return 0

    try:
        backup = apply_sync_plan(archive_path, plan)
    except OSError as error:
        print(f"[ERROR] Не удалось обновить архив: {error}")
        return 2

    if backup:
        print(f"[OK] Архив обновлён: {archive_path}")
        print(f"[OK] Резервная копия: {backup}")
    return 0


def main() -> int:
    args = parse_args()
    root = args.root.resolve()
    archive_path = (args.archive or (root / "yt-dlp-archive.txt")).resolve()
    return synchronize_archive(
        root,
        archive_path,
        apply=args.apply,
        assume_yes=args.yes,
    )
