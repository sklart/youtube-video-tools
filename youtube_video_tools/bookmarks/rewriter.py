"""Bookmark rewriter helpers."""

import os
import tempfile
from pathlib import Path

from ..services.ffmpeg import FFmpegClient, FFprobeClient
from ..services.process import ExternalToolError


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
