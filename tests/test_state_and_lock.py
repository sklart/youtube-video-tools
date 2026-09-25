import tempfile
from pathlib import Path
from unittest.mock import patch

from tests.support import IsolatedTestCase
from youtube_video_tools import cache as video_cache
from youtube_video_tools import cli
from youtube_video_tools.access import AccessMode, command_access
from youtube_video_tools.locking import ArchiveLock, ArchiveLockedError
from youtube_video_tools.state import atomic_write_json


class StateAndLockTests(IsolatedTestCase):
    def test_abbreviated_write_options_require_lock(self):
        self.assertIs(command_access("report", ["--o=test.csv"]), AccessMode.STATE_WRITE)
        self.assertIs(command_access("archive-sync", ["--ap"]), AccessMode.ARCHIVE_WRITE)

    def test_command_root_override_cannot_bypass_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with ArchiveLock(root):
                result = cli.execute_command(
                    "subtitles",
                    ["--root", str(root)],
                    root=root / "other",
                    config_path=self.test_config,
                )
            self.assertEqual(result, 1)

    def test_stale_file_and_exception_do_not_hold_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            lock = ArchiveLock(root)
            lock.path.parent.mkdir(parents=True)
            lock.path.write_text("old process", encoding="utf-8")
            with self.assertRaises(RuntimeError), lock:
                raise RuntimeError("simulated crash")
            with ArchiveLock(root):
                pass

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
