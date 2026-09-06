"""Cross-platform advisory lock for archive mutations."""

from __future__ import annotations

import os
from pathlib import Path


class ArchiveLockedError(RuntimeError):
    """Raised when another process is changing the same archive."""


class ArchiveLock:
    def __init__(self, root: Path) -> None:
        self.path = root / ".video-tools" / "archive.lock"
        self._stream = None

    def __enter__(self) -> ArchiveLock:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._stream = self.path.open("a+", encoding="utf-8")
        try:
            if os.name == "nt":
                import msvcrt

                self._stream.seek(0)
                if not self._stream.read(1):
                    self._stream.write("0")
                    self._stream.flush()
                self._stream.seek(0)
                msvcrt.locking(self._stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            self._stream.close()
            self._stream = None
            raise ArchiveLockedError("архив уже изменяется другим процессом") from error
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        if self._stream is None:
            return
        try:
            if os.name == "nt":
                import msvcrt

                self._stream.seek(0)
                msvcrt.locking(self._stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._stream.fileno(), fcntl.LOCK_UN)
        finally:
            self._stream.close()
            self._stream = None
