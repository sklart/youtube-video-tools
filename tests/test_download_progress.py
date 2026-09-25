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
            for line in (
                "[download] 28.5% ETA 00:22\r",
                "[download] 63.4% ETA 00:09\n",
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
        self.assertNotIn("\r", text)
        self.assertNotIn("\x1b", text)
        if quiet:
            self.assertNotIn("[PROGRESS]", text)
            self.assertNotIn("[INFO]", text)
        else:
            prefix = "REDOWNLOAD: " if command == "redownload" else ""
            self.assertIn(
                f"[PROGRESS] {prefix}[download] 28.5% ETA 00:22\n"
                f"[PROGRESS] {prefix}[download] 63.4% ETA 00:09\n"
                "[ERROR] ERROR: connection lost\n",
                text,
            )
