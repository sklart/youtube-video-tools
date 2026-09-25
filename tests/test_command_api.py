import os
import sys
import tempfile
from pathlib import Path

from tests.support import IsolatedTestCase
from youtube_video_tools.api import AppContext
from youtube_video_tools.commands import dates
from youtube_video_tools.console import Console


class CommandApiTests(IsolatedTestCase):
    def test_dates_run_does_not_mutate_process_context(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "missing-date.mp4").touch()
            argv, environment = list(sys.argv), dict(os.environ)
            result = dates.run(dates.Options(), AppContext(root=root, console=Console(quiet=True)))
            self.assertFalse(result.success)
            self.assertEqual(result.exit_code, 1)
            self.assertFalse(result.changed)
            self.assertTrue(result.warnings)
            self.assertEqual(sys.argv, argv)
            self.assertEqual(dict(os.environ), environment)
