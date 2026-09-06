import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from youtube_video_tools import cli
from youtube_video_tools.commands import resolution
from youtube_video_tools.console import get_console
from youtube_video_tools.services.process import ProcessResult


class QuietFailureTests(unittest.TestCase):
    def test_dates_quiet_shows_warning_for_missing_date(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "Missing [abcdefghijk].mp4").write_bytes(b"video")
            output = StringIO()
            with redirect_stdout(output):
                result = cli.execute_command(
                    "dates", [], root=root, config_path=root / "config.toml", quiet=True
                )
            self.assertEqual(result, 1)
            self.assertIn("[WARN]", output.getvalue())
            self.assertNotIn("Нет даты:", output.getvalue())

    def test_resolution_quiet_shows_warning_for_ffprobe_failure(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            video = root / "Video [abcdefghijk].mp4"
            video.write_bytes(b"video")
            output = StringIO()
            with (
                patch.object(
                    resolution.FFprobeClient,
                    "run",
                    return_value=ProcessResult(("ffprobe",), 1, "", "bad file"),
                ),
                redirect_stdout(output),
            ):
                result = cli.execute_command(
                    "resolution", [], root=root, config_path=root / "config.toml", quiet=True
                )
            self.assertEqual(result, 1)
            self.assertIn("[WARN]", output.getvalue())
            self.assertIn(video.name, output.getvalue())
            self.assertNotIn("Проверка видеофайлов", output.getvalue())

    def test_failure_commands_have_quiet_explanation(self):
        for command in ("rename", "subtitles"):
            with self.subTest(command=command):

                def fake_main() -> int:
                    get_console().warning("объяснение проблемы")
                    return 1

                fake_module = type("FakeModule", (), {"main": staticmethod(fake_main)})
                output = StringIO()
                with (
                    patch.dict(cli.COMMAND_MODULES, {command: fake_module}),
                    redirect_stdout(output),
                ):
                    result = cli.execute_command(
                        command,
                        [],
                        root=Path("archive"),
                        config_path=Path("config.toml"),
                        quiet=True,
                    )
                self.assertEqual(result, 1)
                self.assertIn("объяснение проблемы", output.getvalue())


if __name__ == "__main__":
    unittest.main()
