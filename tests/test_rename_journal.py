import importlib
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import video_tools

rename_files = importlib.import_module("rename_files")
read_journal = importlib.import_module("video_journal").read_journal


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
                    rename_files.subprocess,
                    "run",
                    return_value=subprocess.CompletedProcess(
                        args=[],
                        returncode=0,
                        stdout="20260611\n",
                        stderr="",
                    ),
                ),
            ):
                result = rename_files.main()

            destination = root / "Title_11.06.2026 [abcdefghijk].mp4"
            self.assertEqual(result, 0)
            self.assertFalse(source.exists())
            self.assertTrue(destination.exists())

            events = read_journal(root)
            rename_event = next(
                event for event in events if event.get("event") == "rename"
            )
            self.assertEqual(rename_event["source"], source.name)
            self.assertEqual(rename_event["destination"], destination.name)
            self.assertEqual(rename_event["result"], "success")


if __name__ == "__main__":
    unittest.main()
