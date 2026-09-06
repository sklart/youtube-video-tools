"""Archive Report implementation."""

import argparse
import csv
from dataclasses import dataclass
from pathlib import Path

from .. import config as video_config
from ..core import extract_filename_date
from . import inventory

BASE_DIR = video_config.BASE_DIR
collect_inventory = inventory.collect_inventory


@dataclass(frozen=True)
class ReportIssue:
    relative_path: str
    missing_id: bool
    missing_date: bool
    invalid_date: bool
    missing_author: bool
    missing_subtitles: bool

    @property
    def reasons(self) -> tuple[str, ...]:
        reasons = []
        if self.missing_id:
            reasons.append("нет ID")
        if self.missing_date:
            reasons.append("нет даты")
        if self.invalid_date:
            reasons.append("некорректная дата")
        if self.missing_author:
            reasons.append("нет папки автора")
        if self.missing_subtitles:
            reasons.append("нет субтитров")
        return tuple(reasons)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Создаёт отчёт о неполных метаданных видео.")
    parser.add_argument("--root", type=Path, default=BASE_DIR)
    parser.add_argument(
        "--output",
        type=Path,
        help="Сохранить найденные проблемы в CSV.",
    )
    return parser.parse_args()


def inspect_archive(root: Path) -> tuple[int, list[ReportIssue]]:
    records = collect_inventory(root)
    issues = []
    for record in records:
        date_text, valid_date = extract_filename_date(record.filename)
        issue = ReportIssue(
            relative_path=record.relative_path,
            missing_id=not bool(record.source_id),
            missing_date=date_text is None,
            invalid_date=date_text is not None and not valid_date,
            missing_author=not bool(record.folder),
            missing_subtitles=record.subtitle_count == 0,
        )
        if issue.reasons:
            issues.append(issue)
    return len(records), issues


def write_report(output: Path, issues: list[ReportIssue]) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8-sig", newline="") as csv_file:
        writer = csv.writer(csv_file, delimiter=";")
        writer.writerow(
            [
                "relative_path",
                "missing_id",
                "missing_date",
                "invalid_date",
                "missing_author",
                "missing_subtitles",
                "reasons",
            ]
        )
        for issue in issues:
            writer.writerow(
                [
                    issue.relative_path,
                    int(issue.missing_id),
                    int(issue.missing_date),
                    int(issue.invalid_date),
                    int(issue.missing_author),
                    int(issue.missing_subtitles),
                    " | ".join(issue.reasons),
                ]
            )


def main() -> int:
    args = parse_args()
    root = args.root.resolve()
    if not root.is_dir():
        print(f"[ERROR] Корневая папка не найдена: {root}")
        return 2

    checked, issues = inspect_archive(root)
    for issue in issues:
        print(f"[ISSUE] {issue.relative_path}: {', '.join(issue.reasons)}")

    if args.output:
        output = args.output.resolve()
        try:
            write_report(output, issues)
        except OSError as error:
            print(f"[ERROR] Не удалось записать {output}: {error}")
            return 2
        print(f"[OK] CSV: {output}")

    counters = {
        "без ID": sum(issue.missing_id for issue in issues),
        "без даты": sum(issue.missing_date for issue in issues),
        "неверная дата": sum(issue.invalid_date for issue in issues),
        "без автора": sum(issue.missing_author for issue in issues),
        "без субтитров": sum(issue.missing_subtitles for issue in issues),
    }
    details = "; ".join(f"{name}: {count}" for name, count in counters.items())
    print(f"[SUMMARY] Видео: {checked}; с проблемами: {len(issues)}; {details}")
    return 0
