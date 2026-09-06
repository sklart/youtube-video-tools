import importlib
import tempfile
import unittest
from pathlib import Path

import video_tools

sync_download_archive = importlib.import_module("sync_download_archive")
from sync_download_archive import (
    apply_sync_plan,
    build_sync_plan,
    synchronize_archive,
)


class SyncDownloadArchiveTests(unittest.TestCase):
    def test_plan_removes_only_missing_youtube_ids(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            channel = root / "Channel"
            channel.mkdir()
            (channel / "Present [abcdefghijk].mp4").write_bytes(b"video")
            archive = root / "yt-dlp-archive.txt"
            archive.write_text(
                "youtube abcdefghijk\n"
                "youtube missing1234\n"
                "rutube rutube-id\n"
                "# comment\n",
                encoding="utf-8",
            )

            plan = build_sync_plan(root, archive)

            self.assertEqual(plan.removed_ids, ["missing1234"])
            self.assertEqual(
                plan.kept_lines,
                [
                    "youtube abcdefghijk",
                    "rutube rutube-id",
                    "# comment",
                ],
            )

    def test_apply_creates_backup_and_rewrites_archive(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "Present [abcdefghijk].mkv").write_bytes(b"video")
            archive = root / "yt-dlp-archive.txt"
            original = "youtube abcdefghijk\nyoutube missing1234\n"
            archive.write_text(original, encoding="utf-8")
            plan = build_sync_plan(root, archive)

            backup = apply_sync_plan(archive, plan)

            self.assertIsNotNone(backup)
            self.assertEqual(backup.read_text(encoding="utf-8"), original)
            self.assertEqual(
                archive.read_text(encoding="utf-8"),
                "youtube abcdefghijk\n",
            )

    def test_dry_run_does_not_modify_archive(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            archive = root / "yt-dlp-archive.txt"
            original = "youtube missing1234\n"
            archive.write_text(original, encoding="utf-8")

            result = synchronize_archive(
                root,
                archive,
                apply=False,
                assume_yes=False,
            )

            self.assertEqual(result, 0)
            self.assertEqual(archive.read_text(encoding="utf-8"), original)
            self.assertEqual(list(root.glob("*.backup-*")), [])

    def test_cancel_does_not_modify_archive(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            archive = root / "yt-dlp-archive.txt"
            original = "youtube missing1234\n"
            archive.write_text(original, encoding="utf-8")

            result = synchronize_archive(
                root,
                archive,
                apply=True,
                assume_yes=False,
                input_fn=lambda _: "no",
            )

            self.assertEqual(result, 0)
            self.assertEqual(archive.read_text(encoding="utf-8"), original)


if __name__ == "__main__":
    unittest.main()
