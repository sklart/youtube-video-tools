"""Video Doctor implementation."""

import os
import shutil
import sys
import tempfile
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .. import cache as video_metadata_cache
from .. import config as video_config
from .. import journal as video_journal
from ..console import Console, Level
from ..services.process import ExternalToolError
from ..services.process import run as run_process

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
            "warning",
            f"не создан; используются значения по умолчанию ({config_path})",
        )
    try:
        with config_path.open("rb") as config_file:
            config = tomllib.load(config_file)
    except (OSError, tomllib.TOMLDecodeError) as error:
        return {}, CheckResult("Конфигурация", "error", str(error))

    errors = []
    paths = config.get("paths", {})
    if not isinstance(paths, dict):
        errors.append("[paths] должна быть таблицей")
    else:
        for key in ("cookies", "yt_dlp", "ffprobe", "ffmpeg"):
            if key in paths and (not isinstance(paths[key], str) or not paths[key].strip()):
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
        if "pause_seconds" in subtitles and (
            not isinstance(subtitles["pause_seconds"], (int, float))
            or isinstance(subtitles["pause_seconds"], bool)
            or subtitles["pause_seconds"] < 0
        ):
            errors.append("subtitles.pause_seconds должен быть неотрицательным числом")

    sorting = config.get("sorting", {})
    if not isinstance(sorting, dict):
        errors.append("[sorting] должна быть таблицей")
    else:
        max_retries = sorting.get("max_retries", 1)
        if not isinstance(max_retries, int) or isinstance(max_retries, bool):
            errors.append("sorting.max_retries должен быть целым числом")
        elif max_retries < 1:
            errors.append("sorting.max_retries должен быть не меньше 1")
        for key in ("pause_seconds",):
            if key in sorting and (
                not isinstance(sorting[key], (int, float))
                or isinstance(sorting[key], bool)
                or sorting[key] < 0
            ):
                errors.append(f"sorting.{key} должен быть неотрицательным числом")
        if "allow_unknown" in sorting and not isinstance(sorting["allow_unknown"], bool):
            errors.append("sorting.allow_unknown должен быть логическим значением")

    download = config.get("download", {})
    if not isinstance(download, dict):
        errors.append("[download] должна быть таблицей")
    elif "sync_archive_before_download" in download and not isinstance(
        download["sync_archive_before_download"], bool
    ):
        errors.append("download.sync_archive_before_download должен быть логическим значением")

    bookmarks = config.get("bookmarks", {})
    if not isinstance(bookmarks, dict):
        errors.append("[bookmarks] должна быть таблицей")
    else:
        for key in ("pause_seconds", "retry_backoff_seconds"):
            if key in bookmarks and (
                not isinstance(bookmarks[key], (int, float))
                or isinstance(bookmarks[key], bool)
                or bookmarks[key] < 0
            ):
                errors.append(f"bookmarks.{key} должен быть неотрицательным числом")
        if "max_retries" in bookmarks and (
            not isinstance(bookmarks["max_retries"], int)
            or isinstance(bookmarks["max_retries"], bool)
            or bookmarks["max_retries"] < 1
        ):
            errors.append("bookmarks.max_retries должен быть целым числом не меньше 1")
        if "ffmpeg_timeout_seconds" in bookmarks and (
            not isinstance(bookmarks["ffmpeg_timeout_seconds"], int)
            or isinstance(bookmarks["ffmpeg_timeout_seconds"], bool)
            or bookmarks["ffmpeg_timeout_seconds"] <= 0
        ):
            errors.append("bookmarks.ffmpeg_timeout_seconds должен быть целым числом больше 0")

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
        result = run_process([executable, *version_args], timeout=15)
    except ExternalToolError as error:
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


def check_cache(root: Path) -> CheckResult:
    path = video_metadata_cache.cache_path(root)
    if not path.exists():
        return CheckResult("Кэш", "ok", "ещё не создан")
    count, error = video_metadata_cache.inspect_cache(root)
    if error:
        return CheckResult("Кэш", "error", f"повреждён: {error}")
    return CheckResult("Кэш", "ok", f"{count} записей: {path}")


def run_doctor(root: Path, config_path: Path) -> list[CheckResult]:
    config, config_result = load_and_check_config(config_path)
    results = [
        check_python(),
        config_result,
        check_root(root),
    ]
    results.extend(
        [
            check_cookies(config),
            check_command("yt-dlp", configured_command(config, "yt_dlp", "yt-dlp"), ["--version"]),
            check_command(
                "ffprobe", configured_command(config, "ffprobe", "ffprobe"), ["-version"]
            ),
            check_command("ffmpeg", configured_command(config, "ffmpeg", "ffmpeg"), ["-version"]),
            check_journal(root),
            check_cache(root),
        ]
    )
    return results


def print_results(results: list[CheckResult], *, console: Console | None = None) -> None:
    console = console or Console()
    levels = {"ok": Level.SUCCESS, "warning": Level.WARNING, "error": Level.ERROR}
    for result in results:
        console.emit(levels[result.status], f"{result.name}: {result.message}")
    errors = sum(result.status == "error" for result in results)
    warnings = sum(result.status == "warning" for result in results)
    console.info(f"Ошибок: {errors}; предупреждений: {warnings}")


def doctor_exit_code(results: list[CheckResult]) -> int:
    return 1 if any(result.status == "error" for result in results) else 0
