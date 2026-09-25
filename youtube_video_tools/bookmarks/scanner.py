"""Bookmark scanner helpers."""

import json
import time
from pathlib import Path

from ..cache import set_field as set_cached_field
from ..cache import set_value as set_cached_value
from ..commands.inventory import VIDEO_EXTENSIONS, extract_source
from ..console import get_console
from ..core import path_selected
from ..services.ffmpeg import FFprobeClient
from ..services.process import ExternalToolError
from ..services.yt_dlp import YtDlpClient
from .constants import MARK_CATEGORIES, REMOVE_CATEGORIES
from .planner import extract_removed_segments, normalize_chapters


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
            get_console().info(
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
