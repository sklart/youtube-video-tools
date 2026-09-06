import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from youtube_video_tools.commands import inventory, subtitles


class SubtitleSupportTests(unittest.TestCase):
    def test_inventory_counts_ass_subtitles(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            channel = root / "Channel"
            channel.mkdir()
            video = channel / "Video_11.06.2026 [abcdefghijk].mkv"
            subtitle = channel / "Video_11.06.2026 [abcdefghijk].ru-en-US.ass"
            video.write_bytes(b"video")
            subtitle.write_text("subtitle", encoding="utf-8")

            records = inventory.collect_inventory(root)

            self.assertEqual(len(records), 1)
            self.assertEqual(records[0].subtitle_count, 1)
            self.assertEqual(records[0].subtitles, subtitle.name)

    def test_subtitle_exists_supports_ass_language_variant(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            video = root / "Video [abcdefghijk].webm"
            subtitle = root / "Video [abcdefghijk].ru-en-US.ass"
            video.write_bytes(b"video")
            subtitle.write_text("subtitle", encoding="utf-8")

            self.assertTrue(subtitles.subtitle_exists(video, "ru-en-US"))

    def test_get_subtitles_scans_non_mp4_video_extensions(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            folder = root / "Channel"
            folder.mkdir()
            video = folder / "Video [abcdefghijk].mkv"
            cookies = root / "cookies.txt"
            video.write_bytes(b"video")
            cookies.write_text("cookie", encoding="utf-8")

            captured = []
            previous_argv = sys.argv
            try:
                sys.argv = [
                    "get_subtitles.py",
                    "--root",
                    str(root),
                    "--folder",
                    "Channel",
                    "--lang",
                    "ru-en-US",
                    "--cookies",
                    str(cookies),
                ]
                with (
                    patch(
                        "subprocess.run",
                        side_effect=lambda cmd, **kwargs: captured.append(cmd[-1]),
                    ),
                    patch(
                        "time.sleep",
                        return_value=None,
                    ),
                ):
                    result = subtitles.main()
            finally:
                sys.argv = previous_argv

            self.assertEqual(result, 0)
            self.assertEqual(captured, ["https://www.youtube.com/watch?v=abcdefghijk"])


if __name__ == "__main__":
    unittest.main()
