import json
import tempfile
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from tests.support import IsolatedTestCase
from youtube_video_tools.commands import bookmarks, download
from youtube_video_tools.console import Console, use_console
from youtube_video_tools.services.process import ProcessResult


class DownloadProgressTests(IsolatedTestCase):
    def test_streamed_progress_keeps_line_boundaries(self):
        for command in ("download", "redownload"):
            for quiet in (False, True):
                with self.subTest(command=command, quiet=quiet):
                    self.check_output(command, quiet)

    def check_output(self, command, quiet):
        def fake_stream(client, arguments, **kwargs):
            self.assertEqual(arguments.count("--progress-template"), 2)
            self.assertIn("--progress-delta", arguments)
            self.assertIn("--no-simulate", arguments)
            self.assertNotIn("--write-info-json", arguments)
            for line in (
                "VT_PROGRESS:"
                + json.dumps(
                    {
                        "info": {"id": "test", "title": "Title"},
                        "progress": {
                            "status": "downloading",
                            "downloaded_bytes": 285,
                            "total_bytes": 1000,
                        },
                    }
                ),
                "VT_PROGRESS:"
                + json.dumps(
                    {
                        "info": {"id": "test", "title": "Title"},
                        "progress": {
                            "status": "downloading",
                            "downloaded_bytes": 634,
                            "total_bytes": 1000,
                        },
                    }
                ),
                "WARNING: retrying\n",
                "ERROR: connection lost\n",
            ):
                kwargs["on_line"](line)
            return ProcessResult(("yt-dlp",), 1, "", "")

        output = StringIO()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cookies = root / "cookies.txt"
            cookies.touch()
            with (
                redirect_stdout(output),
                use_console(Console(quiet=quiet)),
                patch.object(download.YtDlpClient, "stream", fake_stream),
            ):
                if command == "download":
                    with (
                        patch.object(download, "load_config", return_value={}),
                        patch.object(download, "configured_path", return_value=cookies),
                        patch.object(download, "synchronize_archive", return_value=0),
                        patch("sys.argv", ["video-tools", "--root", str(root)]),
                    ):
                        self.assertEqual(download.main(), 1)
                else:
                    success, error = bookmarks.redownload_video(
                        root / "test.mp4", "test-id", yt_dlp="yt-dlp", cookies=None, timeout=30
                    )
                    self.assertFalse(success)
                    self.assertIsNotNone(error)

        text = output.getvalue()
        self.assertIn("[ERROR] ERROR: connection lost\n", text)
        self.assertIn("[WARN] WARNING: retrying\n", text)
        self.assertNotIn("\r", text)
        self.assertNotIn("\x1b", text)
        if quiet:
            self.assertNotIn("[PROGRESS]", text)
            self.assertNotIn("[INFO]", text)
        else:
            progress = [line for line in text.splitlines() if line.startswith("[PROGRESS]")]
            self.assertEqual(len(progress), 2)
            self.assertIn("28.5%", progress[0])
            self.assertIn("63.4%", progress[1])
            self.assertIn("Title", progress[1])
            if command == "redownload":
                self.assertIn("Перекачивание", progress[1])
