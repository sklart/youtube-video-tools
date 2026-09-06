"""Source identifiers encoded in archive filenames."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum


class SourceType(StrEnum):
    YOUTUBE = "youtube"
    RUTUBE = "rutube"


@dataclass(frozen=True)
class SourceRef:
    source_type: SourceType
    source_id: str


_YOUTUBE_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
_RUTUBE_ID = re.compile(r"^[0-9a-f]{32}$", re.IGNORECASE)
_BRACKETS = re.compile(r"\[([^\[\]]+)\]")


def parse_source_ref(filename: str) -> SourceRef | None:
    """Extract a recognised source ID without treating descriptive brackets as IDs."""
    for value in reversed(_BRACKETS.findall(filename)):
        if _YOUTUBE_ID.fullmatch(value):
            return SourceRef(SourceType.YOUTUBE, value)
        if _RUTUBE_ID.fullmatch(value):
            return SourceRef(SourceType.RUTUBE, value)
    return None
