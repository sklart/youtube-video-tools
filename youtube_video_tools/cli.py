"""Command line interface and interactive menu."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from . import __version__
from .commands import (
    archive_sync,
    bookmarks,
    dates,
    doctor,
    download,
    duplicates,
    inventory,
    rename,
    report,
    resolution,
    resort,
    subtitles,
)
from .config import CONFIG_ENV_NAME, default_config_path, default_root_path
from .console import Console, configure_stdout, use_console
from .core import FOLDER_FILTER_ENV, is_affirmative_reply, resolve_folder_filter
from .locking import ArchiveLock, ArchiveLockedError

COMMAND_MODULES = {
    "download": download,
    "bookmarks": bookmarks,
    "subtitles": subtitles,
    "rename": rename,
    "resort": resort,
    "duplicates": duplicates,
    "dates": dates,
    "resolution": resolution,
    "inventory": inventory,
    "report": report,
    "archive-sync": archive_sync,
}
ROOT_AWARE_COMMANDS = set(COMMAND_MODULES)
FOLDER_FILTER_COMMANDS = {
    "subtitles",
    "rename",
    "duplicates",
    "dates",
    "resolution",
    "inventory",
    "report",
    "bookmarks",
}
MENU_OPTIONS = {
    "1": ("Проверить окружение (doctor)", "doctor", []),
    "2": ("Скачать избранное YouTube", "download", []),
    "3": ("Скачать субтитры", "subtitles", []),
    "4": ("Предпросмотр переименования", "rename", ["--dry-run"]),
    "5": ("Выполнить переименование", "rename", ["--apply"]),
    "6": ("Предпросмотр сортировки", "resort", ["--dry-run"]),
    "7": ("Выполнить сортировку", "resort", ["--apply"]),
    "8": ("Отменить последнюю сортировку", "resort", ["--undo-last"]),
    "9": ("Найти дубли", "duplicates", []),
    "10": ("Проверить даты в именах", "dates", []),
    "11": ("Проверить разрешение видео", "resolution", []),
    "12": ("Создать CSV-инвентаризацию", "inventory", []),
    "13": ("Проверить архив загрузок", "archive-sync", ["--dry-run"]),
    "14": ("Синхронизировать архив загрузок", "archive-sync", ["--apply"]),
    "15": ("Отчёт о проблемах архива", "report", []),
    "16": ("Обновить закладки SponsorBlock", "bookmarks", ["--apply"]),
    "0": ("Выход", None, []),
}
MENU_HELP = {
    "1": "Проверяет Python, config.toml, cookies, yt-dlp, ffprobe, ffmpeg, свободное место, права записи и журнал.",
    "2": "Скачивает новые видео из плейлиста YouTube «Смотреть позже».",
    "3": "Скачивает автоматические субтитры для папок и языков из config.toml.",
    "4": "Получает даты публикации с YouTube и показывает план новых имён.",
    "5": "Получает даты публикации и реально переименовывает подходящие файлы.",
    "6": "Показывает план распределения файлов по папкам авторов.",
    "7": "Перемещает видео и связанные субтитры по папкам авторов.",
    "8": "Возвращает файлы из последнего завершённого запуска сортировки.",
    "9": "Ищет повторяющиеся YouTube ID и точные копии файлов. Файлы не удаляет.",
    "10": "Проверяет даты формата ДД.ММ.ГГГГ в именах видео.",
    "11": "Показывает разрешение видео с помощью ffprobe.",
    "12": "Создаёт CSV-инвентаризацию по файловым метаданным.",
    "13": "Показывает устаревшие ID в yt-dlp-archive.txt.",
    "14": "Удаляет устаревшие ID из yt-dlp-archive.txt с резервной копией.",
    "15": "Показывает видео без ID, даты, папки автора или субтитров.",
    "16": "Сравнивает встроенные главы с данными SponsorBlock и обновляет их без перекодирования.",
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Единый интерфейс инструментов видеоархива.")
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument(
        "--root", type=Path, default=default_root_path(), help="Корневая папка архива."
    )
    parser.add_argument(
        "--config", type=Path, default=default_config_path(), help="Путь к config.toml."
    )
    output_group = parser.add_mutually_exclusive_group()
    output_group.add_argument(
        "--verbose", action="store_true", help="Показать фактические пути и команду."
    )
    output_group.add_argument(
        "--quiet",
        action="store_true",
        help="Скрыть обычный вывод, оставив предупреждения и ошибки.",
    )
    folder_group = parser.add_mutually_exclusive_group()
    folder_group.add_argument(
        "--folder", action="append", dest="folders", help="Папка или маска папок."
    )
    folder_group.add_argument(
        "--folder-file", type=Path, help="UTF-8 файл со списком папок или масок."
    )
    folder_group.add_argument(
        "--all-folders", action="store_true", help="Обрабатывать все папки архива."
    )
    parser.add_argument(
        "command", choices=["doctor", *COMMAND_MODULES], help="Команда для запуска."
    )
    parser.add_argument("arguments", nargs=argparse.REMAINDER, help="Аргументы выбранной команды.")
    return parser


def execute_command(
    command_name: str,
    arguments: list[str],
    *,
    root: Path,
    config_path: Path,
    verbose: bool = False,
    quiet: bool = False,
    folders: tuple[str, ...] = (),
) -> int:
    console = Console(quiet=quiet, verbose=verbose)
    if verbose:
        console.verbose(f"root={root}")
        console.verbose(f"config={config_path}")
        console.verbose(
            f"command={command_name}; arguments={' '.join(arguments) if arguments else '(нет)'}"
        )
        console.verbose("folders=" + (", ".join(folders) if folders else "(все)"))
    if folders and command_name not in FOLDER_FILTER_COMMANDS:
        console.error(f"Команда {command_name} не поддерживает выбор папок.")
        return 2
    if command_name == "doctor":
        if arguments:
            console.error("Команда doctor не принимает дополнительные аргументы.")
            return 2
        results = doctor.run_doctor(root, config_path)
        doctor.print_results(results, console=console)
        return doctor.doctor_exit_code(results)
    command_arguments = ["--root", str(root), *arguments]
    if command_name == "subtitles" and folders:
        for folder in folders:
            command_arguments.extend(["--folder", folder])
    previous_argv = sys.argv
    previous_config = os.environ.get(CONFIG_ENV_NAME)
    previous_folders = os.environ.get(FOLDER_FILTER_ENV)
    os.environ[CONFIG_ENV_NAME] = str(config_path)
    if folders:
        os.environ[FOLDER_FILTER_ENV] = json.dumps(folders, ensure_ascii=False)
    else:
        os.environ.pop(FOLDER_FILTER_ENV, None)
    mutating = (
        command_name == "download"
        or (command_name in {"rename", "archive-sync", "bookmarks"} and "--apply" in arguments)
        or (command_name == "resort" and ({"--apply", "--undo-last"} & set(arguments)))
    )
    try:
        sys.argv = [f"{command_name}.py", *command_arguments]
        with use_console(console):
            if mutating:
                with ArchiveLock(root):
                    return int(COMMAND_MODULES[command_name].main() or 0)
            return int(COMMAND_MODULES[command_name].main() or 0)
    except ArchiveLockedError as error:
        console.error(str(error))
        return 1
    except KeyboardInterrupt:
        console.warning("Операция прервана.")
        return 130
    except (OSError, ImportError) as error:
        console.error(f"Не удалось запустить {command_name}: {error}")
        return 2
    finally:
        sys.argv = previous_argv
        if previous_config is None:
            os.environ.pop(CONFIG_ENV_NAME, None)
        else:
            os.environ[CONFIG_ENV_NAME] = previous_config
        if previous_folders is None:
            os.environ.pop(FOLDER_FILTER_ENV, None)
        else:
            os.environ[FOLDER_FILTER_ENV] = previous_folders


def interactive_menu(*, root: Path, config_path: Path, input_fn=input) -> int:
    while True:
        print(f"Video Tools {__version__}\nАрхив: {root}\n")
        for key, (label, _, _) in MENU_OPTIONS.items():
            print(f"{key:>2}. {label}")
            if key in MENU_HELP:
                print(f"    {MENU_HELP[key]}\n")
        try:
            choice = input_fn("Выберите действие: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\nВыход.")
            return 0
        option = MENU_OPTIONS.get(choice)
        if not option:
            print(f"[ERROR] Неизвестный пункт меню: {choice!r}\n")
            continue
        label, command_name, arguments = option
        if command_name is None:
            print("Выход.")
            return 0
        if command_name == "rename" and "--apply" in arguments:
            if not is_affirmative_reply(input_fn("Продолжить переименование? [Y/n]: ")):
                print("[CANCEL] Переименование не выполнялось.\n")
                continue
        print(f"\n[RUN] {label}")
        execute_command(command_name, list(arguments), root=root, config_path=config_path)
        try:
            input_fn("Нажмите Enter, чтобы вернуться в меню...")
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        print()


def main(argv: list[str] | None = None, *, input_fn=input) -> int:
    configure_stdout()
    arguments = sys.argv[1:] if argv is None else argv
    if not arguments:
        return interactive_menu(
            root=default_root_path().resolve(),
            config_path=default_config_path().resolve(),
            input_fn=input_fn,
        )
    args = build_parser().parse_args(arguments)
    try:
        folders = resolve_folder_filter(
            args.root.resolve(), args.folders, args.folder_file, all_folders=args.all_folders
        )
    except (OSError, ValueError) as error:
        Console().error(f"Не удалось выбрать папки: {error}")
        return 2
    return execute_command(
        args.command,
        args.arguments,
        root=args.root.resolve(),
        config_path=args.config.resolve(),
        verbose=args.verbose,
        quiet=args.quiet,
        folders=folders,
    )
