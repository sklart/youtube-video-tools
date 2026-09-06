"""Inventory implementation."""

import argparse
import csv
from dataclasses import dataclass
from pathlib import Path

from .. import config as video_config
from ..core import (
    DEFAULT_SUBTITLE_EXTENSIONS,
    DEFAULT_VIDEO_EXTENSIONS,
    extract_filename_date,
    path_selected,
)
from ..models import parse_source_ref

BASE_DIR = video_config.BASE_DIR


VIDEO_EXTENSIONS = DEFAULT_VIDEO_EXTENSIONS
SUBTITLE_EXTENSIONS = DEFAULT_SUBTITLE_EXTENSIONS


@dataclass(frozen=True)
class InventoryRecord:
    folder: str
    relative_path: str
    filename: str
    extension: str
    size_bytes: int
    source_type: str
    source_id: str
    upload_date: str
    subtitle_count: int
    subtitles: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Создаёт CSV-инвентаризацию без чтения видеопотока."
    )
    parser.add_argument("--root", type=Path, default=BASE_DIR)
    parser.add_argument(
        "--output",
        type=Path,
        help="Путь итогового CSV. По умолчанию <root>/inventory.csv.",
    )
    return parser.parse_args()


def extract_source(filename: str) -> tuple[str, str]:
    source = parse_source_ref(filename)
    return (source.source_type.value, source.source_id) if source else ("", "")


def extract_date(filename: str) -> str:
    value, _ = extract_filename_date(filename)
    return value or ""


def related_subtitles(video_path: Path) -> list[Path]:
    subtitles = []
    prefix = f"{video_path.stem}."
    for candidate in video_path.parent.iterdir():
        if (
            candidate.is_file()
            and candidate.name.startswith(prefix)
            and candidate.suffix.lower() in SUBTITLE_EXTENSIONS
        ):
            subtitles.append(candidate)
    return sorted(subtitles, key=lambda path: path.name.casefold())


def build_record(root: Path, video_path: Path) -> InventoryRecord:
    relative = video_path.relative_to(root)
    folder = relative.parts[0] if len(relative.parts) > 1 else ""
    source_type, source_id = extract_source(video_path.name)
    subtitles = related_subtitles(video_path)
    return InventoryRecord(
        folder=folder,
        relative_path=str(relative),
        filename=video_path.name,
        extension=video_path.suffix.lower(),
        size_bytes=video_path.stat().st_size,
        source_type=source_type,
        source_id=source_id,
        upload_date=extract_date(video_path.name),
        subtitle_count=len(subtitles),
        subtitles=" | ".join(path.name for path in subtitles),
    )


def collect_inventory(root: Path) -> list[InventoryRecord]:
    records = []
    for path in root.rglob("*"):
        if path.is_file() and path_selected(root, path) and path.suffix.lower() in VIDEO_EXTENSIONS:
            records.append(build_record(root, path))
    return sorted(
        records,
        key=lambda record: (
            record.folder.casefold(),
            record.filename.casefold(),
            record.relative_path.casefold(),
        ),
    )


def write_inventory(output: Path, records: list[InventoryRecord]) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8-sig", newline="") as csv_file:
        writer = csv.writer(csv_file, delimiter=";")
        writer.writerow(
            [
                "folder",
                "relative_path",
                "filename",
                "extension",
                "size_bytes",
                "source_type",
                "source_id",
                "upload_date",
                "subtitle_count",
                "subtitles",
            ]
        )
        for record in records:
            writer.writerow(
                [
                    record.folder,
                    record.relative_path,
                    record.filename,
                    record.extension,
                    record.size_bytes,
                    record.source_type,
                    record.source_id,
                    record.upload_date,
                    record.subtitle_count,
                    record.subtitles,
                ]
            )


def main() -> int:
    args = parse_args()
    root = args.root.resolve()
    output = (args.output or (root / "inventory.csv")).resolve()

    if not root.is_dir():
        print(f"[ERROR] Корневая папка не найдена: {root}")
        return 2

    print(f"[SCAN] Файловые метаданные: {root}")
    records = collect_inventory(root)
    try:
        write_inventory(output, records)
    except OSError as error:
        print(f"[ERROR] Не удалось записать {output}: {error}")
        return 2

    total_bytes = sum(record.size_bytes for record in records)
    with_subtitles = sum(record.subtitle_count > 0 for record in records)
    print(f"[OK] CSV: {output}")
    print(
        f"[SUMMARY] Видео: {len(records)}; с субтитрами: {with_subtitles}; "
        f"размер: {total_bytes / (1024**3):.2f} ГБ"
    )
    return 0
