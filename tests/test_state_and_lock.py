import tempfile
import unittest
from pathlib import Path

from youtube_video_tools.locking import ArchiveLock, ArchiveLockedError
from youtube_video_tools.state import atomic_write_json


class StateAndLockTests(unittest.TestCase):
    def test_atomic_json_write_replaces_previous_valid_document(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "state.json"
            atomic_write_json(path, {"version": 1})
            atomic_write_json(path, {"version": 2, "items": ["ok"]})
            self.assertEqual(
                path.read_text(encoding="utf-8"),
                '{\n  "items": [\n    "ok"\n  ],\n  "version": 2\n}\n',
            )

    def test_second_lock_is_rejected_until_first_is_released(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            with ArchiveLock(root):
                with self.assertRaises(ArchiveLockedError):
                    with ArchiveLock(root):
                        pass
            with ArchiveLock(root):
                pass
