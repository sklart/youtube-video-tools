import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from youtube_video_tools import cli
from youtube_video_tools.commands import doctor as video_doctor
from youtube_video_tools.commands.doctor import (
    check_command,
    check_root,
    doctor_exit_code,
    load_and_check_config,
    run_doctor,
)

VALID_CONFIG = """
[paths]
cookies = "{cookies}"
yt_dlp = "yt-dlp"
ffprobe = "ffprobe"

[subtitles]
folders = ["Channel"]
languages = ["ru"]

[sorting]
max_retries = 3
"""


class VideoDoctorTests(unittest.TestCase):
    def test_valid_environment_without_real_commands(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            cookies = root / "cookies.txt"
            cookies.write_text("cookies", encoding="utf-8")
            config = root / "config.toml"
            config.write_text(
                VALID_CONFIG.format(cookies=str(cookies).replace("\\", "\\\\")),
                encoding="utf-8",
            )

            with patch(
                "youtube_video_tools.commands.doctor.check_command",
                side_effect=lambda name, command, version_args: video_doctor.CheckResult(
                    name, "ok", command
                ),
            ):
                results = run_doctor(root, config)

            self.assertEqual(doctor_exit_code(results), 0)
            self.assertFalse(any(result.status == "error" for result in results))
            self.assertEqual([result.name for result in results].count("ffmpeg"), 1)

    def test_invalid_toml_is_reported(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config = Path(temp_dir) / "config.toml"
            config.write_text("[paths\n", encoding="utf-8")

            loaded, result = load_and_check_config(config)

            self.assertEqual(loaded, {})
            self.assertEqual(result.status, "error")

    def test_invalid_numeric_setting_is_reported(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config = root / "config.toml"
            config.write_text(
                """
[paths]
yt_dlp = "yt-dlp"
ffprobe = "ffprobe"
[sorting]
max_retries = "many"
""",
                encoding="utf-8",
            )

            _, result = load_and_check_config(config)

            self.assertEqual(result.status, "error")
            self.assertIn("целым числом", result.message)

    def test_invalid_section_types_never_crash_doctor(self):
        for section, value in (
            ("paths", "[]"),
            ("paths", '"broken"'),
            ("subtitles", "[]"),
            ("sorting", '"broken"'),
            ("download", "[]"),
            ("bookmarks", '"broken"'),
        ):
            with (
                self.subTest(section=section, value=value),
                tempfile.TemporaryDirectory() as temp_dir,
            ):
                root = Path(temp_dir)
                config = root / "config.toml"
                config.write_text(f"{section} = {value}\n", encoding="utf-8")
                with patch.object(video_doctor, "check_command"):
                    results = run_doctor(root, config)
                self.assertEqual(results[1].status, "error")
                self.assertEqual(doctor_exit_code(results), 1)

    def test_invalid_bookmarks_timeout_is_reported(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config = Path(temp_dir) / "config.toml"
            config.write_text("[bookmarks]\nffmpeg_timeout_seconds = false\n", encoding="utf-8")
            _, result = load_and_check_config(config)
            self.assertEqual(result.status, "error")
            self.assertIn("ffmpeg_timeout_seconds", result.message)

    def test_broken_cache_is_reported(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            cache = root / ".video-tools" / "yt-dlp-cache.json"
            cache.parent.mkdir()
            cache.write_text("{broken", encoding="utf-8")
            result = video_doctor.check_cache(root)
            self.assertEqual(result.status, "error")

    def test_missing_command_is_reported(self):
        with patch("youtube_video_tools.commands.doctor.shutil.which", return_value=None):
            result = check_command("tool", "missing-tool", ["--version"])
        self.assertEqual(result.status, "error")

    def test_root_write_probe_leaves_no_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            result = check_root(root)
            self.assertIn(result.status, {"ok", "warning"})
            self.assertEqual(list(root.iterdir()), [])

    def test_quiet_keeps_warning_from_doctor(self):
        warning = video_doctor.CheckResult("Конфигурация", "warning", "не создан")
        output = StringIO()
        with (
            patch.object(video_doctor, "run_doctor", return_value=[warning]),
            redirect_stdout(output),
        ):
            result = cli.execute_command(
                "doctor",
                [],
                root=Path("archive"),
                config_path=Path("config.toml"),
                quiet=True,
            )
        self.assertEqual(result, 0)
        self.assertIn("[WARN] Конфигурация: не создан", output.getvalue())


if __name__ == "__main__":
    unittest.main()
