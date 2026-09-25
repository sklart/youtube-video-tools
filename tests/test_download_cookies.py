import tempfile
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from tests.support import IsolatedTestCase
from youtube_video_tools.commands import download
from youtube_video_tools.console import Console, use_console
from youtube_video_tools.services.process import ProcessResult


class DownloadCookiesTests(IsolatedTestCase):
    def test_cookies_diagnostics(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cookies = root / "cookies.txt"
            cookies.write_text("private-cookie-content", encoding="utf-8")
            for case, path in (
                ("unset", None),
                ("missing", root / "missing.txt"),
                ("directory", root),
                ("unavailable", cookies),
                ("success", cookies),
            ):
                for quiet in (False, True):
                    output = StringIO()

                    def stream(client, arguments, **kwargs):
                        self.assertEqual(client.cookies_file, cookies)
                        if case == "unavailable":
                            for _ in range(2):
                                kwargs["on_line"](
                                    "ERROR: [youtube:tab] WL: YouTube said: The playlist does not exist."
                                )
                        return ProcessResult(("yt-dlp",), int(case == "unavailable"), "", "")

                    with (
                        self.subTest(case=case, quiet=quiet),
                        redirect_stdout(output),
                        use_console(Console(quiet=quiet)),
                        patch("sys.argv", ["download", "--root", str(root)]),
                        patch.object(download, "load_config", return_value={}),
                        patch.object(download, "configured_path", return_value=path),
                        patch.object(download, "synchronize_archive", return_value=0) as sync,
                        patch.object(
                            download.YtDlpClient, "stream", autospec=True, side_effect=stream
                        ) as runner,
                    ):
                        result = download.main()
                        text = output.getvalue()
                        self.assertNotIn("private-cookie-content", text)
                        if case in {"unset", "missing", "directory"}:
                            self.assertEqual(result, 2)
                            self.assertIn("[ERROR] Cookies-файл не найден", text)
                            self.assertIn("YOUTUBE_COOKIES_FILE", text)
                            sync.assert_not_called()
                            runner.assert_not_called()
                        else:
                            self.assertEqual(result, int(case == "unavailable"))
                            self.assertNotIn("Cookies-файл не найден", text)
                            if case == "unavailable":
                                self.assertEqual(text.count("обновите cookies"), 1)
                                self.assertIn("[WARN]", text)
                                self.assertIn(str(cookies), text)
                            else:
                                self.assertNotIn("[WARN]", text)
                            if not quiet:
                                self.assertIn(f"[INFO] Cookies-файл: {cookies}", text)
                        if quiet:
                            self.assertNotIn("[INFO]", text)
