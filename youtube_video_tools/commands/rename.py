"""Rename Files implementation."""

import argparse
import re
import subprocess
import uuid
from datetime import datetime
from pathlib import Path

from .. import cache as video_metadata_cache
from .. import config as video_config
from .. import journal as video_journal
from ..core import extract_filename_date, normalize_windows_name, path_selected
from ..models import SourceType, parse_source_ref

BASE_DIR = video_config.BASE_DIR
configured_command = video_config.configured_command
configured_path = video_config.configured_path
get_cached_field = video_metadata_cache.get_field
load_config = video_config.load_config
relative_path = video_journal.relative_path
set_cached_field = video_metadata_cache.set_field
write_journal_event = video_journal.write_journal_event


FILE_RE = re.compile(r"^(?P<title>.+) \[[^][]+\]\.(?P<ext>.+)$")
DATE_RE = re.compile(r"_\d{2}\.\d{2}\.\d{4}$")


def parse_args() -> argparse.Namespace:
    config = load_config()
    default_cookies = configured_path(config, "cookies", env_name="YOUTUBE_COOKIES_FILE")

    parser = argparse.ArgumentParser(description="Добавляет дату публикации в имена видеофайлов.")
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
        source = parse_source_ref(path.name)
        if match and source and source.source_type is SourceType.YOUTUBE:
            yield path, match, source.source_id


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

    for old_path, match, video_id in iter_candidates(root):
        title = match.group("title")
        existing_date, valid_date = extract_filename_date(old_path.name)
        if existing_date and valid_date:
            skipped += 1
            continue
        if existing_date and not valid_date:
            print(f"[WARNING] Некорректная дата в имени будет заменена: {old_path}")
            title = re.sub(r"_\d{2}\.\d{2}\.\d{4}$", "", title)

        upload_date, error = get_upload_date(
            video_id,
            yt_dlp=args.yt_dlp,
            cookies=args.cookies,
            timeout=args.timeout,
            metadata_cache=metadata_cache_state,
        )
        if error:
            print(f"[SKIP] {old_path}: {error}")
            failed += 1
            continue

        new_name = f"{title}_{upload_date} [{video_id}].{match.group('ext')}"
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

    print(f"[SUMMARY] Запланировано/выполнено: {renamed}; пропущено: {skipped}; ошибок: {failed}")
    video_metadata_cache.save_cache(root, metadata_cache_state)
    return 1 if failed else 0
