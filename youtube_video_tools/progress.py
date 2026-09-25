"""Structured yt-dlp events and shared dependency-free progress rendering."""

from __future__ import annotations

import json
import math
import re
import time
from dataclasses import dataclass

from .console import Console, display_width, truncate_display

INFO_FIELDS = "id,title,playlist_index,playlist_count,n_entries,format_id,vcodec,acodec"
PROGRESS_FIELDS = (
    "status,downloaded_bytes,total_bytes,total_bytes_estimate,speed,eta,elapsed,postprocessor"
)
EVENT_BODY = (
    '{"info":%(info.{' + INFO_FIELDS + '})j,"progress":%(progress.{' + PROGRESS_FIELDS + "})j}"
)
DOWNLOAD_TEMPLATE = "download:VT_PROGRESS:" + EVENT_BODY
POSTPROCESS_TEMPLATE = "postprocess:VT_POSTPROCESS:" + EVENT_BODY
COMPLETE_TEMPLATE = "after_move:VT_COMPLETE:%(.{" + INFO_FIELDS + "})j"
START_TEMPLATE = "before_dl:VT_START:%(.{" + INFO_FIELDS + "})j"
ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


def clean_text(value: object) -> str:
    text = ANSI.sub("", str(value))
    return "".join(char if char.isprintable() else " " for char in text).strip()


def _text(value: object) -> str:
    return "" if value is None or value == "NA" else clean_text(value)


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        return float(value) if math.isfinite(value) and value >= 0 else None
    except (OverflowError, ValueError):
        return None


def _integer(value: object) -> int | None:
    number = _number(value)
    return int(number) if number is not None else None


@dataclass(frozen=True)
class DownloadProgressEvent:
    video_id: str = ""
    title: str = ""
    playlist_index: int | None = None
    playlist_count: int | None = None
    status: str = ""
    downloaded_bytes: int | None = None
    total_bytes: int | None = None
    total_bytes_estimate: int | None = None
    speed: float | None = None
    eta: float | None = None
    elapsed: float | None = None
    format_id: str = ""
    vcodec: str = ""
    acodec: str = ""


@dataclass(frozen=True)
class PostprocessEvent:
    video_id: str = ""
    title: str = ""
    postprocessor: str = ""
    status: str = ""
    playlist_index: int | None = None
    playlist_count: int | None = None


@dataclass(frozen=True)
class CompleteEvent:
    video_id: str = ""
    title: str = ""
    playlist_index: int | None = None
    playlist_count: int | None = None


@dataclass(frozen=True)
class StartEvent:
    video_id: str = ""
    title: str = ""
    playlist_index: int | None = None
    playlist_count: int | None = None


def parse_event(
    line: str,
) -> DownloadProgressEvent | PostprocessEvent | CompleteEvent | StartEvent | None:
    prefix, separator, payload = line.strip().partition(":")
    if not separator or prefix not in {"VT_PROGRESS", "VT_POSTPROCESS", "VT_COMPLETE", "VT_START"}:
        return None
    data = json.loads(payload)
    if not isinstance(data, dict):
        raise ValueError("event must be an object")
    info = data if prefix in {"VT_COMPLETE", "VT_START"} else data.get("info", {})
    progress = data.get("progress", {})
    if not isinstance(info, dict) or not isinstance(progress, dict):
        raise ValueError("info/progress must be objects")
    common = dict(
        video_id=_text(info.get("id")),
        title=_text(info.get("title")),
        playlist_index=_integer(info.get("playlist_index")),
        playlist_count=_integer(info.get("playlist_count")) or _integer(info.get("n_entries")),
    )
    if prefix == "VT_COMPLETE":
        return CompleteEvent(**common)
    if prefix == "VT_START":
        return StartEvent(**common)
    if prefix == "VT_POSTPROCESS":
        return PostprocessEvent(
            **common,
            postprocessor=_text(progress.get("postprocessor")),
            status=_text(progress.get("status")),
        )
    return DownloadProgressEvent(
        **common,
        status=_text(progress.get("status")),
        **{
            key: _integer(progress.get(key))
            for key in ("downloaded_bytes", "total_bytes", "total_bytes_estimate")
        },
        **{key: _number(progress.get(key)) for key in ("speed", "eta", "elapsed")},
        **{key: _text(info.get(key)) for key in ("format_id", "vcodec", "acodec")},
    )


def format_time(seconds: float | None) -> str:
    if seconds is None:
        return "--:--"
    minutes, secs = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours:02}:{minutes:02}:{secs:02}" if hours else f"{minutes:02}:{secs:02}"


def stream_stage(event: DownloadProgressEvent) -> str:
    video = bool(event.vcodec and event.vcodec != "none")
    audio = bool(event.acodec and event.acodec != "none")
    if video and event.acodec == "none":
        return "Видео"
    if audio and event.vcodec == "none":
        return "Аудио"
    return "Медиа"


class DownloadProgressRenderer:
    def __init__(self, console: Console, *, redownload: bool = False, clock=time.monotonic):
        self.console = console
        self.redownload = redownload
        self.clock = clock
        self.started = clock()
        self.last_key = None
        self.last_time = float("-inf")
        self.last_percent: float | None = None
        self.last_stage = None
        self.completed: set[tuple] = set()
        self.errors = 0

    def arguments(self) -> list[str]:
        arguments = [
            "--newline",
            "--no-colors",
            "--progress",
            "--progress-delta",
            "0.25" if self.console.is_interactive else "1",
            "--progress-template",
            DOWNLOAD_TEMPLATE,
            "--progress-template",
            POSTPROCESS_TEMPLATE,
            "--print",
            COMPLETE_TEMPLATE,
            "--print",
            START_TEMPLATE,
            "--no-simulate",
            "--no-quiet",
        ]
        if self.console.verbose_enabled:
            arguments.append("--verbose")
        return arguments

    def label(self, event) -> str:
        prefix = "Перекачивание " if self.redownload else ""
        if event.playlist_index is not None:
            count = f"/{event.playlist_count:02}" if event.playlist_count is not None else ""
            prefix += f"{event.playlist_index:02}{count} "
        return prefix

    def frame(self, event: DownloadProgressEvent, percent: float | None) -> str:
        title = event.title or event.video_id or "Видео без названия"
        percentage = f"{percent:5.1f}%" if percent is not None else "  --.-%"
        filled = int(percent / 5) if percent is not None else 0
        bar = "█" * filled + "░" * (20 - filled)
        prefix = f"↓ {self.label(event)}· {stream_stage(event)} {bar} {percentage}"
        total = event.total_bytes or event.total_bytes_estimate
        downloaded = event.downloaded_bytes
        size = f"{downloaded / 1048576:.1f}/" if downloaded is not None else "?/"
        size += f"{total / 1048576:.1f} MiB" if total else "? MiB"
        details = [
            f"прошло {format_time(event.elapsed)}",
            size,
            f"{event.speed / 1048576:.1f} MiB/s" if event.speed is not None else "? MiB/s",
            f"ETA {format_time(event.eta)}",
        ]
        if not self.console.is_interactive:
            return " │ ".join([prefix, *details, title])
        width = self.console.terminal_width - 1
        while details and display_width(" │ ".join([prefix, *details])) + 15 > width:
            details.pop(0)
        if display_width(prefix) + 15 > width:
            prefix = f"↓ {self.label(event)}{stream_stage(event)} {percentage}"
        if display_width(prefix) + 5 > width:
            prefix = percentage.strip()
        fixed = " │ ".join([prefix, *details]) + " │ "
        return truncate_display(
            fixed + truncate_display(title, width - display_width(fixed)), width
        )

    def on_line(self, raw_line: str) -> None:
        line = raw_line.strip()
        if not line:
            return
        try:
            event = parse_event(line)
        except (ValueError, TypeError, OverflowError) as error:
            self.console.verbose(
                f"Некорректное progress-событие: {clean_text(error)}; {clean_text(line)}"
            )
            return
        if isinstance(event, DownloadProgressEvent):
            self.download(event)
        elif isinstance(event, PostprocessEvent):
            key = (event.video_id or event.title, event.playlist_index, event.postprocessor)
            if key != self.last_stage:
                self.last_stage = key
                name = event.postprocessor.removeprefix("FFmpeg")
                description = {
                    "Merger": "Объединение видео и аудио",
                    "SponsorBlock": "SponsorBlock: обработка сегментов",
                    "ModifyChapters": "Запись глав",
                }.get(name, f"Обработка: {name or 'неизвестный этап'}")
                self.console.stage(f"↳ {self.label(event)}{description}")
        elif isinstance(event, StartEvent):
            self.console.write_live(
                f"↓ {self.label(event)}Подготовка │ {event.title or event.video_id}"
            )
        elif isinstance(event, CompleteEvent):
            key = (event.video_id or event.title, event.playlist_index)
            if key[0] and key not in self.completed:
                self.completed.add(key)
                self.console.finish_live(
                    f"✓ {self.label(event)}{'Файл скачан' if self.redownload else 'Готово'} │ {event.title or event.video_id}"
                )
        else:
            text = clean_text(line)
            if re.match(r"^(?:ERROR:|\[error\])", text, re.IGNORECASE):
                self.errors += 1
                self.console.error(text)
            elif re.match(r"^(?:WARNING:|\[warning\])", text, re.IGNORECASE):
                self.console.warning(text)
            else:
                self.console.verbose(text)

    def download(self, event: DownloadProgressEvent) -> None:
        self.last_stage = None
        key = (
            event.video_id or event.title,
            event.playlist_index,
            event.format_id,
            stream_stage(event),
        )
        total = event.total_bytes or event.total_bytes_estimate
        percent = (
            100 * min(1.0, max(0.0, event.downloaded_bytes / total))
            if total and event.downloaded_bytes is not None
            else None
        )
        finished = event.status == "finished"
        if event.status == "error":
            self.on_line(f"ERROR: {event.title or event.video_id}: ошибка загрузки")
            return
        if finished:
            percent = 100.0
        now = self.clock()
        if not finished and key == self.last_key:
            interval = 0.25 if self.console.is_interactive else 1.0
            notable = (
                not self.console.is_interactive
                and percent is not None
                and (self.last_percent is None or abs(percent - self.last_percent) >= 10)
            )
            if now - self.last_time < interval and not notable:
                return
        self.last_key, self.last_time, self.last_percent = key, now, percent
        frame = self.frame(event, percent)
        if self.console.is_interactive:
            self.console.write_live(frame)
        else:
            self.console.progress(frame)

    def finish(self, returncode: int) -> None:
        self.close()
        if returncode and not self.errors:
            self.on_line(f"ERROR: yt-dlp завершился с кодом {returncode}")
        parts = []
        if self.completed:
            parts.append(f"Видео завершено: {len(self.completed)}")
        parts.extend(
            [
                f"Сообщений об ошибках: {self.errors}",
                f"Время: {format_time(self.clock() - self.started)}",
            ]
        )
        if returncode or self.errors:
            self.console.warning("Загрузка завершена с ошибками. " + "; ".join(parts))
        else:
            self.console.success("Готово. " + "; ".join(parts))

    def close(self) -> None:
        self.console.clear_live()
