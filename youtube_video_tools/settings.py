"""Validated settings shared by command adapters and reusable APIs."""

import math
from dataclasses import dataclass, field, fields
from typing import Any


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class PathSettings:
    cookies: str = ""
    yt_dlp: str = "yt-dlp"
    ffprobe: str = "ffprobe"
    ffmpeg: str = "ffmpeg"


@dataclass(frozen=True)
class SubtitleSettings:
    folders: tuple[str, ...] = ("Deep Look",)
    languages: tuple[str, ...] = ("ru-en-US",)
    pause_seconds: float = 5.0


@dataclass(frozen=True)
class SortingSettings:
    pause_seconds: float = 2.0
    max_retries: int = 3
    allow_unknown: bool = False


@dataclass(frozen=True)
class DownloadSettings:
    sync_archive_before_download: bool = True


@dataclass(frozen=True)
class BookmarkSettings:
    pause_seconds: float = 3.0
    max_retries: int = 2
    retry_backoff_seconds: float = 30.0
    ffmpeg_timeout_seconds: int = 600


def _section(config: dict, name: str, model: type):
    values = config.get(name, {})
    if not isinstance(values, dict):
        raise ConfigError(f"Некорректная конфигурация: [{name}] должна быть таблицей")
    result = {}
    for item in fields(model):
        if item.name not in values:
            continue
        value = values[item.name]
        default = item.default
        if isinstance(default, bool):
            valid = isinstance(value, bool)
            expected = "должен быть логическим значением"
        elif isinstance(default, int):
            valid = type(value) is int and value >= 1
            expected = "должен быть целым числом не меньше 1"
        elif isinstance(default, float):
            valid = type(value) in (int, float) and math.isfinite(value) and value >= 0
            expected = "должен быть конечным числом не меньше 0"
        elif isinstance(default, tuple):
            valid = isinstance(value, list) and all(isinstance(v, str) and v.strip() for v in value)
            expected = "должен быть списком непустых строк"
        else:
            valid = isinstance(value, str) and bool(value.strip())
            expected = "должен быть непустой строкой"
        if not valid:
            raise ConfigError(
                f"Некорректная конфигурация: {name}.{item.name} = {value!r}; {expected}"
            )
        result[item.name] = tuple(value) if isinstance(default, tuple) else value
    return model(**result)


@dataclass(frozen=True)
class Settings:
    paths: PathSettings = field(default_factory=PathSettings)
    subtitles: SubtitleSettings = field(default_factory=SubtitleSettings)
    sorting: SortingSettings = field(default_factory=SortingSettings)
    download: DownloadSettings = field(default_factory=DownloadSettings)
    bookmarks: BookmarkSettings = field(default_factory=BookmarkSettings)

    @classmethod
    def from_mapping(cls, config: dict[str, Any]) -> "Settings":
        if not isinstance(config, dict):
            raise ConfigError("Конфигурация должна быть таблицей")
        return cls(
            paths=_section(config, "paths", PathSettings),
            subtitles=_section(config, "subtitles", SubtitleSettings),
            sorting=_section(config, "sorting", SortingSettings),
            download=_section(config, "download", DownloadSettings),
            bookmarks=_section(config, "bookmarks", BookmarkSettings),
        )
