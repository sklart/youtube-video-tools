"""Check Resolution implementation."""

import argparse
import os
from pathlib import Path

from .. import config as video_config
from ..core import path_selected
from ..services.ffmpeg import FFprobeClient
from ..services.process import ExternalToolError
from . import inventory

configured_command = video_config.configured_command
load_config = video_config.load_config

VIDEO_EXTENSIONS = inventory.VIDEO_EXTENSIONS

BASE_DIR = video_config.BASE_DIR


def get_video_resolution(file_path, ffprobe="ffprobe"):
    """Возвращает (width, height) видео через ffprobe"""
    try:
        result = FFprobeClient(ffprobe).run(
            [
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=width,height",
                "-of",
                "csv=s=x:p=0",
                str(file_path),
            ],
            timeout=30,
        )
        if result.returncode == 0 and result.stdout.strip():
            width, height = result.stdout.strip().split("x")
            return int(width), int(height)
    except (ExternalToolError, ValueError):
        return None
    return None


def colorize_resolution(height: int, resolution: str) -> str:
    """Return a plain resolution string suitable for redirected output."""
    return resolution


def main() -> int:
    parser = argparse.ArgumentParser(description="Проверяет разрешение видео.")
    parser.add_argument("--root", type=Path, default=BASE_DIR)
    args = parser.parse_args()
    root_path = args.root.resolve()
    ffprobe = configured_command(load_config(), "ffprobe", "ffprobe")

    if not root_path.is_dir():
        print(f"[ERROR] Корневая папка не найдена: {root_path}")
        return 2
    print(f"[CHECK] Проверка видеофайлов в {root_path} ...\n")
    failures = 0
    for root, _, files in os.walk(root_path):
        for file in files:
            if Path(file).suffix.lower() in VIDEO_EXTENSIONS:
                file_path = Path(root) / file
                if not path_selected(root_path, file_path):
                    continue
                res = get_video_resolution(file_path, ffprobe)
                if res:
                    w, h = res
                    resolution = f"{w}x{h}"
                    print(f"{file_path}  ->  {colorize_resolution(h, resolution)}")
                else:
                    print(f"{file_path}  ->  [Не удалось определить]")
                    failures += 1
    return 1 if failures else 0
