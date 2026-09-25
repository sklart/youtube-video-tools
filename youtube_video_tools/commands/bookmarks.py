"""Update Bookmarks implementation."""

import argparse
import time
import uuid
from dataclasses import dataclass
from functools import partial
from pathlib import Path

from .. import cache as video_metadata_cache
from .. import config as video_config
from .. import journal as video_journal
from ..bookmarks.constants import MARK_CATEGORIES as MARK_CATEGORIES
from ..bookmarks.constants import REMOVE_CATEGORIES as REMOVE_CATEGORIES
from ..bookmarks.constants import REMOVE_CATEGORY_SET as REMOVE_CATEGORY_SET
from ..bookmarks.planner import chapter_category as chapter_category
from ..bookmarks.planner import chapter_label as chapter_label
from ..bookmarks.planner import chapter_title as chapter_title
from ..bookmarks.planner import chapters_equal as chapters_equal
from ..bookmarks.planner import extract_removed_segments as extract_removed_segments
from ..bookmarks.planner import is_trimmed_local_video as is_trimmed_local_video
from ..bookmarks.planner import normalize_chapters as normalize_chapters
from ..bookmarks.planner import short_chapter_diff as short_chapter_diff
from ..bookmarks.planner import summarize_transition as summarize_transition
from ..bookmarks.redownload import redownload_video as redownload_video
from ..bookmarks.redownload import simplify_terminal_line as simplify_terminal_line
from ..bookmarks.rewriter import build_ffmetadata as build_ffmetadata
from ..bookmarks.rewriter import escape_ffmetadata_value as escape_ffmetadata_value
from ..bookmarks.rewriter import rewrite_embedded_chapters as rewrite_embedded_chapters
from ..bookmarks.rewriter import validate_temporary_video as validate_temporary_video
from ..bookmarks.scanner import cache_sponsorblock_metadata as cache_sponsorblock_metadata
from ..bookmarks.scanner import classify_remote_error as classify_remote_error
from ..bookmarks.scanner import fetch_remote_video_info as fetch_remote_video_info
from ..bookmarks.scanner import is_private_or_unavailable_error as is_private_or_unavailable_error
from ..bookmarks.scanner import is_rate_limited_message as is_rate_limited_message
from ..bookmarks.scanner import is_retry_later_error as is_retry_later_error
from ..bookmarks.scanner import iter_youtube_videos as iter_youtube_videos
from ..bookmarks.scanner import read_embedded_chapters as read_embedded_chapters
from ..bookmarks.scanner import read_local_duration as read_local_duration
from ..bookmarks.state import BOOKMARKS_PLAN_REPORT_FILE as BOOKMARKS_PLAN_REPORT_FILE
from ..bookmarks.state import BOOKMARKS_SCAN_STATE_FILE as BOOKMARKS_SCAN_STATE_FILE
from ..bookmarks.state import SCAN_SCHEMA_VERSION as SCAN_SCHEMA_VERSION
from ..bookmarks.state import clear_scan_state as clear_scan_state
from ..bookmarks.state import file_identity as file_identity
from ..bookmarks.state import identity_matches as identity_matches
from ..bookmarks.state import load_scan_state as load_scan_state
from ..bookmarks.state import persist_scan_progress as persist_scan_progress
from ..bookmarks.state import plan_report_path as plan_report_path
from ..bookmarks.state import rebuild_scan_results as rebuild_scan_results
from ..bookmarks.state import save_scan_state as save_scan_state
from ..bookmarks.state import scan_state_path as scan_state_path
from ..bookmarks.state import state_record_for_scan as state_record_for_scan
from ..bookmarks.state import write_plan_report as write_plan_report
from ..console import get_console
from ..core import is_affirmative_reply
from ..services.ffmpeg import FFmpegClient as FFmpegClient
from ..services.ffmpeg import FFprobeClient as FFprobeClient
from ..services.process import ExternalToolError as ExternalToolError
from ..services.yt_dlp import YtDlpClient as YtDlpClient
from ..settings import Settings
from . import inventory

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


@dataclass(frozen=True)
class ProgressTracker:
    line: object
    tick: object


def parse_args() -> argparse.Namespace:
    config = load_config()
    bookmarks_config = Settings.from_mapping(config).bookmarks
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
        default=bookmarks_config.pause_seconds,
        help="Пауза между запросами к YouTube в секундах.",
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=bookmarks_config.max_retries,
        help="Сколько раз повторять временно ограниченный запрос к YouTube.",
    )
    parser.add_argument(
        "--retry-backoff-seconds",
        type=float,
        default=bookmarks_config.retry_backoff_seconds,
        help="Пауза перед повтором после rate limit YouTube.",
    )
    parser.add_argument(
        "--ffmpeg-timeout",
        type=int,
        default=bookmarks_config.ffmpeg_timeout_seconds,
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

    return ProgressTracker(line=line, tick=tick)


def confirm_apply(plan_count: int, assume_yes: bool, input_fn=input) -> bool:
    if assume_yes:
        return True
    try:
        answer = input_fn("Перезаписать встроенные главы в перечисленных видео? [Y/n]: ")
    except (EOFError, KeyboardInterrupt):
        return False
    return is_affirmative_reply(answer)


def main() -> int:
    args = parse_args()
    root = args.root.resolve()
    if args.cookies and not args.cookies.exists():
        get_console().error(f"Cookies-файл не найден: {args.cookies}")
        return 2

    videos = iter_youtube_videos(root)
    get_console().info(f"[{'APPLY' if args.apply else 'DRY-RUN'}] YouTube-видео: {len(videos)}")

    metadata_cache_state = load_cache(root)
    scan_state = load_scan_state(root, apply_mode=args.apply) or {
        "schema_version": SCAN_SCHEMA_VERSION,
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
        get_console().info(
            f"[RESUME] Найдены результаты предыдущего прохода: "
            f"{len(saved_records)}/{len(videos)} видео уже обработано."
        )
    pending_retry_records = [
        relative
        for relative, record in scan_state.get("records", {}).items()
        if isinstance(record, dict) and record.get("result") == "retry_later"
    ]
    if pending_retry_records:
        get_console().info(
            f"[RESUME] Временный бан YouTube прервал проверку для "
            f"{len(pending_retry_records)} видео; они будут проверены повторно."
        )

    total_videos = len(videos)
    check_tracker = build_progress_tracker()
    for index, (video_path, video_id) in enumerate(videos, start=1):
        relative = relative_path(root, video_path)
        if relative in saved_records:
            continue
        try:
            make_record = partial(
                state_record_for_scan, identity=file_identity(root, video_path, video_id)
            )
        except OSError as error:
            get_console().error(f"{relative}: {error}")
            failed += 1
            continue
        get_console().info(check_tracker.line("CHECK", index, total_videos, relative))
        current, current_error = read_embedded_chapters(
            video_path,
            ffprobe=args.ffprobe,
            timeout=args.timeout,
        )
        if current_error:
            get_console().error(f"{relative}: {current_error}")
            failed += 1
            scan_state["records"][relative] = {
                **make_record(
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
            get_console().error(f"{relative}: {duration_error}")
            failed += 1
            scan_state["records"][relative] = {
                **make_record(
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
            get_console().error(f"{relative}: {remote_error}")
            error_kind = classify_remote_error(remote_error)
            if error_kind == "retry_later":
                retry_later += 1
                scan_state["records"][relative] = make_record(
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
                    **make_record(
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
                        **make_record(
                            video_id=video_id,
                            result="trimmed_redownload",
                            remote_info=remote_info,
                        ),
                        "trimmed_policy": "redownload_all",
                    }
                elif action == "skip_all":
                    trimmed_policy = "skip_all"
                    get_console().warning(
                        f"SKIP: {relative}: пропущено для всех уже обрезанных видео."
                    )
                    scan_state["records"][relative] = {
                        **make_record(
                            video_id=video_id,
                            result="trimmed_skip",
                            remote_info=remote_info,
                        ),
                        "trimmed_policy": "skip_all",
                    }
                elif action == "redownload":
                    pending_redownloads.append((video_path, video_id, relative, remote_info))
                    scan_state["records"][relative] = make_record(
                        video_id=video_id,
                        result="trimmed_redownload",
                        remote_info=remote_info,
                    )
                else:
                    get_console().warning(
                        f"SKIP: {relative}: уже обрезано, обновление глав небезопасно."
                    )
                    scan_state["records"][relative] = make_record(
                        video_id=video_id,
                        result="trimmed_skip",
                        remote_info=remote_info,
                    )
            else:
                get_console().info(
                    f"[TRIMMED] {relative}: видео уже обрезано; "
                    "безопасное обновление глав требует перекачивания."
                )
                scan_state["records"][relative] = make_record(
                    video_id=video_id,
                    result="trimmed",
                    remote_info=remote_info,
                )
            persist_scan_progress(root, scan_state, metadata_cache_state)
            check_tracker.tick()
            continue

        if chapters_equal(current, target):
            up_to_date += 1
            scan_state["records"][relative] = make_record(
                video_id=video_id,
                result="up_to_date",
            )
            persist_scan_progress(root, scan_state, metadata_cache_state)
            if args.pause_seconds > 0 and index < total_videos:
                time.sleep(args.pause_seconds)
            check_tracker.tick()
            continue

        get_console().info(f"[PLAN] {relative}: {summarize_transition(current, target)}")
        planned.append((video_path, current, target))
        scan_state["records"][relative] = make_record(
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
    get_console().info(f"[REPORT] План сохранён: {relative_path(root, report_path)}")
    get_console().info(
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

    if planned:
        get_console().warning(
            f"Будет обновлено {len(planned)} файлов; "
            f"перекачивание потребуется для {len(pending_redownloads)}."
        )
    if planned and not confirm_apply(len(planned), args.yes):
        get_console().warning("Обновление закладок не выполнялось. План сохранён для продолжения.")
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
        get_console().info(
            apply_tracker.line(
                "APPLY", update_index, total_updates, relative_path(root, video_path)
            )
        )
        try:
            record = scan_state["records"].get(relative_path(root, video_path), {})
            if not identity_matches(root, video_path, str(record.get("video_id", "")), record):
                raise RuntimeError(
                    "STALE: файл изменился после сканирования; запустите проверку заново."
                )
            rewrite_embedded_chapters(
                video_path,
                target,
                ffmpeg=args.ffmpeg,
                ffprobe=args.ffprobe,
                timeout=args.ffmpeg_timeout,
            )
            updated += 1
            record = scan_state["records"].get(relative_path(root, video_path))
            if isinstance(record, dict):
                record["apply_result"] = "success"
                record.update(file_identity(root, video_path, str(record["video_id"])))
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
            get_console().info(f"[UPDATED] {relative_path(root, video_path)}")
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
            get_console().error(f"{relative_path(root, video_path)}: {error}")
            apply_tracker.tick()

    redownloaded = 0
    total_redownloads = len(pending_redownloads)
    redownload_tracker = build_progress_tracker()
    for redownload_index, (video_path, video_id, relative, remote_info) in enumerate(
        pending_redownloads, start=1
    ):
        get_console().info(
            redownload_tracker.line("REDOWNLOAD", redownload_index, total_redownloads, relative)
        )
        record = scan_state["records"].get(relative, {})
        if not identity_matches(root, video_path, video_id, record):
            get_console().warning(
                f"STALE: {relative}: файл изменился после сканирования; перекачивание отменено."
            )
            failed += 1
            continue
        success, error = redownload_video(
            video_path,
            video_id,
            yt_dlp=args.yt_dlp,
            cookies=args.cookies,
            ffprobe=args.ffprobe,
            timeout=args.timeout,
        )
        if success:
            redownloaded += 1
            cache_sponsorblock_metadata(metadata_cache_state, video_id, remote_info)
            record = scan_state["records"].get(relative)
            if isinstance(record, dict):
                record["redownload_result"] = "success"
                record.update(file_identity(root, video_path, video_id))
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
            get_console().info(f"[UPDATED] {relative}: перекачано.")
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
            get_console().error(f"{relative}: {error}")
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
    get_console().info(
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
