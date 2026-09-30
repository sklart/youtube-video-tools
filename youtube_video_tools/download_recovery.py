"""Targeted recovery of existing files rejected by SponsorBlock cutting."""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path

from .bookmarks.redownload import redownload_video
from .console import get_console
from .progress import PostprocessEvent, parse_event
from .state import atomic_write_text

MISMATCH = "Cannot cut video since the real and expected durations mismatch"


def file_stamp(path: Path) -> tuple[int, ...]:
    stat = path.stat()
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns


@dataclass(frozen=True)
class Candidate:
    root: Path
    path: Path
    video_id: str
    stamp: tuple[int, ...]

    @classmethod
    def capture(cls, root: Path, event: PostprocessEvent) -> Candidate:
        path = Path(event.filepath)
        if not event.filepath or not path.is_absolute():
            raise ValueError("нет точного абсолютного пути")
        if not re.fullmatch(r"[A-Za-z0-9_-]{11}", event.video_id):
            raise ValueError("некорректный YouTube ID")
        if not path.stem.endswith(f"[{event.video_id}]"):
            raise ValueError("ID не совпадает с именем файла")
        if path.suffix.lower() not in {".mp4", ".mkv", ".webm", ".mov", ".avi"}:
            raise ValueError("неподдерживаемый контейнер")
        root = root.resolve(strict=True)
        resolved = path.resolve(strict=True)
        if resolved != path.absolute() or not resolved.is_relative_to(root) or not path.is_file():
            raise ValueError("путь вне архива или содержит перенаправление")
        return cls(root, path, event.video_id, file_stamp(path))

    def verify(self) -> None:
        if (
            self.path.resolve(strict=True) != self.path
            or not self.path.is_relative_to(self.root)
            or not self.path.is_file()
            or file_stamp(self.path) != self.stamp
        ):
            raise ValueError("оригинал изменился после обнаружения ошибки; замена отменена")


class RecoveryQueue:
    def __init__(self, root: Path):
        self.root = root
        self.active: Candidate | None = None
        self.pending: dict[Path, Candidate] = {}
        self.unresolved = 0

    def on_line(self, line: str) -> None:
        text = line.strip()
        if text.startswith("VT_"):
            self.active = None
            try:
                event = parse_event(text)
                if (
                    isinstance(event, PostprocessEvent)
                    and event.postprocessor.removeprefix("FFmpeg") == "ModifyChapters"
                    and event.status == "started"
                ):
                    self.active = Candidate.capture(self.root, event)
            except (ValueError, TypeError, OSError, RuntimeError):
                pass
            return
        if text.startswith("ERROR:"):
            candidate, self.active = self.active, None
            if MISMATCH in text:
                if candidate is not None:
                    self.pending.setdefault(candidate.path, candidate)
                else:
                    self.unresolved += 1
                    get_console().warning(
                        "Адресное перекачивание недоступно: нет безопасной связи ошибки с ID и путём."
                    )
        elif text and not text.startswith(("[debug]", "[ModifyChapters]", "WARNING:")):
            # Extractor output and unknown records must not inherit the previous video's identity.
            self.active = None


def choose_action(path: Path, *, input_fn=None) -> str:
    if input_fn is None:
        if not getattr(sys.stdin, "isatty", lambda: False)():
            return "skip_all"
        input_fn = input
    try:
        answer = (
            input_fn(
                f"Перекачать {path.name}? [R] да / [S] пропустить / "
                "[RA] перекачать все / [SA] пропустить все: "
            )
            .strip()
            .casefold()
        )
    except EOFError:
        return "skip_all"
    return {"r": "redownload", "ra": "redownload_all", "sa": "skip_all"}.get(answer, "skip")


def record_download(archive: Path, video_id: str) -> None:
    text = archive.read_text(encoding="utf-8") if archive.exists() else ""
    entry = f"youtube {video_id}"
    if entry not in {line.strip() for line in text.splitlines()}:
        atomic_write_text(
            archive, text + ("\n" if text and not text.endswith("\n") else "") + entry + "\n"
        )


def recover(
    queue: RecoveryQueue,
    *,
    archive: Path,
    yt_dlp: str,
    cookies: Path,
    ffprobe: str,
    timeout: int,
    input_fn=None,
) -> tuple[int, int, int]:
    restored = skipped = failed = 0
    policy = ""
    for candidate in queue.pending.values():
        action = policy or choose_action(candidate.path, input_fn=input_fn)
        if action in {"redownload_all", "skip_all"}:
            policy = action
        if action in {"skip", "skip_all"}:
            skipped += 1
            continue
        try:
            candidate.verify()
            success, error = redownload_video(
                candidate.path,
                candidate.video_id,
                yt_dlp=yt_dlp,
                cookies=cookies,
                ffprobe=ffprobe,
                timeout=timeout,
                before_replace=candidate.verify,
            )
            if not success:
                raise ValueError(error or "перекачивание не завершено")
            record_download(archive, candidate.video_id)
            restored += 1
        except (OSError, ValueError, RuntimeError) as error:
            failed += 1
            get_console().error(f"{candidate.path.name}: {error}")
    if queue.pending or queue.unresolved:
        get_console().warning(
            f"Восстановление: успешно {restored}; пропущено {skipped}; "
            f"ошибок {failed}; без безопасного пути {queue.unresolved}. "
            "Исходный код завершения загрузки сохранён."
        )
    return restored, skipped, failed
