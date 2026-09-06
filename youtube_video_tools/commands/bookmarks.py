"""Update Bookmarks implementation."""

import argparse
import json
import os
import tempfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from .. import cache as video_metadata_cache
from .. import config as video_config
from .. import journal as video_journal
from ..core import is_affirmative_reply, path_selected
from ..services.ffmpeg import FFmpegClient, FFprobeClient
from ..services.process import ExternalToolError
from ..services.yt_dlp import YtDlpClient
from ..state import atomic_write_json
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

MARK_CATEGORIES = "all"
REMOVE_CATEGORIES = "sponsor,interaction,selfpromo"
REMOVE_CATEGORY_SET = {"sponsor", "interaction", "selfpromo"}


@dataclass(frozen=True)
class ProgressTracker:
    line: object
    tick: object


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
    command = [
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
    attempts = max(1, max_retries + 1)
    for attempt in range(1, attempts + 1):
        try:
            result = YtDlpClient(yt_dlp, cookies_file=cookies).run(command, timeout=timeout)
        except ExternalToolError as error:
            return None, str(error)

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
    command = [
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_chapters",
        str(video_path),
    ]
    try:
        result = FFprobeClient(ffprobe).run(command, timeout=timeout)
    except ExternalToolError as error:
        return None, str(error)

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
    command = [
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(video_path),
    ]
    try:
        result = FFprobeClient(ffprobe).run(command, timeout=timeout)
    except ExternalToolError as error:
        return None, str(error)

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
    ffprobe: str = "ffprobe",
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

        result = FFmpegClient(ffmpeg).run(cmd[1:], timeout=timeout)
        if result.returncode != 0:
            message = result.stderr.strip().splitlines()
            raise RuntimeError(
                message[-1] if message else f"ffmpeg завершился с кодом {result.returncode}"
            )
        valid, error = validate_temporary_video(temp_output, ffprobe=ffprobe)
        if not valid:
            raise RuntimeError(f"временный файл не прошёл проверку: {error}")
        temp_output.replace(video_path)
    except ExternalToolError as error:
        raise RuntimeError(f"ffmpeg превысил таймаут ({timeout} с): {error}") from error
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


def validate_temporary_video(path: Path, *, ffprobe: str) -> tuple[bool, str | None]:
    """Verify that a media replacement is non-empty and contains a video stream."""
    try:
        if not path.is_file():
            return False, "выходной файл не создан"
        if path.stat().st_size <= 0:
            return False, "выходной файл пуст"
        if not FFprobeClient(ffprobe).has_video_stream(path):
            return False, "ffprobe не нашёл видеопоток"
    except (OSError, ExternalToolError) as error:
        return False, str(error)
    return True, None


def simplify_terminal_line(line: str) -> str:
    cleaned = line.replace("\ufffd", "?")
    return "".join(
        character if character.isprintable() or character in "\r\n\t" else "?"
        for character in cleaned
    )


def redownload_video(
    video_path: Path,
    video_id: str,
    *,
    yt_dlp: str,
    cookies: Path | None,
    ffprobe: str = "ffprobe",
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

    command = [
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

    def show_line(raw_line: str) -> None:
        line = simplify_terminal_line(raw_line.strip())
        if not line:
            return
        if "[download]" in line:
            print(f"\r[REDOWNLOAD] {line}", end="")
        elif "error" in line.lower():
            print(f"\n[ERROR] {line}")

    try:
        result = YtDlpClient(yt_dlp, cookies_file=cookies).stream(
            command,
            timeout=timeout,
            on_line=show_line,
        )
    except ExternalToolError as error:
        return False, str(error)

    if result.returncode != 0:
        return False, f"yt-dlp завершился с кодом {result.returncode}"

    if not temp_output.exists():
        matches = sorted(video_path.parent.glob(f"{temp_output.stem}*{video_path.suffix}"))
        if matches:
            temp_output = matches[0]
        else:
            return False, "перекачивание завершилось без выходного файла"

    valid, error = validate_temporary_video(temp_output, ffprobe=ffprobe)
    if not valid:
        temp_output.unlink(missing_ok=True)
        return False, f"временный файл не прошёл проверку: {error}"
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
        str(key): value for key, value in records.items() if isinstance(value, dict)
    }
    return payload


def save_scan_state(root: Path, state: dict) -> None:
    path = scan_state_path(root)
    atomic_write_json(path, state)


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
    path_map = {
        relative_path(root, video_path): (video_path, video_id) for video_path, video_id in videos
    }
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

        print(f"[PLAN] {relative}: {summarize_transition(current, target)}")
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
        print(
            apply_tracker.line(
                "APPLY", update_index, total_updates, relative_path(root, video_path)
            )
        )
        try:
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
    for redownload_index, (video_path, video_id, relative, remote_info) in enumerate(
        pending_redownloads, start=1
    ):
        print(redownload_tracker.line("REDOWNLOAD", redownload_index, total_redownloads, relative))
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
