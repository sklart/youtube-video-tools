"""Adapters for ffmpeg and ffprobe."""

from __future__ import annotations

from pathlib import Path

from .process import ProcessResult, run


class FFmpegClient:
    def __init__(self, executable: str = "ffmpeg") -> None:
        self.executable = executable

    def run(self, arguments: list[str], *, timeout: float = 90) -> ProcessResult:
        return run([self.executable, *arguments], timeout=timeout)

    def version(self) -> ProcessResult:
        return self.run(["-version"], timeout=15)


class FFprobeClient:
    def __init__(self, executable: str = "ffprobe") -> None:
        self.executable = executable

    def run(self, arguments: list[str], *, timeout: float = 30) -> ProcessResult:
        return run([self.executable, *arguments], timeout=timeout)

    def version(self) -> ProcessResult:
        return self.run(["-version"], timeout=15)

    def has_video_stream(self, path: Path) -> bool:
        result = self.run(
            [
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=codec_type",
                "-of",
                "default=nokey=1:noprint_wrappers=1",
                str(path),
            ]
        )
        return result.returncode == 0 and "video" in result.stdout
