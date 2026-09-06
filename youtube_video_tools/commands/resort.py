"""Resort implementation."""

import argparse
import json
import os
import shutil
import subprocess
import tempfile
import time
import uuid
from pathlib import Path

from .. import cache as video_metadata_cache
from .. import config as video_config
from .. import journal as video_journal
from ..core import is_affirmative_reply, normalize_windows_name
from ..models import parse_source_ref
from . import inventory

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
    default_cookies = configured_path(config, "cookies", env_name="YOUTUBE_COOKIES_FILE")

    parser = argparse.ArgumentParser(description="Сортирует видео из корня по папкам авторов.")
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
    source = parse_source_ref(filename)
    return (source.source_id, source.source_type.value) if source else (None, "unknown")


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
        if event.get("event") == "undo_end" and event.get("result") == "success"
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
        candidate = destination.with_name(f"{destination.stem}_{index}{destination.suffix}")
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
                destination_dir
                / sanitize_name(
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
                    destination_dir
                    / sanitize_name(
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
            raise OSError(f"{error}; кроме того, не удалось полностью откатить операцию") from error
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

    print(f"[SUMMARY] Перемещено видео: {moved}; ошибок: {failed}; запуск: {run_id}")
    video_metadata_cache.save_cache(root, metadata_cache_state)
    return 1 if failed else 0
