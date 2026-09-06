import importlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import video_tools

video_doctor = importlib.import_module("video_doctor")
from video_doctor import (
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
                "video_doctor.check_command",
                side_effect=lambda name, command, version_args: (
                    video_doctor.CheckResult(name, "ok", command)
                ),
            ):
                results = run_doctor(root, config)

            self.assertEqual(doctor_exit_code(results), 0)
            self.assertFalse(any(result.status == "error" for result in results))

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

    def test_missing_command_is_reported(self):
        with patch("video_doctor.shutil.which", return_value=None):
            result = check_command("tool", "missing-tool", ["--version"])
        self.assertEqual(result.status, "error")

    def test_root_write_probe_leaves_no_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            result = check_root(root)
            self.assertIn(result.status, {"ok", "warning"})
            self.assertEqual(list(root.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
