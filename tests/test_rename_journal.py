import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from youtube_video_tools.commands import rename as rename_files
from youtube_video_tools.journal import read_journal
from youtube_video_tools.services.process import ProcessResult


class RenameJournalTests(unittest.TestCase):
    def test_apply_writes_successful_rename_to_journal(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "Title [abcdefghijk].mp4"
            source.write_bytes(b"video")
            with (
                patch.object(
                    sys,
                    "argv",
                    [
                        "rename_files.py",
                        "--root",
                        str(root),
                        "--apply",
                    ],
                ),
                patch.object(
                    rename_files.YtDlpClient,
                    "run",
                    return_value=ProcessResult((), 0, "20260611\n", ""),
                ),
            ):
                result = rename_files.main()

            destination = root / "Title_11.06.2026 [abcdefghijk].mp4"
            self.assertEqual(result, 0)
            self.assertFalse(source.exists())
            self.assertTrue(destination.exists())

            events = read_journal(root)
            rename_event = next(event for event in events if event.get("event") == "rename")
            self.assertEqual(rename_event["source"], source.name)
            self.assertEqual(rename_event["destination"], destination.name)
            self.assertEqual(rename_event["result"], "success")


if __name__ == "__main__":
    unittest.main()
