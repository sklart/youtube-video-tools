import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from youtube_video_tools import config


class IsolatedTestCase(unittest.TestCase):
    def setUp(self):
        super().setUp()
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.test_config = Path(directory.name) / "config.toml"
        self.test_config.write_text("", encoding="utf-8")
        environment = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith("VIDEO_TOOLS_") and key != "YOUTUBE_COOKIES_FILE"
        }
        environment[config.CONFIG_ENV_NAME] = str(self.test_config)
        self.enterContext(patch.dict(os.environ, environment, clear=True))
        self.enterContext(patch.object(config, "DEFAULT_CONFIG_PATH", self.test_config))
