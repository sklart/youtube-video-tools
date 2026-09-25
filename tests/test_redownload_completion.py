import tempfile
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from tests.support import IsolatedTestCase
from tests.test_progress_renderer import Tty, download_line, post_line
from youtube_video_tools.bookmarks.redownload import redownload_video
from youtube_video_tools.bookmarks.rewriter import FFprobeClient
from youtube_video_tools.console import Console, use_console
from youtube_video_tools.services.process import ProcessResult
from youtube_video_tools.services.yt_dlp import YtDlpClient


class RedownloadCompletionTests(IsolatedTestCase):
    def test_success_only_after_validation_and_replace(self):
        self.check_modes("success")

    def test_validation_failure_preserves_original_without_success(self):
        self.check_modes("validation")

    def test_replace_failure_preserves_original_without_success(self):
        self.check_modes("replace")

    def check_modes(self, outcome):
        for tty in (True, False):
            with self.subTest(tty=tty):
                self.check_lifecycle(tty, outcome)

    def check_lifecycle(self, tty, outcome):
        output = Tty() if tty else StringIO()
        console = Console()
        real_replace = Path.replace
        phases = []
        with tempfile.TemporaryDirectory() as directory:
            video = Path(directory) / "video.mp4"
            video.write_bytes(b"original")

            def stream(client, arguments, **kwargs):
                temporary = Path(arguments[arguments.index("-o") + 1])
                temporary.write_bytes(b"replacement")
                for line in (
                    'VT_START:{"id":"abc","title":"Test"}',
                    download_line(50),
                    download_line(100, status="finished"),
                    download_line(50, info={"vcodec": "none", "acodec": "aac"}),
                    download_line(100, status="finished", info={"vcodec": "none", "acodec": "aac"}),
                    post_line("Merger"),
                    'VT_COMPLETE:{"id":"abc","title":"Test"}',
                ):
                    kwargs["on_line"](line)
                self.assertNotIn("✓", output.getvalue())
                self.assertEqual(console._live_width, 0)
                phases.append("complete")
                return ProcessResult(("yt-dlp",), 0, "", "")

            def probe(client, path):
                self.assertEqual(path.read_bytes(), b"replacement")
                self.assertNotIn("✓", output.getvalue())
                self.assertIn("Проверка нового файла", output.getvalue())
                phases.append("validation")
                return outcome != "validation"

            def replace(source, target):
                self.assertEqual(phases, ["complete", "validation"])
                self.assertNotIn("✓", output.getvalue())
                self.assertEqual(target, video)
                phases.append("replace")
                if outcome == "replace":
                    raise OSError("replacement denied")
                return real_replace(source, target)

            with (
                patch.dict("os.environ", {"CI": "", "TERM": "xterm"}),
                redirect_stdout(output),
                use_console(console),
                patch.object(YtDlpClient, "stream", stream),
                patch.object(FFprobeClient, "has_video_stream", probe),
                patch.object(Path, "replace", replace),
            ):
                if outcome == "replace":
                    with self.assertRaisesRegex(OSError, "replacement denied"):
                        redownload_video(video, "abc", yt_dlp="yt-dlp", cookies=None, timeout=30)
                else:
                    success, error = redownload_video(
                        video, "abc", yt_dlp="yt-dlp", cookies=None, timeout=30
                    )
                    self.assertEqual(success, outcome == "success")
                    if not success:
                        self.assertIn("ffprobe", error)
                        # Error reporting belongs to the caller, not the helper.
                        console.error(error)

            text = output.getvalue()
            self.assertNotIn("Файл скачан", text)
            self.assertEqual(list(video.parent.iterdir()), [video])
            if outcome == "success":
                self.assertEqual(phases, ["complete", "validation", "replace"])
                self.assertEqual(video.read_bytes(), b"replacement")
                self.assertEqual(text.count("✓"), 1)
                self.assertIn("✓ Видео успешно заменено", text)
                self.assertLess(text.index("Проверка нового файла"), text.index("✓"))
                if tty:
                    self.assertNotIn("[OK]", text)
                    self.assertNotIn("[INFO]", text)
                else:
                    self.assertIn("[INFO] ↳ Проверка нового файла\n", text)
                    self.assertIn("[OK] ✓ Видео успешно заменено\n", text)
            else:
                self.assertEqual(video.read_bytes(), b"original")
                self.assertNotIn("✓", text)
                self.assertNotIn("[OK]", text)
                if outcome == "validation":
                    self.assertEqual(phases, ["complete", "validation"])
                    self.assertIn("[ERROR]", text)
            if not tty:
                self.assertNotIn("\r", text)
                self.assertNotIn("\x1b", text)
