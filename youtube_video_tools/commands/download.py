"""Download Yt Favorites implementation."""

import argparse
import subprocess
import sys
from pathlib import Path

from .. import config as video_config
from . import archive_sync as sync_download_archive

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
        character if character.isprintable() or character in "\r\n\t" else "?"
        for character in cleaned
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Скачивает плейлист Смотреть позже.")
    parser.add_argument("--root", type=Path, default=BASE_DIR)
    args = parser.parse_args()
    root = args.root.resolve()
    config = load_config()
    cookies_file = configured_path(config, "cookies", env_name="YOUTUBE_COOKIES_FILE")
    yt_dlp = configured_command(config, "yt_dlp", "yt-dlp")
    archive_file = root / "yt-dlp-archive.txt"
    output_template = root / "%(uploader)s" / "%(title)s_%(upload_date>%d.%m.%Y)s [%(id)s].%(ext)s"

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
