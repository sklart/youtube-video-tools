"""Bookmark redownload helpers."""

import os
import tempfile
from pathlib import Path

from ..console import get_console
from ..progress import DownloadProgressRenderer
from ..services.process import ExternalToolError
from ..services.yt_dlp import YtDlpClient
from .constants import MARK_CATEGORIES, REMOVE_CATEGORIES
from .rewriter import validate_temporary_video


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

    cleanup_prefix = temp_output.stem
    renderer = DownloadProgressRenderer(get_console(), redownload=True)
    try:
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

        try:
            result = YtDlpClient(yt_dlp, cookies_file=cookies).stream(
                [*renderer.arguments(), *command],
                timeout=timeout,
                on_line=renderer.on_line,
            )
        except ExternalToolError as error:
            return False, str(error)

        if result.returncode != 0:
            return False, f"yt-dlp завершился с кодом {result.returncode}"

        if not temp_output.exists():
            matches = sorted(
                path
                for path in video_path.parent.iterdir()
                if path.is_file()
                and path.name.startswith(cleanup_prefix)
                and path.suffix == video_path.suffix
            )
            if matches:
                temp_output = matches[0]
            else:
                return False, "перекачивание завершилось без выходного файла"

        renderer.console.stage("↳ Проверка нового файла")
        valid, error = validate_temporary_video(temp_output, ffprobe=ffprobe)
        if not valid:
            temp_output.unlink(missing_ok=True)
            return False, f"временный файл не прошёл проверку: {error}"
        temp_output.replace(video_path)
        renderer.console.finish_live("✓ Видео успешно заменено")
        return True, None
    finally:
        renderer.close()
        for temporary in video_path.parent.iterdir():
            if temporary.name.startswith(cleanup_prefix) and temporary.is_file():
                temporary.unlink(missing_ok=True)
