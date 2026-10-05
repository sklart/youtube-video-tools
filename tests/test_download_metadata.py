import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.support import IsolatedTestCase
from youtube_video_tools.commands import download as download_yt_favorites
from youtube_video_tools.services.process import ProcessResult


class DownloadMetadataTests(IsolatedTestCase):
    def test_download_does_not_create_info_json_files(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir).resolve()
            output = []
            cookies = root / "cookies.txt"
            cookies.write_text("cookies", encoding="utf-8")
            previous_argv = sys.argv
            previous_cookies = os.environ.get("YOUTUBE_COOKIES_FILE")
            previous_ytdlp = os.environ.get("VIDEO_TOOLS_YT_DLP")

            def fake_stream(self, arguments, **kwargs):
                output.append([self.executable, *arguments])
                return ProcessResult(tuple(output[-1]), 0, "", "")

            try:
                os.environ["YOUTUBE_COOKIES_FILE"] = str(cookies)
                os.environ["VIDEO_TOOLS_YT_DLP"] = "yt-dlp"
                sys.argv = ["download_yt_favorites.py", "--root", str(root)]
                with patch.object(download_yt_favorites.YtDlpClient, "stream", fake_stream):
                    result = download_yt_favorites.main()
            finally:
                sys.argv = previous_argv
                if previous_cookies is None:
                    os.environ.pop("YOUTUBE_COOKIES_FILE", None)
                else:
                    os.environ["YOUTUBE_COOKIES_FILE"] = previous_cookies
                if previous_ytdlp is None:
                    os.environ.pop("VIDEO_TOOLS_YT_DLP", None)
                else:
                    os.environ["VIDEO_TOOLS_YT_DLP"] = previous_ytdlp

        self.assertEqual(result, 0)
        self.assertEqual(len(output), 1)
        cmd = output[0]
        self.assertNotIn("--write-info-json", cmd)
        metadata_dir = (
            download_yt_favorites.video_config.state_directory(root) / "download-metadata"
        )
        self.assertIn(f"infojson:{metadata_dir / 'videos' / '%(id)s.%(ext)s'}", cmd)
        self.assertIn(f"pl_infojson:{metadata_dir / 'playlists' / '%(id)s.%(ext)s'}", cmd)

        if importlib.util.find_spec("yt_dlp"):
            from yt_dlp import YoutubeDL, parse_options

            # Simulate a user yt-dlp config enabling JSON without downloading any media.
            templates = []
            for index, argument in enumerate(cmd):
                if argument == "-o":
                    templates.extend([argument, cmd[index + 1]])
            options = parse_options(["--ignore-config", "--write-info-json", *templates])
            with YoutubeDL(options.ydl_opts) as downloader:
                info = {"id": "WL", "title": "Watch later", "uploader": "Test user"}
                playlist_path = Path(downloader.prepare_filename(info, "pl_infojson"))
                self.assertEqual(playlist_path, metadata_dir / "playlists" / "WL.info.json")
                video_path = Path(downloader.prepare_filename({**info, "id": "test"}, "infojson"))
                self.assertEqual(video_path, metadata_dir / "videos" / "test.info.json")


if __name__ == "__main__":
    unittest.main()
