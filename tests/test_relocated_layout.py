import tempfile
from pathlib import Path
from unittest.mock import patch

from tests.support import IsolatedTestCase
from youtube_video_tools import cache, config, journal
from youtube_video_tools.commands import bookmarks, resort
from youtube_video_tools.locking import ArchiveLock


class RelocatedLayoutTests(IsolatedTestCase):
    def test_all_state_paths_follow_relocated_project(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "_video_tools"
            state = project / ".video-tools"
            with (
                patch.object(config, "PROJECT_DIR", project),
                patch.object(config, "BASE_DIR", root),
            ):
                paths = (
                    cache.cache_path(root),
                    journal.journal_path(root),
                    bookmarks.scan_state_path(root),
                    bookmarks.plan_report_path(root),
                    ArchiveLock(root).path,
                )
                for path in paths:
                    self.assertEqual(path.parent, state)
                with ArchiveLock(root):
                    self.assertTrue((state / "archive.lock").exists())
                source = root / "source.mp4"
                source.write_bytes(b"test")
                resort.preflight_apply(root, [[(source, root / "target.mp4")]])
                resort.log_error(root, "test", write_log=True)
                self.assertTrue((state / "errors.log").exists())
                self.assertFalse((root / "errors.log").exists())
                bookmarks.save_scan_state(root, {"records": {}})
                journal.write_journal_event(root, {"event": "test"})
                metadata = cache.load_cache(root)
                cache.set_field(metadata, "youtube", "abcdefghijk", "title", "test")
                cache.save_cache(root, metadata)
                self.assertFalse((root / ".video-tools").exists())
                other = root / "other-archive"
                self.assertEqual(config.state_directory(other), other / ".video-tools")

    def test_standard_checkout_keeps_original_state_location(self):
        root = Path("archive")
        with (
            patch.object(config, "PROJECT_DIR", root),
            patch.object(config, "BASE_DIR", root),
        ):
            self.assertEqual(config.state_directory(root), root / ".video-tools")
