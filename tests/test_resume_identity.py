import os
import tempfile
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

from tests.support import IsolatedTestCase
from youtube_video_tools.commands import bookmarks as b


class ResumeIdentityTests(IsolatedTestCase):
    def test_apply_rechecks_file_after_confirmation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            video = root / "Video [abcdefghijk].mp4"
            video.write_bytes(b"original")
            record = b.state_record_for_scan(
                video_id="abcdefghijk",
                result="planned",
                current=[],
                target=[{"title": "Intro"}],
                identity=b.file_identity(root, video, "abcdefghijk"),
            )
            b.save_scan_state(
                root,
                {
                    "schema_version": b.SCAN_SCHEMA_VERSION,
                    "mode": "apply",
                    "records": {video.name: record},
                },
            )
            options = Namespace(
                root=root,
                cookies=None,
                apply=True,
                yes=False,
                ffmpeg="ffmpeg",
                ffprobe="ffprobe",
                ffmpeg_timeout=1,
            )

            def confirm(*args):
                video.write_bytes(b"replaced after scan")
                return True

            with (
                patch.object(b, "parse_args", return_value=options),
                patch.object(b, "confirm_apply", side_effect=confirm),
                patch.object(b, "rewrite_embedded_chapters") as rewrite,
            ):
                self.assertEqual(b.main(), 1)
                rewrite.assert_not_called()
            self.assertEqual(video.read_bytes(), b"replaced after scan")

    def test_resume_requires_every_identity_field(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            video = root / "test.mp4"
            video.write_bytes(b"test")
            identity = b.file_identity(root, video, "abcdefghijk")
            for field, replacement in (
                ("video_id", "other-id"),
                ("size", 100),
                ("mtime_ns", 0),
                ("relative_path", "other.mp4"),
            ):
                with self.subTest(field=field):
                    record = {**identity, field: replacement, "result": "up_to_date"}
                    state = {
                        "schema_version": b.SCAN_SCHEMA_VERSION,
                        "records": {"test.mp4": record},
                    }
                    self.assertEqual(
                        b.rebuild_scan_results(
                            root, [(video, "abcdefghijk")], state, apply_mode=True
                        )[0],
                        {},
                    )
            record = {**identity, "result": "up_to_date"}
            state = {"schema_version": b.SCAN_SCHEMA_VERSION, "records": {"test.mp4": record}}
            self.assertEqual(
                len(
                    b.rebuild_scan_results(root, [(video, "abcdefghijk")], state, apply_mode=True)[
                        0
                    ]
                ),
                1,
            )
            os.utime(video, ns=(identity["mtime_ns"], identity["mtime_ns"] + 1000000000))
            self.assertFalse(b.identity_matches(root, video, "abcdefghijk", record))

    def test_unknown_schema_is_not_resumed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for version in (None, 0, 999):
                b.save_scan_state(root, {"schema_version": version, "mode": "apply", "records": {}})
                self.assertIsNone(b.load_scan_state(root, apply_mode=True))
