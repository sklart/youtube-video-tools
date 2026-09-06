import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from youtube_video_tools.commands import download as download_yt_favorites


class DownloadMetadataTests(unittest.TestCase):
    def test_download_does_not_create_info_json_files(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            output = []
            cookies = root / "cookies.txt"
            cookies.write_text("cookies", encoding="utf-8")
            previous_argv = sys.argv
            previous_cookies = os.environ.get("YOUTUBE_COOKIES_FILE")
            previous_ytdlp = os.environ.get("VIDEO_TOOLS_YT_DLP")

            class FakeProcess:
                def __init__(self, cmd):
                    self.cmd = cmd
                    self.stdout = iter([])

                def __enter__(self):
                    return self

                def __exit__(self, exc_type, exc, tb):
                    return False

                def wait(self):
                    return 0

            def fake_popen(cmd, **kwargs):
                output.append(cmd)
                return FakeProcess(cmd)

            try:
                os.environ["YOUTUBE_COOKIES_FILE"] = str(cookies)
                os.environ["VIDEO_TOOLS_YT_DLP"] = "yt-dlp"
                sys.argv = ["download_yt_favorites.py", "--root", str(root)]
                with patch.object(
                    download_yt_favorites.subprocess, "Popen", side_effect=fake_popen
                ):
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
        self.assertNotIn("infojson", " ".join(cmd))


if __name__ == "__main__":
    unittest.main()
