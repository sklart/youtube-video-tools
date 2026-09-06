import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import video_tools


class MetadataCacheTests(unittest.TestCase):
    def test_rename_uses_cached_upload_date(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            cache = video_tools.video_metadata_cache.load_cache(root)
            video_tools.video_metadata_cache.set_field(
                cache,
                "youtube",
                "abcdefghijk",
                "upload_date",
                "11.06.2026",
            )

            with patch.object(
                video_tools.rename_files.subprocess,
                "run",
                side_effect=AssertionError("subprocess.run should not be called"),
            ):
                date_text, error = video_tools.rename_files.get_upload_date(
                    "abcdefghijk",
                    yt_dlp="yt-dlp",
                    cookies=None,
                    timeout=1,
                    metadata_cache=cache,
                )

            self.assertEqual((date_text, error), ("11.06.2026", None))

    def test_duplicates_uses_cached_title(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            cache = video_tools.video_metadata_cache.load_cache(root)
            video_tools.video_metadata_cache.set_field(
                cache,
                "youtube",
                "abcdefghijk",
                "title",
                "Cached Title",
            )

            with patch.object(
                video_tools.find_duplicates.subprocess,
                "run",
                side_effect=AssertionError("subprocess.run should not be called"),
            ):
                title = video_tools.find_duplicates.get_youtube_title(
                    "abcdefghijk",
                    cookies_file=None,
                    yt_dlp="yt-dlp",
                    metadata_cache=cache,
                )

            self.assertEqual(title, "Cached Title")

    def test_resort_uses_cached_uploader(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            cache = video_tools.video_metadata_cache.load_cache(root)
            video_tools.video_metadata_cache.set_field(
                cache,
                "youtube",
                "abcdefghijk",
                "uploader",
                "Cached Channel",
            )

            with patch.object(
                video_tools.resort.subprocess,
                "run",
                side_effect=AssertionError("subprocess.run should not be called"),
            ):
                uploader = video_tools.resort.get_uploader(
                    "abcdefghijk",
                    "youtube",
                    yt_dlp="yt-dlp",
                    cookies=None,
                    timeout=1,
                    max_retries=1,
                    pause=0,
                    cache={},
                    metadata_cache=cache,
                    root=root,
                    write_log=False,
                )

            self.assertEqual(uploader, "Cached Channel")

    def test_cache_round_trip_to_json_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            cache = video_tools.video_metadata_cache.load_cache(root)
            video_tools.video_metadata_cache.set_field(
                cache,
                "youtube",
                "abcdefghijk",
                "title",
                "Saved Title",
            )

            video_tools.video_metadata_cache.save_cache(root, cache)
            reloaded = video_tools.video_metadata_cache.load_cache(root)

            self.assertEqual(
                video_tools.video_metadata_cache.get_field(
                    reloaded,
                    "youtube",
                    "abcdefghijk",
                    "title",
                ),
                "Saved Title",
            )


if __name__ == "__main__":
    unittest.main()
