"""Explicit command access policy, independent of video modification."""

from enum import Enum, auto


class AccessMode(Enum):
    READ_ONLY = auto()
    STATE_WRITE = auto()
    ARCHIVE_WRITE = auto()


def command_access(name: str, arguments: list[str]) -> AccessMode:
    if name in {"download", "subtitles"}:
        return AccessMode.ARCHIVE_WRITE
    if name in {"rename", "resort", "bookmarks", "archive-sync"} and (
        {"--apply", "--undo-last"} & set(arguments)
    ):
        return AccessMode.ARCHIVE_WRITE
    if name in {"rename", "resort", "bookmarks", "duplicates", "doctor", "inventory"}:
        return AccessMode.STATE_WRITE
    if name == "report" and any(
        arg.startswith("--")
        and len(arg.split("=")[0]) > 2
        and "--output".startswith(arg.split("=")[0])
        for arg in arguments
    ):
        return AccessMode.STATE_WRITE
    if name == "archive-sync" and any(
        arg.startswith("--") and len(arg) > 2 and "--apply".startswith(arg) for arg in arguments
    ):
        return AccessMode.ARCHIVE_WRITE
    return AccessMode.READ_ONLY
