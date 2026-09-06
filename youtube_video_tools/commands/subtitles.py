"""Get Subtitles implementation."""

import argparse
import time
from pathlib import Path

from .. import config as video_config
from ..models import SourceType, parse_source_ref
from ..services.process import ExternalToolError
from ..services.yt_dlp import YtDlpClient
from . import inventory

BASE_DIR = video_config.BASE_DIR
configured_command = video_config.configured_command
configured_path = video_config.configured_path
load_config = video_config.load_config


VIDEO_EXTENSIONS = inventory.VIDEO_EXTENSIONS
SUBTITLE_EXTENSIONS = inventory.SUBTITLE_EXTENSIONS


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
        default=configured_path(config, "cookies", env_name="YOUTUBE_COOKIES_FILE"),
    )
    args = parser.parse_args()
    args.folders = args.folders or list(subtitle_config.get("folders", ["Deep Look"]))
    args.languages = args.languages or list(subtitle_config.get("languages", ["ru-en-US"]))
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
        middle = candidate.name[len(prefix) : -len(candidate.suffix)]
        candidate_key = middle.casefold()
        if candidate_key == language_key or candidate_key.startswith(f"{language_key}."):
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
) -> int:
    source = parse_source_ref(video_path.name)
    if not source or source.source_type is not SourceType.YOUTUBE:
        print(f"[SKIP] Не удалось найти ID: {video_path.name}")
        return 0

    errors = 0
    url = f"https://www.youtube.com/watch?v={source.source_id}"
    for language in languages:
        if subtitle_exists(video_path, language):
            print(f"[SKIP] Субтитры {language} уже есть: {video_path.name}")
            continue

        command = [
            "--write-auto-sub",
            "--sub-langs",
            language,
            "--skip-download",
            "-o",
            str(video_path.with_suffix(".%(ext)s")),
            url,
        ]
        print(f"[DOWNLOAD] {language}: {video_path.name}")
        try:
            result = YtDlpClient(yt_dlp, cookies_file=cookies).run(command, timeout=timeout)
            if result.returncode:
                print(f"[ERROR] Не удалось скачать субтитры: код {result.returncode}")
                errors += 1
        except ExternalToolError as error:
            print(f"[ERROR] {error}")
            errors += 1

        if pause > 0:
            time.sleep(pause)
    return errors


def main() -> int:
    args = parse_args()
    if args.cookies and not args.cookies.exists():
        print(f"[ERROR] Cookies-файл не найден: {args.cookies}")
        return 2

    errors = 0
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
            errors += download_subtitles(
                video_path,
                languages=args.languages,
                cookies=args.cookies,
                yt_dlp=args.yt_dlp,
                timeout=args.timeout,
                pause=args.pause,
            )
    return 1 if errors else 0
