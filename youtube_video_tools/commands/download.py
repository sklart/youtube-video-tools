"""Download Yt Favorites implementation."""

import argparse
from pathlib import Path

from .. import config as video_config
from ..services.process import ExternalToolError
from ..services.yt_dlp import YtDlpClient
from . import archive_sync as sync_download_archive

BASE_DIR = video_config.BASE_DIR
configured_command = video_config.configured_command
configured_path = video_config.configured_path
load_config = video_config.load_config
synchronize_archive = sync_download_archive.synchronize_archive


PLAYLIST_URL = "https://www.youtube.com/playlist?list=WL"


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
        print(f"[ERROR] Cookies-файл не найден: {cookies_file}")
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
            print("[ERROR] Загрузка отменена из-за ошибки синхронизации.")
            return sync_result

    arguments = [
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

    def show_line(raw_line: str) -> None:
        line = simplify_terminal_line(raw_line.strip())
        if not line:
            return
        if "[download]" in line:
            print(f"\r[PROCESSING] {line}", end="")
        elif "error" in line.lower():
            print(f"\n[ERROR] {line}")

    try:
        result = YtDlpClient(yt_dlp, cookies_file=cookies_file).stream(arguments, on_line=show_line)
    except ExternalToolError as error:
        print(f"[ERROR] {error}")
        return 2

    if result.returncode != 0:
        print(f"\n[ERROR] yt-dlp завершился с кодом {result.returncode}")
    else:
        print()

    return result.returncode
