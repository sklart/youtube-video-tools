"""Check Date implementation."""

import argparse
from dataclasses import dataclass
from pathlib import Path

from .. import config as video_config
from ..api import AppContext, CommandResult
from ..console import get_console
from ..core import active_folder_filter, extract_filename_date, path_selected
from . import inventory

BASE_DIR = video_config.BASE_DIR
VIDEO_EXTENSIONS = inventory.VIDEO_EXTENSIONS


def parse_args(arguments=None):
    parser = argparse.ArgumentParser(
        description="Проверяет наличие и корректность даты в именах видео."
    )
    parser.add_argument("--root", type=Path, default=BASE_DIR)
    return parser.parse_args(arguments)


def inspect_video_dates(root: Path, folders: tuple[str, ...] | None = None):
    missing = []
    invalid = []
    checked = 0
    for path in root.rglob("*"):
        if (
            not path.is_file()
            or not path_selected(root, path, folders)
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


@dataclass(frozen=True)
class Options:
    folders: tuple[str, ...] = ()


def run(options: Options, context: AppContext) -> CommandResult:
    root = context.root
    console = context.console
    if not root.is_dir():
        error = f"Корневая папка не найдена: {root}"
        console.error(error)
        return CommandResult(exit_code=2, errors=(error,))

    checked, missing, invalid = inspect_video_dates(root, options.folders)
    for path in missing:
        console.info(f"Нет даты: {path}")
    for path, date_text in invalid:
        console.info(f"Некорректная дата {date_text}: {path}")
    summary = f"Видео: {checked}; без даты: {len(missing)}; некорректная дата: {len(invalid)}"
    if missing or invalid:
        console.warning(summary)
        return CommandResult(exit_code=1, warnings=(summary,))
    console.success(summary)
    return CommandResult()


def main():
    args = parse_args()
    return run(
        Options(folders=active_folder_filter()),
        AppContext(root=args.root.resolve(), console=get_console()),
    ).exit_code
