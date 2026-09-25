"""Download Yt Favorites implementation."""

import argparse
from pathlib import Path

from .. import config as video_config
from ..console import get_console
from ..progress import DownloadProgressRenderer
from ..services.process import ExternalToolError
from ..services.yt_dlp import YtDlpClient
from ..settings import Settings
from . import archive_sync as sync_download_archive

BASE_DIR = video_config.BASE_DIR
configured_command = video_config.configured_command
configured_path = video_config.configured_path
load_config = video_config.load_config
synchronize_archive = sync_download_archive.synchronize_archive


PLAYLIST_URL = "https://www.youtube.com/playlist?list=WL"


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

    if not cookies_file or not cookies_file.is_file():
        get_console().error(
            f"Cookies-файл не найден или не настроен: {cookies_file or 'путь не указан'}. "
            "Укажите путь к файлу (не каталогу) в config.toml или YOUTUBE_COOKIES_FILE."
        )
        return 2
    get_console().info(f"Cookies-файл: {cookies_file}")

    if Settings.from_mapping(config).download.sync_archive_before_download:
        get_console().info("[SYNC] Проверка yt-dlp-archive.txt по локальным видео...")
        sync_result = synchronize_archive(
            root,
            archive_file,
            apply=True,
            assume_yes=True,
        )
        if sync_result != 0:
            get_console().error("Загрузка отменена из-за ошибки синхронизации.")
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

    renderer = DownloadProgressRenderer(get_console())
    auth_hint_shown = False

    def show_line(line: str) -> None:
        nonlocal auth_hint_shown
        renderer.on_line(line)
        message = line.casefold()
        if (
            not auth_hint_shown
            and "error:" in message
            and "[youtube:tab] wl:" in message
            and "playlist does not exist" in message
        ):
            auth_hint_shown = True
            get_console().warning(
                f"YouTube не предоставил доступ к «Смотреть позже». Cookies-файл: {cookies_file}. "
                "Наличие файла не подтверждает авторизацию: проверьте аккаунт и обновите cookies. "
                "Это сообщение YouTube само по себе не означает, что файл cookies отсутствует."
            )

    try:
        result = YtDlpClient(yt_dlp, cookies_file=cookies_file).stream(
            [*renderer.arguments(), *arguments], on_line=show_line
        )
    except ExternalToolError as error:
        renderer.on_line(f"ERROR: {error}")
        renderer.finish(2)
        return 2
    finally:
        renderer.close()
    renderer.finish(result.returncode)
    return result.returncode
