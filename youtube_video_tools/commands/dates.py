"""Check Date implementation."""

import argparse
from pathlib import Path

from .. import config as video_config
from ..console import get_console
from ..core import extract_filename_date, path_selected
from . import inventory

BASE_DIR = video_config.BASE_DIR
VIDEO_EXTENSIONS = inventory.VIDEO_EXTENSIONS


def parse_args():
    parser = argparse.ArgumentParser(
        description="Проверяет наличие и корректность даты в именах видео."
    )
    parser.add_argument("--root", type=Path, default=BASE_DIR)
    return parser.parse_args()


def inspect_video_dates(root: Path):
    missing = []
    invalid = []
    checked = 0
    for path in root.rglob("*"):
        if (
            not path.is_file()
            or not path_selected(root, path)
            or path.suffix.lower() not in VIDEO_EXTENSIONS
        ):
            continue
        checked += 1
        date_text, is_valid = extract_filename_date(path.name)
        if date_text is None:
            missing.append(path)
        elif not is_valid:
            invalid.append((path, date_text))
    return checked, missing, invalid


def main():
    args = parse_args()
    root = args.root.resolve()
    if not root.is_dir():
        get_console().error(f"Корневая папка не найдена: {root}")
        return 2

    checked, missing, invalid = inspect_video_dates(root)
    for path in missing:
        get_console().info(f"[MISSING] {path}")
    for path, date_text in invalid:
        get_console().info(f"[INVALID] {date_text}: {path}")
    get_console().info(
        f"[SUMMARY] Видео: {checked}; без даты: {len(missing)}; некорректная дата: {len(invalid)}"
    )
    return 1 if missing or invalid else 0
