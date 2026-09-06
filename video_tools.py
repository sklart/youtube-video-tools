"""Video archive utilities in one readable, self-contained Python file."""

import argparse
import contextlib
import fnmatch
import io
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(
        encoding="utf-8",
        errors="replace",
        line_buffering=True,
    )


WINDOWS_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{number}" for number in range(1, 10)),
    *(f"LPT{number}" for number in range(1, 10)),
}
INVALID_WINDOWS_CHARS = '<>:"/\\|?*'
FOLDER_FILTER_ENV = "VIDEO_TOOLS_FOLDERS"
DEFAULT_VIDEO_EXTENSIONS = {
    ".mp4", ".mkv", ".webm", ".mov", ".avi", ".flv", ".wmv",
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
    cleaned = "".join(
        "_" if ord(character) < 32 else character
        for character in name.translate(translation)
    ).strip().rstrip(" .")

    extension = ""
    stem = cleaned
    if preserve_extension:
        suffix = Path(cleaned).suffix
        if suffix and len(suffix) < max_length:
            extension = suffix
            stem = cleaned[:-len(suffix)].rstrip(" .")

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


# ============================================================================
# Video Config
# ============================================================================

def _build_video_config():
    import os
    import tomllib
    from pathlib import Path
    from typing import Any


    BASE_DIR = Path(__file__).resolve().parent
    DEFAULT_CONFIG_PATH = BASE_DIR / "config.toml"
    CONFIG_ENV_NAME = "VIDEO_TOOLS_CONFIG"
    ROOT_ENV_NAME = "VIDEO_TOOLS_ROOT"
    PATH_ENV_NAMES = {
        "cookies": "YOUTUBE_COOKIES_FILE",
        "yt_dlp": "VIDEO_TOOLS_YT_DLP",
        "ffprobe": "VIDEO_TOOLS_FFPROBE",
        "ffmpeg": "VIDEO_TOOLS_FFMPEG",
    }


    def env_text(env_name: str | None) -> str | None:
        if not env_name:
            return None
        value = os.environ.get(env_name)
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None


    def env_path(env_name: str | None) -> Path | None:
        value = env_text(env_name)
        return Path(value).expanduser() if value else None


    def default_root_path() -> Path:
        return env_path(ROOT_ENV_NAME) or BASE_DIR


    def default_config_path() -> Path:
        return env_path(CONFIG_ENV_NAME) or DEFAULT_CONFIG_PATH


    def load_config(config_path: Path | None = None) -> dict[str, Any]:
        path = config_path or default_config_path()
        if not path.exists():
            return {}

        with path.open("rb") as config_file:
            return tomllib.load(config_file)


    def configured_path(
        config: dict[str, Any],
        key: str,
        *,
        env_name: str | None = None,
    ) -> Path | None:
        resolved_env_name = env_name or PATH_ENV_NAMES.get(key)
        configured_env_path = env_path(resolved_env_name)
        if configured_env_path:
            return configured_env_path

        value = config.get("paths", {}).get(key)
        return Path(value).expanduser() if value else None


    def configured_command(
        config: dict[str, Any],
        key: str,
        default: str,
        *,
        env_name: str | None = None,
    ) -> str:
        resolved_env_name = env_name or PATH_ENV_NAMES.get(key)
        configured_env_value = env_text(resolved_env_name)
        if configured_env_value:
            return configured_env_value
        return str(config.get("paths", {}).get(key, default))
    return SimpleNamespace(**locals())

video_config = _build_video_config()


# ============================================================================
# Video Metadata Cache
# ============================================================================

def _build_video_metadata_cache():
    import copy
    import json
    from pathlib import Path

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
        normalized = {
            str(key): value
            for key, value in entries.items()
            if isinstance(value, dict)
        }
        return {"entries": normalized, "dirty": False}


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
        payload = json.dumps(
            state["entries"],
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        path.write_text(payload + "\n", encoding="utf-8")
        state["dirty"] = False
    return SimpleNamespace(**locals())

video_metadata_cache = _build_video_metadata_cache()


# ============================================================================
# Video Journal
# ============================================================================

def _build_video_journal():
    import json
    from datetime import datetime, timezone
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
            "timestamp": datetime.now(timezone.utc).isoformat(),
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
                    raise ValueError(
                        f"Повреждён журнал, строка {line_number}: {error}"
                    ) from error
                if isinstance(event, dict):
                    events.append(event)
        return events
    return SimpleNamespace(**locals())

video_journal = _build_video_journal()


# ============================================================================
# Inventory
# ============================================================================

def _build_inventory():
    import argparse
    import csv
    import re
    from dataclasses import dataclass
    from pathlib import Path

    BASE_DIR = video_config.BASE_DIR


    VIDEO_EXTENSIONS = DEFAULT_VIDEO_EXTENSIONS
    SUBTITLE_EXTENSIONS = DEFAULT_SUBTITLE_EXTENSIONS
    YOUTUBE_ID_RE = re.compile(r"\[([A-Za-z0-9_-]{11})\]")
    ANY_ID_RE = re.compile(r"\[([A-Za-z0-9_-]+)\]")


    @dataclass(frozen=True)
    class InventoryRecord:
        folder: str
        relative_path: str
        filename: str
        extension: str
        size_bytes: int
        source_type: str
        source_id: str
        upload_date: str
        subtitle_count: int
        subtitles: str


    def parse_args() -> argparse.Namespace:
        parser = argparse.ArgumentParser(
            description="Создаёт CSV-инвентаризацию без чтения видеопотока."
        )
        parser.add_argument("--root", type=Path, default=BASE_DIR)
        parser.add_argument(
            "--output",
            type=Path,
            help="Путь итогового CSV. По умолчанию <root>/inventory.csv.",
        )
        return parser.parse_args()


    def extract_source(filename: str) -> tuple[str, str]:
        youtube_matches = YOUTUBE_ID_RE.findall(filename)
        if youtube_matches:
            return "youtube", youtube_matches[-1]

        matches = ANY_ID_RE.findall(filename)
        if matches:
            return "other", matches[-1]
        return "", ""


    def extract_date(filename: str) -> str:
        value, _ = extract_filename_date(filename)
        return value or ""


    def related_subtitles(video_path: Path) -> list[Path]:
        subtitles = []
        prefix = f"{video_path.stem}."
        for candidate in video_path.parent.iterdir():
            if (
                candidate.is_file()
                and candidate.name.startswith(prefix)
                and candidate.suffix.lower() in SUBTITLE_EXTENSIONS
            ):
                subtitles.append(candidate)
        return sorted(subtitles, key=lambda path: path.name.casefold())


    def build_record(root: Path, video_path: Path) -> InventoryRecord:
        relative = video_path.relative_to(root)
        folder = relative.parts[0] if len(relative.parts) > 1 else ""
        source_type, source_id = extract_source(video_path.name)
        subtitles = related_subtitles(video_path)
        return InventoryRecord(
            folder=folder,
            relative_path=str(relative),
            filename=video_path.name,
            extension=video_path.suffix.lower(),
            size_bytes=video_path.stat().st_size,
            source_type=source_type,
            source_id=source_id,
            upload_date=extract_date(video_path.name),
            subtitle_count=len(subtitles),
            subtitles=" | ".join(path.name for path in subtitles),
        )


    def collect_inventory(root: Path) -> list[InventoryRecord]:
        records = []
        for path in root.rglob("*"):
            if (
                path.is_file()
                and path_selected(root, path)
                and path.suffix.lower() in VIDEO_EXTENSIONS
            ):
                records.append(build_record(root, path))
        return sorted(
            records,
            key=lambda record: (
                record.folder.casefold(),
                record.filename.casefold(),
                record.relative_path.casefold(),
            ),
        )


    def write_inventory(output: Path, records: list[InventoryRecord]) -> None:
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("w", encoding="utf-8-sig", newline="") as csv_file:
            writer = csv.writer(csv_file, delimiter=";")
            writer.writerow(
                [
                    "folder",
                    "relative_path",
                    "filename",
                    "extension",
                    "size_bytes",
                    "source_type",
                    "source_id",
                    "upload_date",
                    "subtitle_count",
                    "subtitles",
                ]
            )
            for record in records:
                writer.writerow(
                    [
                        record.folder,
                        record.relative_path,
                        record.filename,
                        record.extension,
                        record.size_bytes,
                        record.source_type,
                        record.source_id,
                        record.upload_date,
                        record.subtitle_count,
                        record.subtitles,
                    ]
                )


    def main() -> int:
        args = parse_args()
        root = args.root.resolve()
        output = (args.output or (root / "inventory.csv")).resolve()

        if not root.is_dir():
            print(f"[ERROR] Корневая папка не найдена: {root}")
            return 2

        print(f"[SCAN] Файловые метаданные: {root}")
        records = collect_inventory(root)
        try:
            write_inventory(output, records)
        except OSError as error:
            print(f"[ERROR] Не удалось записать {output}: {error}")
            return 2

        total_bytes = sum(record.size_bytes for record in records)
        with_subtitles = sum(record.subtitle_count > 0 for record in records)
        print(f"[OK] CSV: {output}")
        print(
            f"[SUMMARY] Видео: {len(records)}; с субтитрами: {with_subtitles}; "
            f"размер: {total_bytes / (1024**3):.2f} ГБ"
        )
        return 0
    return SimpleNamespace(**locals())

inventory = _build_inventory()


# ============================================================================
# Archive Report
# ============================================================================

def _build_archive_report():
    import argparse
    import csv
    from dataclasses import dataclass
    from pathlib import Path

    BASE_DIR = video_config.BASE_DIR
    collect_inventory = inventory.collect_inventory


    @dataclass(frozen=True)
    class ReportIssue:
        relative_path: str
        missing_id: bool
        missing_date: bool
        invalid_date: bool
        missing_author: bool
        missing_subtitles: bool

        @property
        def reasons(self) -> tuple[str, ...]:
            reasons = []
            if self.missing_id:
                reasons.append("нет ID")
            if self.missing_date:
                reasons.append("нет даты")
            if self.invalid_date:
                reasons.append("некорректная дата")
            if self.missing_author:
                reasons.append("нет папки автора")
            if self.missing_subtitles:
                reasons.append("нет субтитров")
            return tuple(reasons)


    def parse_args() -> argparse.Namespace:
        parser = argparse.ArgumentParser(
            description="Создаёт отчёт о неполных метаданных видео."
        )
        parser.add_argument("--root", type=Path, default=BASE_DIR)
        parser.add_argument(
            "--output",
            type=Path,
            help="Сохранить найденные проблемы в CSV.",
        )
        return parser.parse_args()


    def inspect_archive(root: Path) -> tuple[int, list[ReportIssue]]:
        records = collect_inventory(root)
        issues = []
        for record in records:
            date_text, valid_date = extract_filename_date(record.filename)
            issue = ReportIssue(
                relative_path=record.relative_path,
                missing_id=not bool(record.source_id),
                missing_date=date_text is None,
                invalid_date=date_text is not None and not valid_date,
                missing_author=not bool(record.folder),
                missing_subtitles=record.subtitle_count == 0,
            )
            if issue.reasons:
                issues.append(issue)
        return len(records), issues


    def write_report(output: Path, issues: list[ReportIssue]) -> None:
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("w", encoding="utf-8-sig", newline="") as csv_file:
            writer = csv.writer(csv_file, delimiter=";")
            writer.writerow(
                [
                    "relative_path",
                    "missing_id",
                    "missing_date",
                    "invalid_date",
                    "missing_author",
                    "missing_subtitles",
                    "reasons",
                ]
            )
            for issue in issues:
                writer.writerow(
                    [
                        issue.relative_path,
                        int(issue.missing_id),
                        int(issue.missing_date),
                        int(issue.invalid_date),
                        int(issue.missing_author),
                        int(issue.missing_subtitles),
                        " | ".join(issue.reasons),
                    ]
                )


    def main() -> int:
        args = parse_args()
        root = args.root.resolve()
        if not root.is_dir():
            print(f"[ERROR] Корневая папка не найдена: {root}")
            return 2

        checked, issues = inspect_archive(root)
        for issue in issues:
            print(f"[ISSUE] {issue.relative_path}: {', '.join(issue.reasons)}")

        if args.output:
            output = args.output.resolve()
            try:
                write_report(output, issues)
            except OSError as error:
                print(f"[ERROR] Не удалось записать {output}: {error}")
                return 2
            print(f"[OK] CSV: {output}")

        counters = {
            "без ID": sum(issue.missing_id for issue in issues),
            "без даты": sum(issue.missing_date for issue in issues),
            "неверная дата": sum(issue.invalid_date for issue in issues),
            "без автора": sum(issue.missing_author for issue in issues),
            "без субтитров": sum(issue.missing_subtitles for issue in issues),
        }
        details = "; ".join(f"{name}: {count}" for name, count in counters.items())
        print(f"[SUMMARY] Видео: {checked}; с проблемами: {len(issues)}; {details}")
        return 0
    return SimpleNamespace(**locals())


archive_report = _build_archive_report()


# ============================================================================
# Sync Download Archive
# ============================================================================

def _build_sync_download_archive():
    import argparse
    import os
    import shutil
    import tempfile
    from dataclasses import dataclass
    from datetime import datetime
    from pathlib import Path

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
            description=(
                "Удаляет из yt-dlp-archive.txt ID, для которых локального видео "
                "больше нет."
            )
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
        original_lines = archive_path.read_text(encoding="utf-8").splitlines()
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
        if not archive_path.is_file():
            print(f"[ERROR] Архив не найден: {archive_path}")
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
                answer = input_fn(
                    "Удалить перечисленные ID из архива? [Y/n]: "
                )
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
    return SimpleNamespace(**locals())

sync_download_archive = _build_sync_download_archive()


# ============================================================================
# Check Date
# ============================================================================

def _build_check_date():
    import argparse
    from pathlib import Path

    BASE_DIR = video_config.BASE_DIR
    VIDEO_EXTENSIONS = inventory.VIDEO_EXTENSIONS

    def parse_args():
        parser = argparse.ArgumentParser(
            description="Проверяет наличие и корректность даты в именах видео."
        )
        parser.add_argument("--root", type=Path, default=BASE_DIR)
        return parser.parse_args()

    def inspect_video_dates(root: Path):
        missing = []
        invalid = []
        checked = 0
        for path in root.rglob("*"):
            if (
                not path.is_file()
                or not path_selected(root, path)
                or path.suffix.lower() not in VIDEO_EXTENSIONS
            ):
                continue
            checked += 1
            date_text, is_valid = extract_filename_date(path.name)
            if date_text is None:
                missing.append(path)
            elif not is_valid:
                invalid.append((path, date_text))
        return checked, missing, invalid

    def main():
        args = parse_args()
        root = args.root.resolve()
        if not root.is_dir():
            print(f"[ERROR] Корневая папка не найдена: {root}")
            return 2

        checked, missing, invalid = inspect_video_dates(root)
        for path in missing:
            print(f"[MISSING] {path}")
        for path, date_text in invalid:
            print(f"[INVALID] {date_text}: {path}")
        print(
            f"[SUMMARY] Видео: {checked}; без даты: {len(missing)}; "
            f"некорректная дата: {len(invalid)}"
        )
        return 1 if missing or invalid else 0
    return SimpleNamespace(**locals())

check_date = _build_check_date()


# ============================================================================
# Check Resolution
# ============================================================================

def _build_check_resolution():
    import argparse
    import os
    import subprocess
    from pathlib import Path

    configured_command = video_config.configured_command
    load_config = video_config.load_config

    # Подсветка в терминале (ANSI escape codes)
    RESET = "\033[0m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    RED = "\033[91m"
    MAGENTA = "\033[95m"
    CYAN = "\033[96m"

    # Расширения, которые считаем видео
    VIDEO_EXTENSIONS = inventory.VIDEO_EXTENSIONS

    BASE_DIR = Path(__file__).resolve().parent

    def get_video_resolution(file_path, ffprobe="ffprobe"):
        """Возвращает (width, height) видео через ffprobe"""
        try:
            cmd = [
                ffprobe, "-v", "error",
                "-select_streams", "v:0",
                "-show_entries", "stream=width,height",
                "-of", "csv=s=x:p=0", str(file_path)
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if result.returncode == 0 and result.stdout.strip():
                width, height = result.stdout.strip().split("x")
                return int(width), int(height)
        except Exception:
            return None
        return None

    def colorize_resolution(height: int, resolution: str) -> str:
        """Возвращает цветную строку разрешения по высоте"""
        if height <= 720:
            return resolution
        elif height <= 1080:
            return f"{GREEN}{resolution}{RESET}"
        elif height <= 1440:
            return f"{YELLOW}{resolution}{RESET}"
        elif height <= 2160:
            return f"{RED}{resolution}{RESET}"
        else:
            return f"{MAGENTA}{resolution}{RESET}"

    def main():
        parser = argparse.ArgumentParser(description="Проверяет разрешение видео.")
        parser.add_argument("--root", type=Path, default=BASE_DIR)
        args = parser.parse_args()
        root_path = args.root.resolve()
        ffprobe = configured_command(load_config(), "ffprobe", "ffprobe")

        print(f"{CYAN}Проверка видеофайлов в {root_path} ...{RESET}\n")
        for root, _, files in os.walk(root_path):
            for file in files:
                if Path(file).suffix.lower() in VIDEO_EXTENSIONS:
                    file_path = Path(root) / file
                    if not path_selected(root_path, file_path):
                        continue
                    res = get_video_resolution(file_path, ffprobe)
                    if res:
                        w, h = res
                        resolution = f"{w}x{h}"
                        print(f"{file_path}  ->  {colorize_resolution(h, resolution)}")
                    else:
                        print(f"{file_path}  ->  [Не удалось определить]")
    return SimpleNamespace(**locals())

check_resolution = _build_check_resolution()


# ============================================================================
# Download Yt Favorites
# ============================================================================

def _build_download_yt_favorites():
    import argparse
    import subprocess
    import sys
    from pathlib import Path

    BASE_DIR = video_config.BASE_DIR
    configured_command = video_config.configured_command
    configured_path = video_config.configured_path
    load_config = video_config.load_config
    synchronize_archive = sync_download_archive.synchronize_archive


    PLAYLIST_URL = "https://www.youtube.com/playlist?list=WL"

    GREEN = "\033[92m"
    RED = "\033[91m"
    RESET = "\033[0m"


    def fix_unknown_channel(line: str) -> str:
        return line.replace("<unknown>", "Unknown")


    def simplify_terminal_line(line: str) -> str:
        cleaned = fix_unknown_channel(line).replace("\ufffd", "?")
        return "".join(
            character
            if character.isprintable() or character in "\r\n\t"
            else "?"
            for character in cleaned
        )

    def main() -> int:
        parser = argparse.ArgumentParser(description="Скачивает плейлист Смотреть позже.")
        parser.add_argument("--root", type=Path, default=BASE_DIR)
        args = parser.parse_args()
        root = args.root.resolve()
        config = load_config()
        cookies_file = configured_path(
            config, "cookies", env_name="YOUTUBE_COOKIES_FILE"
        )
        yt_dlp = configured_command(config, "yt_dlp", "yt-dlp")
        archive_file = root / "yt-dlp-archive.txt"
        output_template = (
            root
            / "%(uploader)s"
            / "%(title)s_%(upload_date>%d.%m.%Y)s [%(id)s].%(ext)s"
        )

        if not cookies_file or not cookies_file.exists():
            print(f"{RED}[ERROR]{RESET} Cookies-файл не найден: {cookies_file}")
            return 2

        if config.get("download", {}).get("sync_archive_before_download", True):
            print("[SYNC] Проверка yt-dlp-archive.txt по локальным видео...")
            sync_result = synchronize_archive(
                root,
                archive_file,
                apply=True,
                assume_yes=True,
            )
            if sync_result != 0:
                print(f"{RED}[ERROR]{RESET} Загрузка отменена из-за ошибки синхронизации.")
                return sync_result

        cmd = [
            yt_dlp,
            "--cookies",
            str(cookies_file),
            "--encoding",
            "utf-8",
            "--sponsorblock-mark",
            "all",
            "--sponsorblock-remove",
            "sponsor,interaction,selfpromo",
            "-f",
            (
                "(bestvideo[ext=mp4][height<=720]+bestaudio[ext=m4a])/"
                "best[ext=mp4][height<=720]/best[height<=720]"
            ),
            "--format-sort",
            "res:720",
            "--download-archive",
            str(archive_file),
            "--extractor-args",
            "youtubetab:skip=authcheck",
            "-o",
            str(output_template),
            PLAYLIST_URL,
        ]

        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(
                encoding="utf-8",
                errors="replace",
                line_buffering=True,
            )

        try:
            with subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                encoding="utf-8",
                errors="replace",
            ) as process:
                assert process.stdout is not None
                for raw_line in process.stdout:
                    line = simplify_terminal_line(raw_line.strip())
                    if not line:
                        continue
                    if "[download]" in line:
                        print(f"\r{GREEN}[Processing]{RESET} {line}", end="")
                    elif "error" in line.lower():
                        print(f"\n{RED}[ERROR]{RESET} {line}")

                return_code = process.wait()
        except FileNotFoundError:
            print(f"{RED}[ERROR]{RESET} Не найдена программа: {yt_dlp}")
            return 2

        if return_code != 0:
            print(f"\n{RED}[ERROR]{RESET} yt-dlp завершился с кодом {return_code}")
        else:
            print()

        return return_code
    return SimpleNamespace(**locals())

download_yt_favorites = _build_download_yt_favorites()


# ============================================================================
# Update Embedded SponsorBlock Bookmarks
# ============================================================================

def _build_update_bookmarks():
    import argparse
    import json
    import subprocess
    import tempfile
    import time
    import uuid
    from pathlib import Path

    BASE_DIR = video_config.BASE_DIR
    configured_command = video_config.configured_command
    configured_path = video_config.configured_path
    get_cached_field = video_metadata_cache.get_field
    get_cached_value = video_metadata_cache.get_value
    load_cache = video_metadata_cache.load_cache
    load_config = video_config.load_config
    save_cache = video_metadata_cache.save_cache
    set_cached_field = video_metadata_cache.set_field
    set_cached_value = video_metadata_cache.set_value
    VIDEO_EXTENSIONS = inventory.VIDEO_EXTENSIONS
    extract_source = inventory.extract_source
    relative_path = video_journal.relative_path
    write_journal_event = video_journal.write_journal_event

    MARK_CATEGORIES = "all"
    REMOVE_CATEGORIES = "sponsor,interaction,selfpromo"
    REMOVE_CATEGORY_SET = {"sponsor", "interaction", "selfpromo"}

    def parse_args() -> argparse.Namespace:
        config = load_config()
        bookmarks_config = config.get("bookmarks", {})
        parser = argparse.ArgumentParser(
            description=(
                "Сверяет встроенные главы видео с актуальными данными "
                "SponsorBlock и при необходимости обновляет контейнер."
            )
        )
        parser.add_argument("--root", type=Path, default=BASE_DIR)
        parser.add_argument(
            "--cookies",
            type=Path,
            default=configured_path(
                config,
                "cookies",
                env_name="YOUTUBE_COOKIES_FILE",
            ),
            help="Путь к cookies-файлу yt-dlp.",
        )
        parser.add_argument(
            "--timeout",
            type=int,
            default=90,
            help="Таймаут одного запроса в секундах.",
        )
        parser.add_argument(
            "--pause-seconds",
            type=float,
            default=float(bookmarks_config.get("pause_seconds", 3)),
            help="Пауза между запросами к YouTube в секундах.",
        )
        parser.add_argument(
            "--max-retries",
            type=int,
            default=int(bookmarks_config.get("max_retries", 2)),
            help="Сколько раз повторять временно ограниченный запрос к YouTube.",
        )
        parser.add_argument(
            "--retry-backoff-seconds",
            type=float,
            default=float(bookmarks_config.get("retry_backoff_seconds", 30)),
            help="Пауза перед повтором после rate limit YouTube.",
        )
        parser.add_argument(
            "--ffmpeg-timeout",
            type=int,
            default=int(bookmarks_config.get("ffmpeg_timeout_seconds", 600)),
            help="Таймаут переписи глав через ffmpeg в секундах.",
        )
        parser.add_argument(
            "--yes",
            action="store_true",
            help="Не запрашивать подтверждение перед --apply.",
        )
        mode = parser.add_mutually_exclusive_group()
        mode.add_argument(
            "--apply",
            action="store_true",
            help="Перезаписать встроенные главы в контейнере без перекодирования.",
        )
        mode.add_argument(
            "--dry-run",
            action="store_true",
            help="Только показать план обновления (режим по умолчанию).",
        )
        args = parser.parse_args()
        args.yt_dlp = configured_command(config, "yt_dlp", "yt-dlp")
        args.ffprobe = configured_command(config, "ffprobe", "ffprobe")
        args.ffmpeg = configured_command(config, "ffmpeg", "ffmpeg")
        return args

    def iter_youtube_videos(root: Path) -> list[tuple[Path, str]]:
        videos = []
        for path in sorted(root.rglob("*")):
            name_lower = path.name.casefold()
            if (
                not path.is_file()
                or not path_selected(root, path)
                or path.suffix.lower() not in VIDEO_EXTENSIONS
                or ".bookmarks." in name_lower
                or ".redownload." in name_lower
            ):
                continue
            source_type, source_id = extract_source(path.name)
            if source_type == "youtube" and source_id:
                videos.append((path, source_id))
        return videos

    def chapter_title(chapter: dict) -> str:
        tags = chapter.get("tags") or {}
        value = chapter.get("title") or tags.get("title") or ""
        return str(value).strip()

    def chapter_category(chapter: dict) -> str:
        tags = chapter.get("tags") or {}
        value = chapter.get("category") or tags.get("sponsorblock_category") or ""
        return str(value).strip()

    def normalize_chapters(chapters: list[dict]) -> list[dict[str, object]]:
        normalized = []
        for chapter in chapters:
            try:
                start = round(float(chapter.get("start_time", 0.0)), 3)
                end = round(float(chapter.get("end_time", start)), 3)
            except (TypeError, ValueError):
                continue
            if end < start:
                end = start
            normalized.append(
                {
                    "start_time": start,
                    "end_time": end,
                    "title": chapter_title(chapter),
                    "category": chapter_category(chapter),
                }
            )
        return normalized

    def extract_removed_segments(chapters: list[dict[str, object]]) -> list[dict[str, object]]:
        removed = []
        for chapter in chapters:
            category = str(chapter.get("category") or "").strip()
            if category not in REMOVE_CATEGORY_SET:
                continue
            try:
                start = round(float(chapter.get("start_time", 0.0)), 3)
                end = round(float(chapter.get("end_time", start)), 3)
            except (TypeError, ValueError):
                continue
            if end <= start:
                continue
            removed.append(
                {
                    "start_time": start,
                    "end_time": end,
                    "title": str(chapter.get("title") or "").strip(),
                    "category": category,
                }
            )
        return removed

    def cache_sponsorblock_metadata(
        metadata_cache_state: dict,
        video_id: str,
        remote_info: dict[str, object],
    ) -> None:
        chapters = list(remote_info.get("chapters") or [])
        removed_segments = extract_removed_segments(chapters)
        set_cached_field(
            metadata_cache_state,
            "youtube",
            video_id,
            "sponsorblock_trimmed",
            "yes" if removed_segments else "no",
        )
        set_cached_value(
            metadata_cache_state,
            "youtube",
            video_id,
            "sponsorblock_removed_segments",
            removed_segments,
        )
        duration = remote_info.get("duration")
        if duration is None:
            return
        try:
            duration_value = round(float(duration), 3)
        except (TypeError, ValueError):
            return
        set_cached_value(
            metadata_cache_state,
            "youtube",
            video_id,
            "sponsorblock_source_duration",
            duration_value,
        )

    def is_rate_limited_message(message: str | None) -> bool:
        if not message:
            return False
        normalized = message.casefold()
        return (
            "rate-limited by youtube" in normalized
            or "try again later" in normalized
            or "recommended to use `-t sleep`" in normalized
        )

    def fetch_remote_video_info(
        video_id: str,
        *,
        yt_dlp: str,
        cookies: Path | None,
        timeout: int,
        max_retries: int,
        retry_backoff_seconds: float,
    ) -> tuple[dict[str, object] | None, str | None]:
        cmd = [
            yt_dlp,
            "--skip-download",
            "--no-playlist",
            "--dump-single-json",
            "--encoding",
            "utf-8",
            "--sponsorblock-mark",
            MARK_CATEGORIES,
            "--sponsorblock-remove",
            REMOVE_CATEGORIES,
            f"https://www.youtube.com/watch?v={video_id}",
        ]
        if cookies and cookies.exists():
            cmd[1:1] = ["--cookies", str(cookies)]

        attempts = max(1, max_retries + 1)
        for attempt in range(1, attempts + 1):
            try:
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=timeout,
                )
            except FileNotFoundError:
                return None, f"не найдена программа {yt_dlp}"
            except subprocess.TimeoutExpired:
                return None, f"таймаут запроса ({timeout} с)"

            if result.returncode == 0:
                try:
                    payload = json.loads(result.stdout)
                except json.JSONDecodeError as error:
                    return None, f"yt-dlp вернул некорректный JSON: {error}"

                duration = payload.get("duration")
                try:
                    duration_value = float(duration) if duration not in (None, "") else None
                except (TypeError, ValueError):
                    duration_value = None
                return {
                    "chapters": normalize_chapters(payload.get("chapters") or []),
                    "duration": duration_value,
                }, None

            message = result.stderr.strip().splitlines()
            error_text = message[-1] if message else f"код ошибки {result.returncode}"
            if attempt < attempts and is_rate_limited_message(error_text):
                wait_seconds = max(0.0, retry_backoff_seconds * attempt)
                print(
                    f"[WAIT] YouTube временно ограничил запрос для {video_id}; "
                    f"повтор {attempt}/{attempts - 1} через {wait_seconds:.1f} с."
                )
                if wait_seconds > 0:
                    time.sleep(wait_seconds)
                continue
            return None, error_text

        return None, "не удалось получить данные YouTube"

    def read_embedded_chapters(
        video_path: Path,
        *,
        ffprobe: str,
        timeout: int,
    ) -> tuple[list[dict[str, object]] | None, str | None]:
        cmd = [
            ffprobe,
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_chapters",
            str(video_path),
        ]
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
            )
        except FileNotFoundError:
            return None, f"не найдена программа {ffprobe}"
        except subprocess.TimeoutExpired:
            return None, f"таймаут ffprobe ({timeout} с)"

        if result.returncode != 0:
            message = result.stderr.strip().splitlines()
            return None, message[-1] if message else f"ffprobe завершился с кодом {result.returncode}"

        try:
            payload = json.loads(result.stdout or "{}")
        except json.JSONDecodeError as error:
            return None, f"ffprobe вернул некорректный JSON: {error}"
        return normalize_chapters(payload.get("chapters") or []), None

    def read_local_duration(
        video_path: Path,
        *,
        ffprobe: str,
        timeout: int,
    ) -> tuple[float | None, str | None]:
        cmd = [
            ffprobe,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(video_path),
        ]
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
            )
        except FileNotFoundError:
            return None, f"не найдена программа {ffprobe}"
        except subprocess.TimeoutExpired:
            return None, f"таймаут ffprobe ({timeout} с)"

        if result.returncode != 0:
            message = result.stderr.strip().splitlines()
            return None, message[-1] if message else f"ffprobe завершился с кодом {result.returncode}"

        text = result.stdout.strip()
        if not text:
            return None, "ffprobe не вернул длительность"
        try:
            return float(text), None
        except ValueError:
            return None, f"ffprobe вернул некорректную длительность: {text!r}"

    def chapters_equal(
        current: list[dict[str, object]],
        target: list[dict[str, object]],
    ) -> bool:
        return current == target

    def escape_ffmetadata_value(value: str) -> str:
        return (
            value.replace("\\", "\\\\")
            .replace("\n", " ")
            .replace("\r", " ")
            .replace(";", r"\;")
            .replace("#", r"\#")
            .replace("=", r"\=")
        )

    def build_ffmetadata(chapters: list[dict[str, object]]) -> str:
        lines = [";FFMETADATA1"]
        for chapter in chapters:
            start_ms = max(0, int(round(float(chapter["start_time"]) * 1000)))
            end_ms = max(start_ms, int(round(float(chapter["end_time"]) * 1000)))
            title = escape_ffmetadata_value(str(chapter.get("title") or ""))
            category = str(chapter.get("category") or "").strip()
            if category:
                title = escape_ffmetadata_value(
                    f"[{category}] {str(chapter.get('title') or '').strip()}".strip()
                )
            lines.extend(
                [
                    "",
                    "[CHAPTER]",
                    "TIMEBASE=1/1000",
                    f"START={start_ms}",
                    f"END={end_ms}",
                    f"title={title}",
                ]
            )
        return "\n".join(lines) + "\n"

    def rewrite_embedded_chapters(
        video_path: Path,
        chapters: list[dict[str, object]],
        *,
        ffmpeg: str,
        timeout: int,
    ) -> None:
        fd, temp_name = tempfile.mkstemp(
            suffix=video_path.suffix,
            prefix=f"{video_path.stem}.bookmarks.",
            dir=str(video_path.parent),
        )
        os.close(fd)
        temp_output = Path(temp_name)
        metadata_path: Path | None = None
        try:
            cmd = [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-i",
                str(video_path),
            ]
            if chapters:
                metadata_fd, metadata_name = tempfile.mkstemp(
                    suffix=".ffmetadata",
                    prefix="sponsorblock.",
                    dir=str(video_path.parent),
                )
                os.close(metadata_fd)
                metadata_path = Path(metadata_name)
                metadata_path.write_text(
                    build_ffmetadata(chapters),
                    encoding="utf-8",
                )
                cmd.extend(
                    [
                        "-f",
                        "ffmetadata",
                        "-i",
                        str(metadata_path),
                    ]
                )
            cmd.extend(
                [
                    "-map",
                    "0",
                    "-c",
                    "copy",
                    "-map_metadata",
                    "0",
                ]
            )
            if chapters:
                cmd.extend(
                    [
                        "-map_chapters",
                        "1",
                        "-movflags",
                        "use_metadata_tags",
                    ]
                )
            else:
                cmd.extend(["-map_chapters", "-1"])
            cmd.append(str(temp_output))

            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
            )
            if result.returncode != 0:
                message = result.stderr.strip().splitlines()
                raise RuntimeError(
                    message[-1] if message else f"ffmpeg завершился с кодом {result.returncode}"
                )
            temp_output.replace(video_path)
        except subprocess.TimeoutExpired as error:
            raise RuntimeError(f"ffmpeg превысил таймаут ({timeout} с)") from error
        except FileNotFoundError as error:
            raise RuntimeError(f"не найдена программа {ffmpeg}") from error
        finally:
            if metadata_path and metadata_path.exists():
                metadata_path.unlink()
            if temp_output.exists():
                temp_output.unlink()

    def chapter_label(chapter: dict[str, object]) -> str:
        title = str(chapter.get("title") or "").strip()
        category = str(chapter.get("category") or "").strip()
        if title and category:
            return f"[{category}] {title}"
        if title:
            return title
        if category:
            return f"[{category}]"
        return "(без названия)"

    def short_chapter_diff(
        current: list[dict[str, object]],
        target: list[dict[str, object]],
        *,
        limit: int = 3,
    ) -> list[str]:
        current_labels = [chapter_label(chapter) for chapter in current]
        target_labels = [chapter_label(chapter) for chapter in target]
        details: list[str] = []

        removed = [label for label in current_labels if label not in target_labels]
        added = [label for label in target_labels if label not in current_labels]

        for label in removed[:limit]:
            details.append(f"удалена: {label}")
        remaining = limit - len(details)
        if remaining > 0:
            for label in added[:remaining]:
                details.append(f"добавлена: {label}")

        if details:
            return details[:limit]

        if len(current) == len(target) and current != target:
            return ["обновлены таймкоды без изменения названий"]
        return []

    def summarize_transition(
        current: list[dict[str, object]],
        target: list[dict[str, object]],
    ) -> str:
        if not current and target:
            base = f"добавить главы: {len(target)}"
        elif current and not target:
            base = f"удалить главы: {len(current)}"
        else:
            base = f"обновить главы: {len(current)} -> {len(target)}"
        details = short_chapter_diff(current, target)
        if details:
            return base + " [" + "; ".join(details) + "]"
        return base

    def is_trimmed_local_video(
        local_duration: float | None,
        remote_duration: float | None,
        *,
        tolerance_seconds: float = 1.0,
    ) -> bool:
        if local_duration is None or remote_duration is None:
            return False
        return (remote_duration - local_duration) > tolerance_seconds

    def simplify_terminal_line(line: str) -> str:
        cleaned = line.replace("\ufffd", "?")
        return "".join(
            character
            if character.isprintable() or character in "\r\n\t"
            else "?"
            for character in cleaned
        )

    def redownload_video(
        video_path: Path,
        video_id: str,
        *,
        yt_dlp: str,
        cookies: Path | None,
        timeout: int,
    ) -> tuple[bool, str | None]:
        fd, temp_name = tempfile.mkstemp(
            suffix=video_path.suffix,
            prefix=f"{video_path.stem}.redownload.",
            dir=str(video_path.parent),
        )
        os.close(fd)
        temp_output = Path(temp_name)
        temp_output.unlink(missing_ok=True)

        cmd = [
            yt_dlp,
            "--no-playlist",
            "--encoding",
            "utf-8",
            "--sponsorblock-mark",
            MARK_CATEGORIES,
            "--sponsorblock-remove",
            REMOVE_CATEGORIES,
            "--force-overwrites",
            "--no-download-archive",
            "-f",
            (
                "(bestvideo[ext=mp4][height<=720]+bestaudio[ext=m4a])/"
                "best[ext=mp4][height<=720]/best[height<=720]"
            ),
            "--format-sort",
            "res:720",
            "--merge-output-format",
            video_path.suffix.lstrip("."),
            "-o",
            str(temp_output),
            f"https://www.youtube.com/watch?v={video_id}",
        ]
        if cookies and cookies.exists():
            cmd[1:1] = ["--cookies", str(cookies)]

        try:
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
        except FileNotFoundError:
            return False, f"не найдена программа {yt_dlp}"

        try:
            assert process.stdout is not None
            for raw_line in process.stdout:
                line = simplify_terminal_line(raw_line.strip())
                if not line:
                    continue
                if "[download]" in line:
                    print(f"\r[REDOWNLOAD] {line}", end="")
                elif "error" in line.lower():
                    print(f"\n[ERROR] {line}")
            return_code = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            process.kill()
            return False, f"таймаут загрузки ({timeout} с)"

        if return_code != 0:
            return False, f"yt-dlp завершился с кодом {return_code}"

        if not temp_output.exists():
            matches = sorted(video_path.parent.glob(f"{temp_output.stem}*{video_path.suffix}"))
            if matches:
                temp_output = matches[0]
            else:
                return False, "перекачивание завершилось без выходного файла"

        temp_output.replace(video_path)
        print()
        return True, None

    def choose_trimmed_action(relative: str, *, input_fn=input) -> str:
        prompt = (
            f"[TRIMMED] {relative}: видео уже физически обрезано. "
            "Выберите действие: "
            "[R] перекачать это видео, "
            "[S] пропустить, "
            "[RA] перекачать все такие видео, "
            "[SA] пропустить все: "
        )
        try:
            answer = input_fn(prompt).strip().upper()
        except (EOFError, KeyboardInterrupt):
            return "skip"
        return {
            "R": "redownload",
            "S": "skip",
            "RA": "redownload_all",
            "SA": "skip_all",
        }.get(answer, "skip")

    BOOKMARKS_SCAN_STATE_FILE = "bookmarks-scan-state.json"
    BOOKMARKS_PLAN_REPORT_FILE = "bookmarks-plan.txt"

    def scan_state_path(root: Path) -> Path:
        return root / video_journal.JOURNAL_DIR_NAME / BOOKMARKS_SCAN_STATE_FILE

    def plan_report_path(root: Path) -> Path:
        return root / video_journal.JOURNAL_DIR_NAME / BOOKMARKS_PLAN_REPORT_FILE

    def load_scan_state(root: Path, *, apply_mode: bool) -> dict | None:
        path = scan_state_path(root)
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(payload, dict):
            return None
        if payload.get("mode") != ("apply" if apply_mode else "dry-run"):
            return None
        records = payload.get("records")
        if not isinstance(records, dict):
            return None
        payload["records"] = {
            str(key): value
            for key, value in records.items()
            if isinstance(value, dict)
        }
        return payload

    def save_scan_state(root: Path, state: dict) -> None:
        path = scan_state_path(root)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def clear_scan_state(root: Path) -> None:
        path = scan_state_path(root)
        try:
            path.unlink()
        except FileNotFoundError:
            return

    def write_plan_report(
        root: Path,
        *,
        apply_mode: bool,
        up_to_date: int,
        planned: list[tuple[Path, list[dict[str, object]], list[dict[str, object]]]],
        pending_redownloads: list[tuple[Path, str, str, dict[str, object]]],
        trimmed: int,
        retry_later: int,
        failed: int,
        private_or_unavailable: int,
        other_errors: int,
    ) -> Path:
        path = plan_report_path(root)
        path.parent.mkdir(parents=True, exist_ok=True)
        lines = [
            "SponsorBlock bookmarks plan report",
            f"mode: {'apply' if apply_mode else 'dry-run'}",
            f"up_to_date: {up_to_date}",
            f"planned_updates: {len(planned)}",
            f"pending_redownloads: {len(pending_redownloads)}",
            f"trimmed_detected: {trimmed}",
            f"retry_later: {retry_later}",
            f"private_or_unavailable: {private_or_unavailable}",
            f"other_errors: {other_errors}",
            f"failed: {failed}",
            "",
            "Planned chapter updates:",
        ]
        if planned:
            for video_path, current, target in planned:
                lines.append(
                    f"- {relative_path(root, video_path)} :: {summarize_transition(current, target)}"
                )
        else:
            lines.append("- none")
        lines.extend(["", "Pending redownloads:"])
        if pending_redownloads:
            for _, _, relative, _ in pending_redownloads:
                lines.append(f"- {relative}")
        else:
            lines.append("- none")
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path

    def is_retry_later_error(message: str | None) -> bool:
        return is_rate_limited_message(message)

    def is_private_or_unavailable_error(message: str | None) -> bool:
        if not message:
            return False
        normalized = message.casefold()
        markers = (
            "private video",
            "sign in if you've been granted access",
            "video unavailable",
            "this video is unavailable",
            "members-only content",
            "login required",
        )
        return any(marker in normalized for marker in markers) and not is_retry_later_error(message)

    def classify_remote_error(message: str | None) -> str:
        if is_retry_later_error(message):
            return "retry_later"
        if is_private_or_unavailable_error(message):
            return "private_or_unavailable"
        return "other_error"

    def state_record_for_scan(
        *,
        video_id: str,
        result: str,
        current: list[dict[str, object]] | None = None,
        target: list[dict[str, object]] | None = None,
        remote_info: dict[str, object] | None = None,
        error: str | None = None,
    ) -> dict[str, object]:
        record: dict[str, object] = {
            "video_id": video_id,
            "result": result,
        }
        if current is not None:
            record["current"] = current
        if target is not None:
            record["target"] = target
        if remote_info is not None:
            record["remote_info"] = remote_info
        if error:
            record["error"] = error
        if result == "planned":
            record["apply_result"] = "pending"
        if result == "trimmed_redownload":
            record["redownload_result"] = "pending"
        return record

    def rebuild_scan_results(
        root: Path,
        videos: list[tuple[Path, str]],
        state: dict | None,
        *,
        apply_mode: bool,
    ) -> tuple[
        dict[str, dict[str, object]],
        list[tuple[Path, list[dict[str, object]], list[dict[str, object]]]],
        list[tuple[Path, str, str, dict[str, object]]],
        int,
        int,
        int,
        int,
        int,
        str | None,
    ]:
        records = state.get("records", {}) if state else {}
        current_relatives = {relative_path(root, video_path) for video_path, _ in videos}
        filtered_records = {
            relative: record
            for relative, record in records.items()
            if relative in current_relatives and isinstance(record, dict)
        }
        processed_records = {
            relative: record
            for relative, record in filtered_records.items()
            if str(record.get("result") or "") != "retry_later"
        }
        planned: list[tuple[Path, list[dict[str, object]], list[dict[str, object]]]] = []
        pending_redownloads: list[tuple[Path, str, str, dict[str, object]]] = []
        up_to_date = failed = trimmed = retry_later = private_or_unavailable = other_errors = 0
        trimmed_policy: str | None = None
        path_map = {relative_path(root, video_path): (video_path, video_id) for video_path, video_id in videos}
        for relative, record in filtered_records.items():
            video_path, video_id = path_map[relative]
            result = str(record.get("result") or "")
            if result == "up_to_date":
                up_to_date += 1
            elif result == "error":
                failed += 1
                error_kind = str(record.get("error_kind") or "other_error")
                if error_kind == "private_or_unavailable":
                    private_or_unavailable += 1
                else:
                    other_errors += 1
            elif result == "retry_later":
                retry_later += 1
            elif result == "planned":
                current = record.get("current")
                target = record.get("target")
                if isinstance(current, list) and isinstance(target, list):
                    if record.get("apply_result") != "success":
                        planned.append((video_path, current, target))
            elif result == "trimmed":
                trimmed += 1
            elif result == "trimmed_skip":
                trimmed += 1
                if apply_mode:
                    policy = str(record.get("trimmed_policy") or "")
                    if policy == "skip_all":
                        trimmed_policy = "skip_all"
            elif result == "trimmed_redownload":
                trimmed += 1
                if apply_mode and record.get("redownload_result") != "success":
                    remote_info = record.get("remote_info")
                    if isinstance(remote_info, dict):
                        pending_redownloads.append((video_path, video_id, relative, remote_info))
                    policy = str(record.get("trimmed_policy") or "")
                    if policy == "redownload_all":
                        trimmed_policy = "redownload_all"
        return (
            processed_records,
            planned,
            pending_redownloads,
            up_to_date,
            failed,
            trimmed,
            retry_later,
            private_or_unavailable,
            other_errors,
            trimmed_policy,
        )

    def persist_scan_progress(
        root: Path,
        state: dict,
        metadata_cache_state: dict,
    ) -> None:
        save_scan_state(root, state)
        save_cache(root, metadata_cache_state)

    def format_eta(seconds: float) -> str:
        seconds_int = max(0, int(round(seconds)))
        hours, remainder = divmod(seconds_int, 3600)
        minutes, secs = divmod(remainder, 60)
        if hours:
            return f"{hours}h {minutes}m"
        if minutes:
            return f"{minutes}m {secs}s"
        return f"{secs}s"

    def progress_prefix(
        label: str,
        current: int,
        total: int,
        item_label: str,
        started_at: float,
    ) -> str:
        elapsed = max(0.0, time.monotonic() - started_at)
        average = elapsed / current if current else 0.0
        remaining = max(0, total - current)
        eta = format_eta(average * remaining)
        return f"[{label}] {current}/{total} {item_label} (ETA {eta})"

    def build_progress_tracker(window_size: int = 20, min_samples_for_eta: int = 5):
        started_at = time.monotonic()
        last_checkpoint = started_at
        recent_durations: list[float] = []

        def line(label: str, current: int, total: int, item_label: str) -> str:
            completed = max(0, current - 1)
            if completed < min_samples_for_eta:
                eta_text = "collecting..."
            else:
                if recent_durations:
                    average = sum(recent_durations) / len(recent_durations)
                else:
                    average = max(0.0, time.monotonic() - started_at) / completed
                remaining = max(0, total - completed)
                eta_text = format_eta(average * remaining)
            return f"[{label}] {current}/{total} {item_label} (ETA {eta_text})"

        def tick() -> None:
            nonlocal last_checkpoint
            now = time.monotonic()
            recent_durations.append(max(0.0, now - last_checkpoint))
            if len(recent_durations) > window_size:
                del recent_durations[0]
            last_checkpoint = now

        return SimpleNamespace(line=line, tick=tick)

    def confirm_apply(plan_count: int, assume_yes: bool, input_fn=input) -> bool:
        if assume_yes:
            return True
        try:
            answer = input_fn(
                "Перезаписать встроенные главы в перечисленных видео? [Y/n]: "
            )
        except (EOFError, KeyboardInterrupt):
            return False
        return is_affirmative_reply(answer)

    def main() -> int:
        args = parse_args()
        root = args.root.resolve()
        if args.cookies and not args.cookies.exists():
            print(f"[ERROR] Cookies-файл не найден: {args.cookies}")
            return 2

        videos = iter_youtube_videos(root)
        print(f"[{'APPLY' if args.apply else 'DRY-RUN'}] YouTube-видео: {len(videos)}")

        metadata_cache_state = load_cache(root)
        scan_state = load_scan_state(root, apply_mode=args.apply) or {
            "mode": "apply" if args.apply else "dry-run",
            "scan_complete": False,
            "records": {},
        }
        (
            saved_records,
            planned,
            pending_redownloads,
            up_to_date,
            failed,
            trimmed,
            retry_later,
            private_or_unavailable,
            other_errors,
            trimmed_policy,
        ) = rebuild_scan_results(root, videos, scan_state, apply_mode=args.apply)
        if saved_records:
            print(
                f"[RESUME] Найдены результаты предыдущего прохода: "
                f"{len(saved_records)}/{len(videos)} видео уже обработано."
            )
        pending_retry_records = [
            relative
            for relative, record in scan_state.get("records", {}).items()
            if isinstance(record, dict) and record.get("result") == "retry_later"
        ]
        if pending_retry_records:
            print(
                f"[RESUME] Временный бан YouTube прервал проверку для "
                f"{len(pending_retry_records)} видео; они будут проверены повторно."
            )

        total_videos = len(videos)
        check_tracker = build_progress_tracker()
        for index, (video_path, video_id) in enumerate(videos, start=1):
            relative = relative_path(root, video_path)
            if relative in saved_records:
                continue
            print(check_tracker.line("CHECK", index, total_videos, relative))
            current, current_error = read_embedded_chapters(
                video_path,
                ffprobe=args.ffprobe,
                timeout=args.timeout,
            )
            if current_error:
                print(f"[ERROR] {relative}: {current_error}")
                failed += 1
                scan_state["records"][relative] = {
                    **state_record_for_scan(
                        video_id=video_id,
                        result="error",
                        error=current_error,
                    ),
                    "error_kind": "other_error",
                }
                other_errors += 1
                persist_scan_progress(root, scan_state, metadata_cache_state)
                check_tracker.tick()
                continue

            local_duration, duration_error = read_local_duration(
                video_path,
                ffprobe=args.ffprobe,
                timeout=args.timeout,
            )
            if duration_error:
                print(f"[ERROR] {relative}: {duration_error}")
                failed += 1
                scan_state["records"][relative] = {
                    **state_record_for_scan(
                        video_id=video_id,
                        result="error",
                        error=duration_error,
                    ),
                    "error_kind": "other_error",
                }
                other_errors += 1
                persist_scan_progress(root, scan_state, metadata_cache_state)
                check_tracker.tick()
                continue

            remote_info, remote_error = fetch_remote_video_info(
                video_id,
                yt_dlp=args.yt_dlp,
                cookies=args.cookies,
                timeout=args.timeout,
                max_retries=args.max_retries,
                retry_backoff_seconds=args.retry_backoff_seconds,
            )
            if remote_error:
                print(f"[ERROR] {relative}: {remote_error}")
                error_kind = classify_remote_error(remote_error)
                if error_kind == "retry_later":
                    retry_later += 1
                    scan_state["records"][relative] = state_record_for_scan(
                        video_id=video_id,
                        result="retry_later",
                        error=remote_error,
                    )
                else:
                    failed += 1
                    if error_kind == "private_or_unavailable":
                        private_or_unavailable += 1
                    else:
                        other_errors += 1
                    scan_state["records"][relative] = {
                        **state_record_for_scan(
                            video_id=video_id,
                            result="error",
                            error=remote_error,
                        ),
                        "error_kind": error_kind,
                    }
                persist_scan_progress(root, scan_state, metadata_cache_state)
                if args.pause_seconds > 0 and index < total_videos:
                    time.sleep(args.pause_seconds)
                check_tracker.tick()
                continue

            assert current is not None
            assert remote_info is not None
            cache_sponsorblock_metadata(metadata_cache_state, video_id, remote_info)
            target = remote_info["chapters"]
            remote_duration = remote_info["duration"]
            cached_trimmed = get_cached_field(
                metadata_cache_state,
                "youtube",
                video_id,
                "sponsorblock_trimmed",
            )
            if cached_trimmed == "yes":
                trimmed_detected = True
            elif cached_trimmed == "no":
                trimmed_detected = False
            else:
                trimmed_detected = is_trimmed_local_video(local_duration, remote_duration)

            if trimmed_detected:
                trimmed += 1
                if args.apply:
                    action = trimmed_policy or choose_trimmed_action(relative)
                    if action == "redownload_all":
                        trimmed_policy = "redownload_all"
                        pending_redownloads.append((video_path, video_id, relative, remote_info))
                        scan_state["records"][relative] = {
                            **state_record_for_scan(
                                video_id=video_id,
                                result="trimmed_redownload",
                                remote_info=remote_info,
                            ),
                            "trimmed_policy": "redownload_all",
                        }
                    elif action == "skip_all":
                        trimmed_policy = "skip_all"
                        print(f"[SKIP] {relative}: пропущено для всех уже обрезанных видео.")
                        scan_state["records"][relative] = {
                            **state_record_for_scan(
                                video_id=video_id,
                                result="trimmed_skip",
                                remote_info=remote_info,
                            ),
                            "trimmed_policy": "skip_all",
                        }
                    elif action == "redownload":
                        pending_redownloads.append((video_path, video_id, relative, remote_info))
                        scan_state["records"][relative] = state_record_for_scan(
                            video_id=video_id,
                            result="trimmed_redownload",
                            remote_info=remote_info,
                        )
                    else:
                        print(f"[SKIP] {relative}: уже обрезано, обновление глав небезопасно.")
                        scan_state["records"][relative] = state_record_for_scan(
                            video_id=video_id,
                            result="trimmed_skip",
                            remote_info=remote_info,
                        )
                else:
                    print(
                        f"[TRIMMED] {relative}: видео уже обрезано; "
                        "безопасное обновление глав требует перекачивания."
                    )
                    scan_state["records"][relative] = state_record_for_scan(
                        video_id=video_id,
                        result="trimmed",
                        remote_info=remote_info,
                    )
                persist_scan_progress(root, scan_state, metadata_cache_state)
                check_tracker.tick()
                continue

            if chapters_equal(current, target):
                up_to_date += 1
                scan_state["records"][relative] = state_record_for_scan(
                    video_id=video_id,
                    result="up_to_date",
                )
                persist_scan_progress(root, scan_state, metadata_cache_state)
                if args.pause_seconds > 0 and index < total_videos:
                    time.sleep(args.pause_seconds)
                check_tracker.tick()
                continue

            print(
                f"[PLAN] {relative}: "
                f"{summarize_transition(current, target)}"
            )
            planned.append((video_path, current, target))
            scan_state["records"][relative] = state_record_for_scan(
                video_id=video_id,
                result="planned",
                current=current,
                target=target,
            )
            persist_scan_progress(root, scan_state, metadata_cache_state)
            if args.pause_seconds > 0 and index < total_videos:
                time.sleep(args.pause_seconds)
            check_tracker.tick()

        scan_state["scan_complete"] = retry_later == 0
        persist_scan_progress(root, scan_state, metadata_cache_state)
        report_path = write_plan_report(
            root,
            apply_mode=args.apply,
            up_to_date=up_to_date,
            planned=planned,
            pending_redownloads=pending_redownloads,
            trimmed=trimmed,
            retry_later=retry_later,
            failed=failed,
            private_or_unavailable=private_or_unavailable,
            other_errors=other_errors,
        )
        print(f"[REPORT] План сохранён: {relative_path(root, report_path)}")
        print(
            f"[SUMMARY] Актуальны: {up_to_date}; "
            f"к обновлению: {len(planned)}; "
            f"требуют перекачивания: {len(pending_redownloads) if args.apply else trimmed}; "
            f"временных блокировок: {retry_later}; "
            f"private/unavailable: {private_or_unavailable}; "
            f"прочих ошибок: {other_errors}; "
            f"ошибок: {failed}"
        )

        if not args.apply or (not planned and not pending_redownloads):
            save_cache(root, metadata_cache_state)
            if retry_later:
                persist_scan_progress(root, scan_state, metadata_cache_state)
            else:
                clear_scan_state(root)
            return 1 if failed else 0

        if planned and not confirm_apply(len(planned), args.yes):
            print("[CANCEL] Обновление закладок не выполнялось. План сохранён для продолжения.")
            save_cache(root, metadata_cache_state)
            return 0

        run_id = uuid.uuid4().hex
        write_journal_event(
            root,
            {
                "event": "run_start",
                "action": "bookmarks",
                "run_id": run_id,
                "videos": len(planned),
                "redownloads": len(pending_redownloads),
            },
        )

        updated = 0
        total_updates = len(planned)
        apply_tracker = build_progress_tracker()
        for update_index, (video_path, current, target) in enumerate(planned, start=1):
            print(apply_tracker.line("APPLY", update_index, total_updates, relative_path(root, video_path)))
            try:
                rewrite_embedded_chapters(
                    video_path,
                    target,
                    ffmpeg=args.ffmpeg,
                    timeout=args.ffmpeg_timeout,
                )
                updated += 1
                record = scan_state["records"].get(relative_path(root, video_path))
                if isinstance(record, dict):
                    record["apply_result"] = "success"
                persist_scan_progress(root, scan_state, metadata_cache_state)
                write_journal_event(
                    root,
                    {
                        "event": "file_updated",
                        "action": "bookmarks",
                        "run_id": run_id,
                        "path": relative_path(root, video_path),
                        "from_chapters": len(current),
                        "to_chapters": len(target),
                        "result": "success",
                    },
                )
                print(f"[UPDATED] {relative_path(root, video_path)}")
                apply_tracker.tick()
            except (OSError, RuntimeError) as error:
                failed += 1
                record = scan_state["records"].get(relative_path(root, video_path))
                if isinstance(record, dict):
                    record["apply_result"] = "error"
                    record["apply_error"] = str(error)
                persist_scan_progress(root, scan_state, metadata_cache_state)
                write_journal_event(
                    root,
                    {
                        "event": "file_updated",
                        "action": "bookmarks",
                        "run_id": run_id,
                        "path": relative_path(root, video_path),
                        "from_chapters": len(current),
                        "to_chapters": len(target),
                        "result": "error",
                        "error": str(error),
                    },
                )
                print(f"[ERROR] {relative_path(root, video_path)}: {error}")
                apply_tracker.tick()

        redownloaded = 0
        total_redownloads = len(pending_redownloads)
        redownload_tracker = build_progress_tracker()
        for redownload_index, (video_path, video_id, relative, remote_info) in enumerate(pending_redownloads, start=1):
            print(redownload_tracker.line("REDOWNLOAD", redownload_index, total_redownloads, relative))
            success, error = redownload_video(
                video_path,
                video_id,
                yt_dlp=args.yt_dlp,
                cookies=args.cookies,
                timeout=args.timeout,
            )
            if success:
                redownloaded += 1
                cache_sponsorblock_metadata(metadata_cache_state, video_id, remote_info)
                record = scan_state["records"].get(relative)
                if isinstance(record, dict):
                    record["redownload_result"] = "success"
                persist_scan_progress(root, scan_state, metadata_cache_state)
                write_journal_event(
                    root,
                    {
                        "event": "file_redownloaded",
                        "action": "bookmarks",
                        "run_id": run_id,
                        "path": relative,
                        "result": "success",
                    },
                )
                print(f"[UPDATED] {relative}: перекачано.")
            else:
                failed += 1
                record = scan_state["records"].get(relative)
                if isinstance(record, dict):
                    record["redownload_result"] = "error"
                    record["redownload_error"] = error or "неизвестная ошибка"
                persist_scan_progress(root, scan_state, metadata_cache_state)
                write_journal_event(
                    root,
                    {
                        "event": "file_redownloaded",
                        "action": "bookmarks",
                        "run_id": run_id,
                        "path": relative,
                        "result": "error",
                        "error": error or "неизвестная ошибка",
                    },
                )
                print(f"[ERROR] {relative}: {error}")
                redownload_tracker.tick()

        write_journal_event(
            root,
            {
                "event": "run_end",
                "action": "bookmarks",
                "run_id": run_id,
                "updated": updated,
                "redownloaded": redownloaded,
                "failed": failed,
            },
        )
        print(
            f"[SUMMARY] Обновлено глав: {updated}; "
            f"перекачано: {redownloaded}; ошибок: {failed}; запуск: {run_id}"
        )
        save_cache(root, metadata_cache_state)
        remaining_planned = [
            record
            for record in scan_state["records"].values()
            if isinstance(record, dict)
            and record.get("result") == "planned"
            and record.get("apply_result") != "success"
        ]
        remaining_redownloads = [
            record
            for record in scan_state["records"].values()
            if isinstance(record, dict)
            and record.get("result") == "trimmed_redownload"
            and record.get("redownload_result") != "success"
        ]
        if remaining_planned or remaining_redownloads:
            persist_scan_progress(root, scan_state, metadata_cache_state)
        else:
            clear_scan_state(root)
        return 1 if failed else 0

    return SimpleNamespace(**locals())


update_bookmarks = _build_update_bookmarks()


# ============================================================================
# Find Duplicates
# ============================================================================

def _build_find_duplicates():
    import argparse
    import hashlib
    import os
    import re
    import subprocess
    from collections import defaultdict
    from difflib import SequenceMatcher
    from pathlib import Path

    from colorama import Fore, Style, init

    BASE_DIR = video_config.BASE_DIR
    configured_command = video_config.configured_command
    configured_path = video_config.configured_path
    VIDEO_EXTENSIONS = inventory.VIDEO_EXTENSIONS
    load_config = video_config.load_config
    get_cached_field = video_metadata_cache.get_field
    set_cached_field = video_metadata_cache.set_field


    init(autoreset=True)
    def get_youtube_title(
        video_id: str,
        *,
        cookies_file,
        yt_dlp: str,
        metadata_cache: dict | None = None,
    ) -> str | None:
        if metadata_cache:
            cached = get_cached_field(metadata_cache, "youtube", video_id, "title")
            if cached:
                return cached
        cmd = [
            yt_dlp,
            "--skip-download",
            "--print",
            "%(title)s",
            "--encoding",
            "utf-8",
            f"https://www.youtube.com/watch?v={video_id}",
        ]
        if cookies_file and cookies_file.exists():
            cmd[1:1] = ["--cookies", str(cookies_file)]

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
            )
        except subprocess.TimeoutExpired:
            print(f"{Fore.RED}[ERROR] Таймаут запроса {video_id}{Style.RESET_ALL}")
            return None
        except FileNotFoundError:
            print(f"{Fore.RED}[ERROR] Не найдена программа {yt_dlp}{Style.RESET_ALL}")
            return None

        if result.returncode == 0:
            title = result.stdout.strip()
            if metadata_cache and title:
                set_cached_field(metadata_cache, "youtube", video_id, "title", title)
            return title or None

        message = result.stderr.strip().splitlines()
        print(
            f"{Fore.RED}[ERROR] {message[-1] if message else 'Ошибка yt-dlp'}"
            f"{Style.RESET_ALL}"
        )
        return None


    def highlight(text: str, color: str = "green") -> str:
        colors = {
            "green": Fore.GREEN,
            "yellow": Fore.YELLOW,
            "red": Fore.RED,
        }
        return f"{colors.get(color, Fore.WHITE)}{text}{Style.RESET_ALL}"


    def clean_filename(name: str) -> str:
        name = os.path.splitext(name)[0]
        name = re.sub(r"\[[A-Za-z0-9_-]{11}\]", "", name)
        name = re.sub(r"\[.*?\]", "", name)
        return name.strip().lower()

    def iter_video_files(root: Path) -> list[Path]:
        files = []
        for path in root.rglob("*"):
            if (
                path.is_file()
                and path_selected(root, path)
                and path.suffix.lower() in VIDEO_EXTENSIONS
            ):
                files.append(path)
        return sorted(files, key=lambda path: str(path).casefold())

    def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as source:
            while True:
                chunk = source.read(chunk_size)
                if not chunk:
                    break
                digest.update(chunk)
        return digest.hexdigest()

    def find_exact_duplicates(
        root: Path, *, video_files: list[Path] | None = None
    ) -> list[tuple[int, str, list[Path]]]:
        size_groups: dict[int, list[Path]] = defaultdict(list)
        for path in video_files if video_files is not None else iter_video_files(root):
            size_groups[path.stat().st_size].append(path)

        duplicates = []
        for size_bytes, paths in sorted(size_groups.items()):
            if len(paths) <= 1:
                continue
            hash_groups: dict[str, list[Path]] = defaultdict(list)
            for path in paths:
                hash_groups[sha256_file(path)].append(path)
            for digest, matched_paths in sorted(hash_groups.items()):
                if len(matched_paths) > 1:
                    duplicates.append((size_bytes, digest, matched_paths))
        return duplicates

    def print_exact_duplicates(root: Path, *, video_files: list[Path] | None = None) -> int:
        duplicates = find_exact_duplicates(root, video_files=video_files)
        for size_bytes, digest, paths in duplicates:
            print(
                f"\n[EXACT] файлов: {len(paths)}; "
                f"размер: {size_bytes}; sha256: {digest}"
            )
            for path in paths:
                print(f"  {path.relative_to(root)}")
        return len(duplicates)


    def find_duplicate_ids(
        root,
        *,
        cookies_file=None,
        yt_dlp="yt-dlp",
        metadata_cache: dict | None = None,
        video_files: list[Path] | None = None,
    ) -> int:
        folder = str(root)
        pattern = re.compile(r"\[([A-Za-z0-9_-]{11})\](?=\.\w+$)")
        id_to_files: dict[str, list[str]] = defaultdict(list)

        for path in video_files if video_files is not None else iter_video_files(Path(folder)):
            match = pattern.search(path.name)
            if match:
                id_to_files[match.group(1)].append(str(path))

        duplicate_id_count = 0
        for video_id, files in id_to_files.items():
            if len(files) <= 1:
                continue

            duplicate_id_count += 1

            print(f"\nID: {video_id} - найдено файлов: {len(files)}")
            youtube_title = get_youtube_title(
                video_id,
                cookies_file=cookies_file,
                yt_dlp=yt_dlp,
                metadata_cache=metadata_cache,
            )
            best_match = None
            if youtube_title:
                best_match = max(
                    files,
                    key=lambda file: SequenceMatcher(
                        None,
                        clean_filename(os.path.basename(file)),
                        youtube_title.lower(),
                    ).ratio(),
                )

            for file in files:
                relative_path = os.path.relpath(file, folder)
                if file == best_match:
                    print(
                        "  "
                        + highlight(relative_path)
                        + f'  <- ближе всего к "{youtube_title}"'
                    )
                else:
                    print(f"  {relative_path}")
        return duplicate_id_count

    def main() -> int:
        parser = argparse.ArgumentParser(description="Ищет повторяющиеся ID видео.")
        parser.add_argument("--root", type=Path, default=BASE_DIR)
        args = parser.parse_args()
        config = load_config()
        root = args.root.resolve()
        video_files = iter_video_files(root)
        print(f"[CHECK] Видеофайлов для проверки: {len(video_files)}")
        metadata_cache_state = video_metadata_cache.load_cache(root)
        exact_count = print_exact_duplicates(root, video_files=video_files)
        duplicate_id_count = find_duplicate_ids(
            root,
            cookies_file=configured_path(
                config, "cookies", env_name="YOUTUBE_COOKIES_FILE"
            ),
            yt_dlp=configured_command(config, "yt_dlp", "yt-dlp"),
            metadata_cache=metadata_cache_state,
            video_files=video_files,
        )
        video_metadata_cache.save_cache(root, metadata_cache_state)
        print(
            "\n[SUMMARY] "
            f"Точных групп дублей: {exact_count}; "
            f"повторяющихся YouTube ID: {duplicate_id_count}"
        )
        if not exact_count and not duplicate_id_count:
            print("[OK] Дубли не найдены.")
        return 0
    return SimpleNamespace(**locals())

find_duplicates = _build_find_duplicates()


# ============================================================================
# Get Subtitles
# ============================================================================

def _build_get_subtitles():
    import argparse
    import re
    import subprocess
    import time
    from pathlib import Path

    BASE_DIR = video_config.BASE_DIR
    configured_command = video_config.configured_command
    configured_path = video_config.configured_path
    load_config = video_config.load_config


    VIDEO_EXTENSIONS = inventory.VIDEO_EXTENSIONS
    SUBTITLE_EXTENSIONS = inventory.SUBTITLE_EXTENSIONS
    VIDEO_PATTERN = re.compile(r"\[([A-Za-z0-9_-]{11})\](?=\.[^.]+$)")


    def parse_args() -> argparse.Namespace:
        config = load_config()
        subtitle_config = config.get("subtitles", {})

        parser = argparse.ArgumentParser(description="Скачивает автосубтитры к видео.")
        parser.add_argument("--root", type=Path, default=BASE_DIR)
        parser.add_argument(
            "--folder",
            action="append",
            dest="folders",
            help="Подпапка для обработки. Можно указать несколько раз.",
        )
        parser.add_argument(
            "--lang",
            action="append",
            dest="languages",
            help="Язык субтитров. Можно указать несколько раз.",
        )
        parser.add_argument(
            "--pause",
            type=float,
            default=float(subtitle_config.get("pause_seconds", 5)),
        )
        parser.add_argument("--timeout", type=int, default=60)
        parser.add_argument(
            "--cookies",
            type=Path,
            default=configured_path(
                config, "cookies", env_name="YOUTUBE_COOKIES_FILE"
            ),
        )
        args = parser.parse_args()
        args.folders = args.folders or list(
            subtitle_config.get("folders", ["Deep Look"])
        )
        args.languages = args.languages or list(
            subtitle_config.get("languages", ["ru-en-US"])
        )
        args.yt_dlp = configured_command(config, "yt_dlp", "yt-dlp")
        return args


    def subtitle_exists(video_path: Path, language: str) -> bool:
        language_key = language.casefold()
        prefix = f"{video_path.stem}."
        for candidate in video_path.parent.iterdir():
            if (
                not candidate.is_file()
                or not candidate.name.startswith(prefix)
                or candidate.suffix.lower() not in SUBTITLE_EXTENSIONS
            ):
                continue
            middle = candidate.name[len(prefix):-len(candidate.suffix)]
            candidate_key = middle.casefold()
            if candidate_key == language_key or candidate_key.startswith(
                f"{language_key}."
            ):
                return True
        return False


    def download_subtitles(
        video_path: Path,
        *,
        languages: list[str],
        cookies: Path | None,
        yt_dlp: str,
        timeout: int,
        pause: float,
    ) -> None:
        match = VIDEO_PATTERN.search(video_path.name)
        if not match:
            print(f"[SKIP] Не удалось найти ID: {video_path.name}")
            return

        url = f"https://www.youtube.com/watch?v={match.group(1)}"
        for language in languages:
            if subtitle_exists(video_path, language):
                print(f"[SKIP] Субтитры {language} уже есть: {video_path.name}")
                continue

            cmd = [
                yt_dlp,
                "--write-auto-sub",
                "--sub-langs",
                language,
                "--skip-download",
                "-o",
                str(video_path.with_suffix(".%(ext)s")),
                url,
            ]
            if cookies and cookies.exists():
                cmd[1:1] = ["--cookies", str(cookies)]

            print(f"[DOWNLOAD] {language}: {video_path.name}")
            try:
                subprocess.run(cmd, check=True, timeout=timeout)
            except FileNotFoundError:
                print(f"[ERROR] Не найдена программа {yt_dlp}")
                return
            except subprocess.TimeoutExpired:
                print(f"[ERROR] Таймаут для {video_path.name}")
            except subprocess.CalledProcessError as error:
                print(f"[ERROR] Не удалось скачать субтитры: {error}")

            if pause > 0:
                time.sleep(pause)


    def main() -> int:
        args = parse_args()
        if args.cookies and not args.cookies.exists():
            print(f"[ERROR] Cookies-файл не найден: {args.cookies}")
            return 2

        for folder_name in args.folders:
            folder = args.root.resolve() / folder_name
            if not folder.is_dir():
                print(f"[SKIP] Папка не найдена: {folder}")
                continue
            print(f"[FOLDER] {folder}")
            for video_path in sorted(
                path
                for path in folder.iterdir()
                if path.is_file() and path.suffix.lower() in VIDEO_EXTENSIONS
            ):
                download_subtitles(
                    video_path,
                    languages=args.languages,
                    cookies=args.cookies,
                    yt_dlp=args.yt_dlp,
                    timeout=args.timeout,
                    pause=args.pause,
                )
        return 0
    return SimpleNamespace(**locals())

get_subtitles = _build_get_subtitles()


# ============================================================================
# Rename Files
# ============================================================================

def _build_rename_files():
    import argparse
    import re
    import subprocess
    import uuid
    from datetime import datetime
    from pathlib import Path

    BASE_DIR = video_config.BASE_DIR
    configured_command = video_config.configured_command
    configured_path = video_config.configured_path
    get_cached_field = video_metadata_cache.get_field
    load_config = video_config.load_config
    relative_path = video_journal.relative_path
    set_cached_field = video_metadata_cache.set_field
    write_journal_event = video_journal.write_journal_event


    FILE_RE = re.compile(
        r"^(?P<title>.+?) \[(?P<id>[A-Za-z0-9_-]{11})\]\.(?P<ext>.+)$"
    )
    DATE_RE = re.compile(r"_\d{2}\.\d{2}\.\d{4}$")


    def parse_args() -> argparse.Namespace:
        config = load_config()
        default_cookies = configured_path(
            config, "cookies", env_name="YOUTUBE_COOKIES_FILE"
        )

        parser = argparse.ArgumentParser(
            description="Добавляет дату публикации в имена видеофайлов."
        )
        parser.add_argument(
            "--root",
            type=Path,
            default=BASE_DIR,
            help="Корневая папка для рекурсивного поиска.",
        )
        parser.add_argument(
            "--cookies",
            type=Path,
            default=default_cookies,
            help="Путь к cookies-файлу yt-dlp.",
        )
        parser.add_argument(
            "--timeout",
            type=int,
            default=45,
            help="Таймаут одного запроса yt-dlp в секундах.",
        )
        mode = parser.add_mutually_exclusive_group()
        mode.add_argument(
            "--apply",
            action="store_true",
            help="Выполнить переименование. Без этого флага выводится только план.",
        )
        mode.add_argument(
            "--dry-run",
            action="store_true",
            help="Явно включить предварительный просмотр (режим по умолчанию).",
        )
        args = parser.parse_args()
        args.yt_dlp = configured_command(config, "yt_dlp", "yt-dlp")
        return args


    def get_upload_date(
        video_id: str,
        *,
        yt_dlp: str,
        cookies: Path | None,
        timeout: int,
        metadata_cache: dict | None = None,
    ) -> tuple[str | None, str | None]:
        if metadata_cache:
            cached = get_cached_field(
                metadata_cache,
                "youtube",
                video_id,
                "upload_date",
            )
            if cached:
                return cached, None
        cmd = [
            yt_dlp,
            "--skip-download",
            "--print",
            "%(upload_date)s",
            f"https://www.youtube.com/watch?v={video_id}",
        ]
        if cookies:
            cmd[1:1] = ["--cookies", str(cookies)]

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
            )
        except FileNotFoundError:
            return None, f"не найдена программа {yt_dlp}"
        except subprocess.TimeoutExpired:
            return None, f"таймаут запроса ({timeout} с)"

        if result.returncode != 0:
            message = result.stderr.strip().splitlines()
            return None, message[-1] if message else f"код ошибки {result.returncode}"

        date_raw = result.stdout.strip()
        if not date_raw or date_raw == "NA":
            return None, "YouTube не вернул дату публикации"

        try:
            formatted = datetime.strptime(date_raw, "%Y%m%d").strftime("%d.%m.%Y")
            if metadata_cache:
                set_cached_field(
                    metadata_cache,
                    "youtube",
                    video_id,
                    "upload_date",
                    formatted,
                )
            return formatted, None
        except ValueError:
            return None, f"неожиданный формат даты: {date_raw!r}"


    def iter_candidates(root: Path):
        for path in root.rglob("*"):
            if not path.is_file() or not path_selected(root, path):
                continue
            match = FILE_RE.match(path.name)
            if match:
                yield path, match


    def main() -> int:
        args = parse_args()
        root = args.root.resolve()

        if args.cookies and not args.cookies.exists():
            print(f"[ERROR] Cookies-файл не найден: {args.cookies}")
            return 2

        mode = "APPLY" if args.apply else "DRY-RUN"
        print(f"[{mode}] Корневая папка: {root}")

        renamed = skipped = failed = 0
        metadata_cache_state = video_metadata_cache.load_cache(root)
        run_id = uuid.uuid4().hex if args.apply else None
        if run_id:
            write_journal_event(
                root,
                {
                    "event": "run_start",
                    "action": "rename",
                    "run_id": run_id,
                },
            )

        for old_path, match in iter_candidates(root):
            title = match.group("title")
            existing_date, valid_date = extract_filename_date(old_path.name)
            if existing_date and valid_date:
                skipped += 1
                continue
            if existing_date and not valid_date:
                print(f"[WARNING] Некорректная дата в имени будет заменена: {old_path}")
                title = re.sub(r"_\d{2}\.\d{2}\.\d{4}$", "", title)

            upload_date, error = get_upload_date(
                match.group("id"),
                yt_dlp=args.yt_dlp,
                cookies=args.cookies,
                timeout=args.timeout,
                metadata_cache=metadata_cache_state,
            )
            if error:
                print(f"[SKIP] {old_path}: {error}")
                failed += 1
                continue

            new_name = (
                f"{title}_{upload_date} [{match.group('id')}].{match.group('ext')}"
            )
            new_name = normalize_windows_name(
                new_name,
                preserve_extension=True,
            )
            new_path = old_path.with_name(new_name)
            if new_path.exists():
                print(f"[SKIP] Уже существует: {new_path}")
                skipped += 1
                continue

            print(f"[{'RENAME' if args.apply else 'PLAN'}] {old_path} -> {new_path.name}")
            if args.apply:
                assert run_id is not None
                source_value = relative_path(root, old_path)
                destination_value = relative_path(root, new_path)
                try:
                    old_path.rename(new_path)
                except OSError as rename_error:
                    write_journal_event(
                        root,
                        {
                            "event": "rename",
                            "action": "rename",
                            "run_id": run_id,
                            "source": source_value,
                            "destination": destination_value,
                            "result": "failed",
                            "error": str(rename_error),
                        },
                    )
                    print(f"[ERROR] Не удалось переименовать {old_path}: {rename_error}")
                    failed += 1
                    continue
                write_journal_event(
                    root,
                    {
                        "event": "rename",
                        "action": "rename",
                        "run_id": run_id,
                        "source": source_value,
                        "destination": destination_value,
                        "result": "success",
                    },
                )
            renamed += 1

        if run_id:
            write_journal_event(
                root,
                {
                    "event": "run_end",
                    "action": "rename",
                    "run_id": run_id,
                    "result": "partial" if failed else "success",
                    "renamed": renamed,
                    "skipped": skipped,
                    "failed": failed,
                },
            )

        print(
            f"[SUMMARY] Запланировано/выполнено: {renamed}; "
            f"пропущено: {skipped}; ошибок: {failed}"
        )
        video_metadata_cache.save_cache(root, metadata_cache_state)
        return 1 if failed else 0
    return SimpleNamespace(**locals())

rename_files = _build_rename_files()


# ============================================================================
# Resort
# ============================================================================

def _build_resort():
    import argparse
    import json
    import os
    import re
    import shutil
    import subprocess
    import tempfile
    import time
    import uuid
    from pathlib import Path

    BASE_DIR = video_config.BASE_DIR
    configured_command = video_config.configured_command
    configured_path = video_config.configured_path
    get_cached_field = video_metadata_cache.get_field
    load_config = video_config.load_config
    JOURNAL_DIR_NAME = video_journal.JOURNAL_DIR_NAME
    journal_path = video_journal.journal_path
    path_from_journal = video_journal.path_from_journal
    read_journal = video_journal.read_journal
    relative_path = video_journal.relative_path
    set_cached_field = video_metadata_cache.set_field
    write_journal_event = video_journal.write_journal_event


    BLOCKED_MESSAGES = [
        "Video unavailable. This video is private",
        "who has blocked it",
        "This content is not available in your country",
        "This video is no longer available",
        "This video has been removed for violating YouTube's Terms of Service",
        "Video unavailable. This video is not available",
    ]
    SUBTITLE_EXTENSIONS = inventory.SUBTITLE_EXTENSIONS


    def parse_args() -> argparse.Namespace:
        config = load_config()
        sorting = config.get("sorting", {})
        default_cookies = configured_path(
            config, "cookies", env_name="YOUTUBE_COOKIES_FILE"
        )

        parser = argparse.ArgumentParser(
            description="Сортирует видео из корня по папкам авторов."
        )
        parser.add_argument("--root", type=Path, default=BASE_DIR)
        parser.add_argument("--cookies", type=Path, default=default_cookies)
        parser.add_argument("--timeout", type=int, default=45)
        parser.add_argument(
            "--pause",
            type=float,
            default=float(sorting.get("pause_seconds", 2)),
        )
        parser.add_argument(
            "--max-retries",
            type=int,
            default=int(sorting.get("max_retries", 3)),
        )
        parser.add_argument(
            "--allow-unknown",
            action="store_true",
            default=bool(sorting.get("allow_unknown", False)),
            help="Разрешить перемещение в папку Unknown при отсутствии автора.",
        )
        parser.add_argument(
            "--yes",
            action="store_true",
            help="Не запрашивать подтверждение перед --apply.",
        )
        mode = parser.add_mutually_exclusive_group()
        mode.add_argument(
            "--apply",
            action="store_true",
            help="Выполнить перемещения. Без флага выводится только план.",
        )
        mode.add_argument(
            "--dry-run",
            action="store_true",
            help="Явно включить предварительный просмотр (режим по умолчанию).",
        )
        mode.add_argument(
            "--undo-last",
            action="store_true",
            help="Отменить последний завершённый запуск --apply.",
        )
        args = parser.parse_args()
        args.yt_dlp = configured_command(config, "yt_dlp", "yt-dlp")
        return args


    def extract_id_and_type(filename: str) -> tuple[str | None, str]:
        matches = re.findall(r"\[([A-Za-z0-9_-]+)\]", filename)
        if not matches:
            return None, "unknown"
        for value in reversed(matches):
            if len(value) == 11:
                return value, "youtube"
        return matches[-1], "rutube"


    def sanitize_name(name: str, *, preserve_extension: bool = False) -> str:
        return normalize_windows_name(
            name,
            preserve_extension=preserve_extension,
        )


    def log_error(root: Path, message: str, *, write_log: bool) -> None:
        print(f"[ERROR] {message}")
        if write_log:
            with (root / "errors.log").open("a", encoding="utf-8") as error_log:
                error_log.write(f"[ERROR] {message}\n")


    def successful_moves(events: list[dict], run_id: str) -> list[tuple[str, str]]:
        active: list[tuple[str, str]] = []
        for event in events:
            if event.get("run_id") != run_id:
                continue
            pair = (event.get("source"), event.get("destination"))
            if not all(isinstance(value, str) for value in pair):
                continue
            if event.get("event") == "move" and event.get("result") == "success":
                active.append(pair)
            elif event.get("event") == "rollback" and event.get("result") == "success":
                try:
                    active.remove(pair)
                except ValueError:
                    pass
        return active


    def find_last_undoable_run(events: list[dict]) -> tuple[str, list[tuple[str, str]]] | None:
        completed = [
            event.get("run_id")
            for event in events
            if event.get("event") == "run_end"
            and event.get("action") == "resort"
            and isinstance(event.get("run_id"), str)
        ]
        undone = {
            event.get("target_run_id")
            for event in events
            if event.get("event") == "undo_end"
            and event.get("result") == "success"
        }
        for run_id in reversed(completed):
            if run_id in undone:
                continue
            moves = successful_moves(events, run_id)
            if moves:
                return run_id, moves
        return None


    def validate_undo_plan(
        root: Path,
        moves: list[tuple[str, str]],
    ) -> list[tuple[Path, Path]]:
        plan = []
        for source_value, destination_value in reversed(moves):
            original = path_from_journal(root, source_value)
            current = path_from_journal(root, destination_value)
            if not current.exists():
                raise FileNotFoundError(f"Перемещённый файл не найден: {current}")
            if original.exists():
                raise FileExistsError(f"Исходный путь уже занят: {original}")
            plan.append((current, original))
        return plan


    def undo_last_run(root: Path) -> int:
        try:
            events = read_journal(root)
            last_run = find_last_undoable_run(events)
        except (OSError, ValueError) as error:
            print(f"[ERROR] Не удалось прочитать журнал: {error}")
            return 2

        if not last_run:
            print("[UNDO] Нет завершённых запусков, доступных для отмены.")
            return 0

        target_run_id, moves = last_run
        try:
            plan = validate_undo_plan(root, moves)
        except (OSError, ValueError) as error:
            print(f"[ERROR] Отмена невозможна: {error}")
            return 2

        undo_run_id = uuid.uuid4().hex
        write_journal_event(
            root,
            {
                "event": "undo_start",
                "run_id": undo_run_id,
                "target_run_id": target_run_id,
                "operations": len(plan),
            },
        )

        completed: list[tuple[Path, Path]] = []
        try:
            for current, original in plan:
                original.parent.mkdir(parents=True, exist_ok=True)
                current.rename(original)
                completed.append((current, original))
                write_journal_event(
                    root,
                    {
                        "event": "undo_move",
                        "run_id": undo_run_id,
                        "target_run_id": target_run_id,
                        "source": relative_path(root, current),
                        "destination": relative_path(root, original),
                        "result": "success",
                    },
                )
                print(f"[UNDO] {current} -> {original}")
        except OSError as error:
            rollback_failed = False
            for current, original in reversed(completed):
                try:
                    original.rename(current)
                except OSError:
                    rollback_failed = True
            write_journal_event(
                root,
                {
                    "event": "undo_end",
                    "run_id": undo_run_id,
                    "target_run_id": target_run_id,
                    "result": "rollback_failed" if rollback_failed else "failed",
                    "error": str(error),
                },
            )
            print(f"[ERROR] Отмена прервана: {error}")
            return 2

        write_journal_event(
            root,
            {
                "event": "undo_end",
                "run_id": undo_run_id,
                "target_run_id": target_run_id,
                "result": "success",
                "operations": len(completed),
            },
        )
        print(f"[SUMMARY] Отменён запуск {target_run_id}; файлов: {len(completed)}")
        return 0


    def get_uploader(
        video_id: str | None,
        video_type: str,
        *,
        yt_dlp: str,
        cookies: Path | None,
        timeout: int,
        max_retries: int,
        pause: float,
        cache: dict[str, str | None],
        metadata_cache: dict | None = None,
        root: Path,
        write_log: bool,
    ) -> str | None:
        if not video_id:
            return None

        key = f"{video_type}:{video_id}"
        if key in cache:
            return cache[key]
        if metadata_cache:
            cached = get_cached_field(metadata_cache, video_type, video_id, "uploader")
            if cached:
                cache[key] = cached
                return cached

        if video_type == "youtube":
            url = f"https://www.youtube.com/watch?v={video_id}"
        elif video_type == "rutube":
            url = f"https://rutube.ru/video/{video_id}/"
        else:
            return None

        cmd = [yt_dlp, "-j", url]
        if video_type == "youtube" and cookies:
            cmd[1:1] = ["--cookies", str(cookies)]

        uploader = None
        for attempt in range(1, max_retries + 1):
            try:
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=timeout,
                )
            except FileNotFoundError:
                log_error(root, f"Не найдена программа {yt_dlp}", write_log=write_log)
                break
            except subprocess.TimeoutExpired:
                log_error(
                    root,
                    f"{video_type.upper()} ID={video_id}: таймаут, попытка {attempt}",
                    write_log=write_log,
                )
                result = None

            if result is not None:
                stderr = result.stderr.strip()
                if stderr and result.returncode != 0:
                    log_error(
                        root,
                        f"{video_type.upper()} ID={video_id}: {stderr.splitlines()[-1]}",
                        write_log=write_log,
                    )
                    if any(message in stderr for message in BLOCKED_MESSAGES):
                        break

                if result.stdout.strip():
                    try:
                        info = json.loads(result.stdout)
                    except json.JSONDecodeError:
                        log_error(
                            root,
                            f"{video_type.upper()} ID={video_id}: некорректный JSON",
                            write_log=write_log,
                        )
                    else:
                        uploader = info.get("uploader") or info.get("uploader_id")
                        if uploader:
                            break

            if attempt < max_retries:
                time.sleep(pause)

        if not uploader and video_type == "rutube":
            uploader = "Rutube"
        if metadata_cache and uploader:
            set_cached_field(
                metadata_cache,
                video_type,
                video_id,
                "uploader",
                uploader,
            )
        cache[key] = uploader
        return uploader


    def unique_destination(
        destination: Path,
        reserved: set[Path],
    ) -> Path:
        candidate = destination
        index = 1
        while candidate.exists() or candidate in reserved:
            candidate = destination.with_name(
                f"{destination.stem}_{index}{destination.suffix}"
            )
            index += 1
        reserved.add(candidate)
        return candidate


    def build_move_plan(
        video_path: Path,
        destination_dir: Path,
        reserved: set[Path],
    ) -> list[tuple[Path, Path]]:
        plan = [
            (
                video_path,
                unique_destination(
                    destination_dir / sanitize_name(
                        video_path.name,
                        preserve_extension=True,
                    ),
                    reserved,
                ),
            )
        ]
        for subtitle in video_path.parent.glob(f"{video_path.stem}.*"):
            if subtitle.suffix.lower() not in SUBTITLE_EXTENSIONS:
                continue
            plan.append(
                (
                    subtitle,
                    unique_destination(
                        destination_dir / sanitize_name(
                            subtitle.name,
                            preserve_extension=True,
                        ),
                        reserved,
                    ),
                )
            )
        return plan


    def preflight_apply(
        root: Path,
        plans: list[list[tuple[Path, Path]]],
    ) -> None:
        if not root.exists():
            raise FileNotFoundError(f"Корневая папка не найдена: {root}")
        if not root.is_dir():
            raise NotADirectoryError(f"Корневой путь не является папкой: {root}")

        destinations: set[Path] = set()
        operation_count = 0
        for plan in plans:
            for source, destination in plan:
                operation_count += 1
                source.resolve().relative_to(root)
                destination.resolve().relative_to(root)
                if not source.is_file():
                    raise FileNotFoundError(f"Исходный файл не найден: {source}")
                if destination.exists():
                    raise FileExistsError(f"Целевой путь уже существует: {destination}")
                if destination in destinations:
                    raise FileExistsError(f"Целевой путь повторяется: {destination}")
                destinations.add(destination)

        required_journal_space = max(1_048_576, operation_count * 4096)
        free_space = shutil.disk_usage(root).free
        if free_space < required_journal_space:
            raise OSError(
                "Недостаточно свободного места для безопасного журнала операций: "
                f"нужно {required_journal_space} байт, доступно {free_space}"
            )

        journal_dir = root / JOURNAL_DIR_NAME
        journal_dir.mkdir(parents=True, exist_ok=True)
        try:
            file_descriptor, probe_name = tempfile.mkstemp(
                prefix=".write-test-",
                dir=journal_dir,
            )
            os.close(file_descriptor)
            Path(probe_name).unlink()
        except OSError as error:
            raise PermissionError(
                f"Нет доступа на запись в служебную папку {journal_dir}: {error}"
            ) from error


    def confirm_apply(
        *,
        video_count: int,
        file_count: int,
        folder_count: int,
        assume_yes: bool,
        input_fn=input,
    ) -> bool:
        print(
            "[CONFIRM] Будет перемещено "
            f"видео: {video_count}; файлов всего: {file_count}; "
            f"целевых папок: {folder_count}."
        )
        if assume_yes:
            return True
        try:
            answer = input_fn("Продолжить? [Y/n]: ")
        except (EOFError, KeyboardInterrupt):
            print()
            return False
        return is_affirmative_reply(answer)


    def apply_move_plan(
        root: Path,
        run_id: str,
        plan: list[tuple[Path, Path]],
    ) -> None:
        completed: list[tuple[Path, Path]] = []
        try:
            for source, destination in plan:
                destination.parent.mkdir(parents=True, exist_ok=True)
                source.rename(destination)
                completed.append((source, destination))
                write_journal_event(
                    root,
                    {
                        "event": "move",
                        "action": "resort",
                        "run_id": run_id,
                        "source": relative_path(root, source),
                        "destination": relative_path(root, destination),
                        "result": "success",
                    },
                )
        except OSError as error:
            rollback_failed = False
            for source, destination in reversed(completed):
                try:
                    destination.rename(source)
                    write_journal_event(
                        root,
                        {
                            "event": "rollback",
                            "action": "resort",
                            "run_id": run_id,
                            "source": relative_path(root, source),
                            "destination": relative_path(root, destination),
                            "result": "success",
                        },
                    )
                except OSError as rollback_error:
                    rollback_failed = True
                    write_journal_event(
                        root,
                        {
                            "event": "rollback",
                            "action": "resort",
                            "run_id": run_id,
                            "source": relative_path(root, source),
                            "destination": relative_path(root, destination),
                            "result": "failed",
                            "error": str(rollback_error),
                        },
                    )
            if rollback_failed:
                raise OSError(
                    f"{error}; кроме того, не удалось полностью откатить операцию"
                ) from error
            raise


    def main() -> int:
        args = parse_args()
        root = args.root.resolve()

        if args.undo_last:
            return undo_last_run(root)

        if args.cookies and not args.cookies.exists():
            print(f"[ERROR] Cookies-файл не найден: {args.cookies}")
            return 2

        videos = sorted(
            path
            for path in root.iterdir()
            if path.is_file() and path.suffix.lower() in inventory.VIDEO_EXTENSIONS
        )
        print(f"[{'APPLY' if args.apply else 'DRY-RUN'}] Найдено видео: {len(videos)}")

        cache: dict[str, str | None] = {}
        metadata_cache_state = video_metadata_cache.load_cache(root)
        reserved: set[Path] = set()
        planned: list[tuple[Path, str, list[tuple[Path, Path]]]] = []
        skipped_unknown = 0

        for video in videos:
            video_id, video_type = extract_id_and_type(video.name)
            uploader = get_uploader(
                video_id,
                video_type,
                yt_dlp=args.yt_dlp,
                cookies=args.cookies,
                timeout=args.timeout,
                max_retries=args.max_retries,
                pause=args.pause,
                cache=cache,
                metadata_cache=metadata_cache_state,
                root=root,
                write_log=False,
            )
            if not uploader:
                if not args.allow_unknown:
                    print(
                        f"[SKIP] Автор не определён: {video.name}. "
                        "Для папки Unknown используйте --allow-unknown."
                    )
                    skipped_unknown += 1
                    continue
                uploader = "Unknown"

            destination_dir = root / sanitize_name(uploader)
            plan = build_move_plan(video, destination_dir, reserved)
            planned.append((video, uploader, plan))

            for source, destination in plan:
                print(f"[PLAN] {source.name} -> {destination}")

            if args.pause > 0:
                time.sleep(args.pause)

        file_count = sum(len(plan) for _, _, plan in planned)
        folder_count = len({uploader for _, uploader, _ in planned})
        print(
            f"[SUMMARY] Запланировано видео: {len(planned)}; файлов: {file_count}; "
            f"папок: {folder_count}; без автора: {skipped_unknown}"
        )

        if not args.apply or not planned:
            return 0

        plans = [plan for _, _, plan in planned]
        try:
            preflight_apply(root, plans)
        except (OSError, ValueError) as error:
            print(f"[ERROR] Предварительная проверка не пройдена: {error}")
            return 2

        if not confirm_apply(
            video_count=len(planned),
            file_count=file_count,
            folder_count=folder_count,
            assume_yes=args.yes,
        ):
            print("[CANCEL] Перемещения не выполнялись.")
            return 0

        run_id = uuid.uuid4().hex
        write_journal_event(
            root,
            {
                "event": "run_start",
                "action": "resort",
                "run_id": run_id,
                "videos": len(planned),
                "files": file_count,
                "allow_unknown": args.allow_unknown,
            },
        )

        moved = failed = 0
        for video, _, plan in planned:
            try:
                apply_move_plan(root, run_id, plan)
            except OSError as error:
                log_error(
                    root,
                    f"Не удалось переместить {video.name}: {error}",
                    write_log=True,
                )
                failed += 1
                continue

            moved += 1
            for source, destination in plan:
                print(f"[MOVE] {source.name} -> {destination}")

        write_journal_event(
            root,
            {
                "event": "run_end",
                "action": "resort",
                "run_id": run_id,
                "result": "partial" if failed else "success",
                "videos": moved,
                "failed": failed,
            },
        )

        print(
            f"[SUMMARY] Перемещено видео: {moved}; ошибок: {failed}; "
            f"запуск: {run_id}"
        )
        video_metadata_cache.save_cache(root, metadata_cache_state)
        return 1 if failed else 0
    return SimpleNamespace(**locals())

resort = _build_resort()


# ============================================================================
# Video Doctor
# ============================================================================

def _build_video_doctor():
    import os
    import shutil
    import subprocess
    import sys
    import tempfile
    import tomllib
    from dataclasses import dataclass
    from pathlib import Path
    from typing import Any

    configured_command = video_config.configured_command
    configured_path = video_config.configured_path
    journal_path = video_journal.journal_path
    read_journal = video_journal.read_journal


    @dataclass(frozen=True)
    class CheckResult:
        name: str
        status: str
        message: str


    def check_python() -> CheckResult:
        version = sys.version_info
        current = f"{version.major}.{version.minor}.{version.micro}"
        if version >= (3, 11):
            return CheckResult("Python", "ok", current)
        return CheckResult("Python", "error", f"{current}; требуется Python 3.11+")


    def load_and_check_config(config_path: Path) -> tuple[dict[str, Any], CheckResult]:
        if not config_path.is_file():
            return {}, CheckResult(
                "Конфигурация",
                "error",
                f"файл не найден: {config_path}",
            )
        try:
            with config_path.open("rb") as config_file:
                config = tomllib.load(config_file)
        except (OSError, tomllib.TOMLDecodeError) as error:
            return {}, CheckResult("Конфигурация", "error", str(error))

        errors = []
        paths = config.get("paths")
        if not isinstance(paths, dict):
            errors.append("секция [paths] отсутствует")
        else:
            for key in ("yt_dlp", "ffprobe"):
                if not isinstance(paths.get(key), str) or not paths[key].strip():
                    errors.append(f"paths.{key} должен быть непустой строкой")

        subtitles = config.get("subtitles", {})
        if not isinstance(subtitles, dict):
            errors.append("[subtitles] должна быть таблицей")
        else:
            for key in ("folders", "languages"):
                value = subtitles.get(key, [])
                if not isinstance(value, list) or not all(
                    isinstance(item, str) and item for item in value
                ):
                    errors.append(f"subtitles.{key} должен быть списком строк")

        sorting = config.get("sorting", {})
        if not isinstance(sorting, dict):
            errors.append("[sorting] должна быть таблицей")
        else:
            max_retries = sorting.get("max_retries", 1)
            if not isinstance(max_retries, int) or isinstance(max_retries, bool):
                errors.append("sorting.max_retries должен быть целым числом")
            elif max_retries < 1:
                errors.append("sorting.max_retries должен быть не меньше 1")

        if errors:
            return config, CheckResult("Конфигурация", "error", "; ".join(errors))
        return config, CheckResult("Конфигурация", "ok", str(config_path))


    def check_cookies(config: dict[str, Any]) -> CheckResult:
        cookies = configured_path(
            config,
            "cookies",
            env_name="YOUTUBE_COOKIES_FILE",
        )
        if not cookies:
            return CheckResult("Cookies", "warning", "путь не настроен")
        if not cookies.is_file():
            return CheckResult("Cookies", "error", f"файл не найден: {cookies}")
        try:
            with cookies.open("rb") as cookies_file:
                cookies_file.read(1)
        except OSError as error:
            return CheckResult("Cookies", "error", f"файл недоступен: {error}")
        return CheckResult("Cookies", "ok", str(cookies))


    def check_command(
        name: str,
        command: str,
        version_args: list[str],
    ) -> CheckResult:
        executable = shutil.which(command)
        if not executable:
            return CheckResult(name, "error", f"команда не найдена: {command}")
        try:
            result = subprocess.run(
                [executable, *version_args],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=15,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            return CheckResult(name, "error", str(error))

        output = (result.stdout or result.stderr).strip().splitlines()
        detail = output[0] if output else executable
        if result.returncode != 0:
            return CheckResult(
                name,
                "error",
                f"код {result.returncode}: {detail}",
            )
        return CheckResult(name, "ok", detail)


    def check_root(root: Path) -> CheckResult:
        if not root.exists():
            return CheckResult("Корневая папка", "error", f"не найдена: {root}")
        if not root.is_dir():
            return CheckResult("Корневая папка", "error", f"не папка: {root}")

        try:
            file_descriptor, probe_name = tempfile.mkstemp(
                prefix=".video-tools-doctor-",
                dir=root,
            )
            os.close(file_descriptor)
            Path(probe_name).unlink()
        except OSError as error:
            return CheckResult("Корневая папка", "error", f"нет записи: {error}")

        free_gb = shutil.disk_usage(root).free / (1024**3)
        status = "warning" if free_gb < 1 else "ok"
        return CheckResult(
            "Корневая папка",
            status,
            f"{root}; свободно {free_gb:.1f} ГБ",
        )


    def check_journal(root: Path) -> CheckResult:
        path = journal_path(root)
        if not path.exists():
            return CheckResult("Журнал", "ok", "ещё не создан")
        try:
            events = read_journal(root)
        except (OSError, ValueError) as error:
            return CheckResult("Журнал", "error", str(error))
        return CheckResult("Журнал", "ok", f"{len(events)} записей: {path}")


    def run_doctor(root: Path, config_path: Path) -> list[CheckResult]:
        config, config_result = load_and_check_config(config_path)
        results = [
            check_python(),
            config_result,
            check_root(root),
        ]
        if config:
            results.extend(
                [
                    check_cookies(config),
                    check_command(
                        "yt-dlp",
                        configured_command(config, "yt_dlp", "yt-dlp"),
                        ["--version"],
                    ),
                    check_command(
                        "ffprobe",
                        configured_command(config, "ffprobe", "ffprobe"),
                        ["-version"],
                    ),
                ]
            )
        results.append(check_journal(root))
        return results


    def print_results(results: list[CheckResult]) -> None:
        labels = {"ok": "OK", "warning": "WARN", "error": "ERROR"}
        for result in results:
            print(f"[{labels[result.status]}] {result.name}: {result.message}")
        errors = sum(result.status == "error" for result in results)
        warnings = sum(result.status == "warning" for result in results)
        print(f"[SUMMARY] Ошибок: {errors}; предупреждений: {warnings}")


    def doctor_exit_code(results: list[CheckResult]) -> int:
        return 1 if any(result.status == "error" for result in results) else 0
    return SimpleNamespace(**locals())

video_doctor = _build_video_doctor()


# ============================================================================
# Main Menu And Command Router
# ============================================================================

BASE_DIR = video_config.BASE_DIR
DEFAULT_CONFIG_PATH = video_config.DEFAULT_CONFIG_PATH
DEFAULT_ROOT_PATH = video_config.default_root_path
DEFAULT_CONFIG_FILE = video_config.default_config_path
CONFIG_ENV_NAME = video_config.CONFIG_ENV_NAME
ROOT_ENV_NAME = video_config.ROOT_ENV_NAME
doctor_exit_code = video_doctor.doctor_exit_code
print_results = video_doctor.print_results
run_doctor = video_doctor.run_doctor
INTERNAL_MODULES = {
    "video_config": video_config,
    "video_metadata_cache": video_metadata_cache,
    "video_journal": video_journal,
    "inventory": inventory,
    "archive_report": archive_report,
    "sync_download_archive": sync_download_archive,
    "check_date": check_date,
    "check_resolution": check_resolution,
    "download_yt_favorites": download_yt_favorites,
    "update_bookmarks": update_bookmarks,
    "find_duplicates": find_duplicates,
    "get_subtitles": get_subtitles,
    "rename_files": rename_files,
    "resort": resort,
    "video_doctor": video_doctor,
}

# Compatibility aliases for tests and existing imports.
for _module_name, _module_namespace in INTERNAL_MODULES.items():
    sys.modules.setdefault(_module_name, _module_namespace)

VERSION = "1.0.0"
COMMAND_SCRIPTS = {
    "download": "download_yt_favorites",
    "bookmarks": "update_bookmarks",
    "subtitles": "get_subtitles",
    "rename": "rename_files",
    "resort": "resort",
    "duplicates": "find_duplicates",
    "dates": "check_date",
    "resolution": "check_resolution",
    "inventory": "inventory",
    "report": "archive_report",
    "archive-sync": "sync_download_archive",
}
ROOT_AWARE_COMMANDS = set(COMMAND_SCRIPTS)
FOLDER_FILTER_COMMANDS = {
    "subtitles",
    "rename",
    "duplicates",
    "dates",
    "resolution",
    "inventory",
    "report",
    "bookmarks",
}
MENU_OPTIONS = {
    "1": ("Проверить окружение (doctor)", "doctor", []),
    "2": ("Скачать избранное YouTube", "download", []),
    "3": ("Скачать субтитры", "subtitles", []),
    "4": ("Предпросмотр переименования", "rename", ["--dry-run"]),
    "5": ("Выполнить переименование", "rename", ["--apply"]),
    "6": ("Предпросмотр сортировки", "resort", ["--dry-run"]),
    "7": ("Выполнить сортировку", "resort", ["--apply"]),
    "8": ("Отменить последнюю сортировку", "resort", ["--undo-last"]),
    "9": ("Найти дубли", "duplicates", []),
    "10": ("Проверить даты в именах", "dates", []),
    "11": ("Проверить разрешение видео", "resolution", []),
    "12": ("Создать CSV-инвентаризацию", "inventory", []),
    "13": ("Проверить архив загрузок", "archive-sync", ["--dry-run"]),
    "14": ("Синхронизировать архив загрузок", "archive-sync", ["--apply"]),
    "15": ("Отчёт о проблемах архива", "report", []),
    "16": ("Обновить закладки SponsorBlock", "bookmarks", ["--apply"]),
    "0": ("Выход", None, []),
}
MENU_HELP = {
    "1": (
        "Проверяет Python, config.toml, cookies, yt-dlp, ffprobe, "
        "свободное место, права записи и журнал. Видео не читает.",
        "video_tools.py doctor",
    ),
    "2": (
        "Скачивает новые видео из плейлиста YouTube «Смотреть позже». "
        "Перед загрузкой синхронизирует yt-dlp-archive.txt с локальными "
        "файлами и создаёт backup при изменении.",
        "video_tools.py download",
    ),
    "3": (
        "Скачивает автоматические субтитры для папок и языков из config.toml. "
        "Параметры можно переопределить через --folder и --lang.",
        "video_tools.py subtitles --folder \"Deep Look\" --lang ru-en-US",
    ),
    "4": (
        "Получает даты публикации с YouTube и показывает план новых имён. "
        "Файлы не переименовывает.",
        "video_tools.py rename --dry-run",
    ),
    "5": (
        "Получает даты публикации и реально переименовывает подходящие файлы. "
        "Операции записываются в JSONL-журнал.",
        "video_tools.py rename --apply",
    ),
    "6": (
        "Определяет авторов через yt-dlp и показывает план распределения "
        "файлов по папкам. Ничего не перемещает.",
        "video_tools.py resort --dry-run",
    ),
    "7": (
        "Перемещает видео и связанные субтитры по папкам авторов. "
        "Перед началом выполняет проверки и запрашивает подтверждение.",
        "video_tools.py resort --apply",
    ),
    "8": (
        "Возвращает файлы из последнего завершённого запуска сортировки. "
        "Отмена прекращается при конфликте исходных путей.",
        "video_tools.py resort --undo-last",
    ),
    "9": (
        "Ищет повторяющиеся YouTube ID и сравнивает локальные названия "
        "с названием на YouTube. Файлы не удаляет.",
        "video_tools.py duplicates",
    ),
    "10": (
        "Проверяет только видеофайлы: показывает отсутствующие и календарно "
        "некорректные даты формата ДД.ММ.ГГГГ. Файлы не изменяет.",
        "video_tools.py dates",
    ),
    "11": (
        "Запускает ffprobe для видео и показывает разрешение. "
        "Может работать долго, но файлы не изменяет.",
        "video_tools.py resolution",
    ),
    "12": (
        "Создаёт inventory.csv по именам и файловым метаданным: папка, "
        "размер, ID, дата и наличие субтитров. Видеопоток не читает.",
        "video_tools.py inventory",
    ),
    "13": (
        "Показывает ID из yt-dlp-archive.txt, для которых локального видео "
        "уже нет. Файл архива не изменяет.",
        "video_tools.py archive-sync --dry-run",
    ),
    "14": (
        "Удаляет устаревшие ID из yt-dlp-archive.txt и предварительно "
        "создаёт резервную копию. Требует подтверждения.",
        "video_tools.py archive-sync --apply",
    ),
    "15": (
        "Показывает видео без ID, даты, папки автора или субтитров. "
        "Работает только с именами и файловыми метаданными.",
        "video_tools.py report --output archive-report.csv",
    ),
    "16": (
        "Сравнивает встроенные главы YouTube-видео с текущими данными "
        "SponsorBlock и при необходимости переписывает контейнер без "
        "перекодирования. Перед записью показывает план и запрашивает "
        "подтверждение.",
        "video_tools.py bookmarks --apply",
    ),
}
def format_description(description: str) -> str:
    return description


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Единый интерфейс инструментов видеоархива."
    )
    parser.add_argument("--version", action="version", version=VERSION)
    parser.add_argument(
        "--root",
        type=Path,
        default=DEFAULT_ROOT_PATH(),
        help="Корневая папка архива.",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG_FILE(),
        help="Путь к config.toml.",
    )
    output_group = parser.add_mutually_exclusive_group()
    output_group.add_argument(
        "--verbose",
        action="store_true",
        help="Показать фактические пути и запускаемую команду.",
    )
    output_group.add_argument(
        "--quiet",
        action="store_true",
        help="Скрыть обычный вывод, оставив ошибки и предупреждения.",
    )
    folder_group = parser.add_mutually_exclusive_group()
    folder_group.add_argument(
        "--folder",
        action="append",
        dest="folders",
        help="Папка или маска папок. Можно указать несколько раз.",
    )
    folder_group.add_argument(
        "--folder-file",
        type=Path,
        help="UTF-8 файл со списком папок или масок, по одной на строку.",
    )
    folder_group.add_argument(
        "--all-folders",
        action="store_true",
        help="Обрабатывать все папки архива (режим по умолчанию).",
    )
    parser.add_argument(
        "command",
        choices=["doctor", *COMMAND_SCRIPTS],
        help="Команда для запуска.",
    )
    parser.add_argument(
        "arguments",
        nargs=argparse.REMAINDER,
        help="Аргументы выбранной команды.",
    )
    return parser


def execute_command(
    command_name: str,
    arguments: list[str],
    *,
    root: Path,
    config_path: Path,
    verbose: bool = False,
    quiet: bool = False,
    folders: tuple[str, ...] = (),
) -> int:
    if quiet:
        captured = io.StringIO()
        with contextlib.redirect_stdout(captured):
            result = execute_command(
                command_name,
                arguments,
                root=root,
                config_path=config_path,
                folders=folders,
            )
        for line in captured.getvalue().splitlines():
            if "[ERROR]" in line or "[WARNING]" in line:
                print(line)
        return result

    if verbose:
        print(f"[VERBOSE] root={root}")
        print(f"[VERBOSE] config={config_path}")
        rendered_arguments = " ".join(arguments) if arguments else "(нет)"
        print(f"[VERBOSE] command={command_name}; arguments={rendered_arguments}")
        print(
            "[VERBOSE] folders="
            + (", ".join(folders) if folders else "(все)")
        )

    if folders and command_name not in FOLDER_FILTER_COMMANDS:
        print(
            f"[ERROR] Команда {command_name} не поддерживает выбор папок. "
            "Запустите её без --folder/--folder-file."
        )
        return 2

    if command_name == "doctor":
        if arguments:
            print("[ERROR] Команда doctor не принимает дополнительные аргументы.")
            return 2
        results = run_doctor(root, config_path)
        print_results(results)
        return doctor_exit_code(results)

    module_name = COMMAND_SCRIPTS[command_name]
    command_arguments = []
    if command_name in ROOT_AWARE_COMMANDS:
        command_arguments.extend(["--root", str(root)])
    command_arguments.extend(arguments)

    previous_argv = sys.argv
    previous_config = os.environ.get(CONFIG_ENV_NAME)
    previous_folders = os.environ.get(FOLDER_FILTER_ENV)
    os.environ[CONFIG_ENV_NAME] = str(config_path)
    if folders:
        os.environ[FOLDER_FILTER_ENV] = json.dumps(
            folders,
            ensure_ascii=False,
        )
    else:
        os.environ.pop(FOLDER_FILTER_ENV, None)
    try:
        module = INTERNAL_MODULES[module_name]
        if command_name == "subtitles" and folders:
            for folder in folders:
                command_arguments.extend(["--folder", folder])
        sys.argv = [f"{module_name}.py", *command_arguments]
        return int(module.main() or 0)
    except (OSError, ImportError) as error:
        print(f"[ERROR] Не удалось запустить {command_name}: {error}")
        return 2
    finally:
        sys.argv = previous_argv
        if previous_config is None:
            os.environ.pop(CONFIG_ENV_NAME, None)
        else:
            os.environ[CONFIG_ENV_NAME] = previous_config
        if previous_folders is None:
            os.environ.pop(FOLDER_FILTER_ENV, None)
        else:
            os.environ[FOLDER_FILTER_ENV] = previous_folders


def interactive_menu(
    *,
    root: Path,
    config_path: Path,
    input_fn=input,
) -> int:
    while True:
        print(f"Video Tools {VERSION}")
        print(f"Архив: {root}")
        print()
        for key, (label, _, _) in MENU_OPTIONS.items():
            print(f"{key:>2}. {label}")
            if key in MENU_HELP:
                description, _ = MENU_HELP[key]
                print(f"    {format_description(description)}")
                print()

        try:
            choice = input_fn("Выберите действие: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\nВыход.")
            return 0

        option = MENU_OPTIONS.get(choice)
        if not option:
            print(f"[ERROR] Неизвестный пункт меню: {choice!r}")
            print()
            continue

        label, command_name, arguments = option
        if command_name is None:
            print("Выход.")
            return 0
        if command_name == "rename" and "--apply" in arguments:
            print(
                "[WARNING] Будут реально переименованы подходящие файлы "
                "во всех подпапках."
            )
            try:
                confirmation = input_fn("Продолжить? [Y/n]: ")
            except (EOFError, KeyboardInterrupt):
                print("\n[CANCEL] Переименование не выполнялось.")
                return 0
            if not is_affirmative_reply(confirmation):
                print("[CANCEL] Переименование не выполнялось.")
                print()
                continue

        print(f"\n[RUN] {label}")
        execute_command(
            command_name,
            list(arguments),
            root=root,
            config_path=config_path,
        )
        print()
        try:
            input_fn("Нажмите Enter, чтобы вернуться в меню...")
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        print()


def main(argv: list[str] | None = None, *, input_fn=input) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    if not arguments:
        return interactive_menu(
            root=DEFAULT_ROOT_PATH().resolve(),
            config_path=DEFAULT_CONFIG_FILE().resolve(),
            input_fn=input_fn,
        )

    args = build_parser().parse_args(arguments)
    try:
        folders = resolve_folder_filter(
            args.root.resolve(),
            args.folders,
            args.folder_file,
            all_folders=args.all_folders,
        )
    except (OSError, ValueError) as error:
        print(f"[ERROR] Не удалось выбрать папки: {error}")
        return 2
    return execute_command(
        args.command,
        args.arguments,
        root=args.root.resolve(),
        config_path=args.config.resolve(),
        verbose=args.verbose,
        quiet=args.quiet,
        folders=folders,
    )


if __name__ == "__main__":
    raise SystemExit(main())
