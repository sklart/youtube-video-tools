import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from youtube_video_tools import cache as video_cache
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

    def test_failed_replace_preserves_existing_state_and_cleans_temp_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "state.json"
            atomic_write_json(path, {"version": 1})
            with (
                patch("youtube_video_tools.state.os.replace", side_effect=OSError("blocked")),
                self.assertRaises(OSError),
            ):
                atomic_write_json(path, {"version": 2})
            self.assertEqual(path.read_text(encoding="utf-8"), '{\n  "version": 1\n}\n')
            self.assertEqual(list(Path(temp_dir).glob(".state.json.*")), [])

    def test_cache_stays_dirty_when_atomic_save_fails(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            state = video_cache.load_cache(root)
            video_cache.set_field(state, "youtube", "abcdefghijk", "title", "Title")
            with (
                patch.object(video_cache, "atomic_write_json", side_effect=OSError("blocked")),
                self.assertRaises(OSError),
            ):
                video_cache.save_cache(root, state)
            self.assertTrue(state["dirty"])
