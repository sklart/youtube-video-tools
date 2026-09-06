import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from youtube_video_tools import cli as video_tools
from youtube_video_tools import config as video_config


class EnvironmentOverrideTests(unittest.TestCase):
    def test_configured_path_prefers_environment(self):
        config = {"paths": {"cookies": "from-config.txt"}}
        with patch.dict(os.environ, {"YOUTUBE_COOKIES_FILE": "from-env.txt"}):
            path = video_config.configured_path(config, "cookies")
        self.assertEqual(path, Path("from-env.txt"))

    def test_configured_command_prefers_environment(self):
        config = {"paths": {"yt_dlp": "yt-dlp-config"}}
        with patch.dict(os.environ, {"VIDEO_TOOLS_YT_DLP": "yt-dlp-env"}):
            command = video_config.configured_command(
                config,
                "yt_dlp",
                "yt-dlp",
            )
        self.assertEqual(command, "yt-dlp-env")

    def test_parser_uses_environment_defaults(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "archive"
            config = Path(temp_dir) / "custom.toml"
            with patch.dict(
                os.environ,
                {
                    "VIDEO_TOOLS_ROOT": str(root),
                    "VIDEO_TOOLS_CONFIG": str(config),
                },
                clear=False,
            ):
                args = video_tools.build_parser().parse_args(["doctor"])

        self.assertEqual(args.root, root)
        self.assertEqual(args.config, config)

    def test_menu_uses_environment_defaults(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "archive"
            config = Path(temp_dir) / "custom.toml"
            answers = iter(["1", "", "0"])
            with (
                patch.dict(
                    os.environ,
                    {
                        "VIDEO_TOOLS_ROOT": str(root),
                        "VIDEO_TOOLS_CONFIG": str(config),
                    },
                    clear=False,
                ),
                patch("youtube_video_tools.cli.execute_command", return_value=0) as execute,
            ):
                result = video_tools.main([], input_fn=lambda _: next(answers))

        self.assertEqual(result, 0)
        execute.assert_called_once_with(
            "doctor",
            [],
            root=root.resolve(),
            config_path=config.resolve(),
        )


if __name__ == "__main__":
    unittest.main()
