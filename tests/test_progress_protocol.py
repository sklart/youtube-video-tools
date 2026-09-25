"""Optional offline contract tests with the real external yt-dlp formatter."""

import importlib.util
import unittest

from tests.support import IsolatedTestCase
from youtube_video_tools.console import Console
from youtube_video_tools.progress import (
    COMPLETE_TEMPLATE,
    DOWNLOAD_TEMPLATE,
    POSTPROCESS_TEMPLATE,
    START_TEMPLATE,
    DownloadProgressRenderer,
    parse_event,
)


@unittest.skipUnless(importlib.util.find_spec("yt_dlp"), "optional yt-dlp contract test")
class ProgressProtocolTests(IsolatedTestCase):
    def test_real_ytdlp_json_templates_handle_missing_values_and_escaping(self):
        from yt_dlp import YoutubeDL

        title = '中文 | "Русский" \\ путь\nстрока'
        info = {"id": "test", "title": title}
        with YoutubeDL({"quiet": True}) as downloader:
            for template in (DOWNLOAD_TEMPLATE, POSTPROCESS_TEMPLATE):
                line = downloader.evaluate_outtmpl(
                    template.split(":", 1)[1], {"info": info, "progress": {"status": "finished"}}
                )
                self.assertNotIn("\n", line)
                event = parse_event(line)
                self.assertEqual(event.video_id, "test")
                self.assertEqual(event.title, title.replace("\n", " "))
            for template in (START_TEMPLATE, COMPLETE_TEMPLATE):
                line = downloader.evaluate_outtmpl(template.split(":", 1)[1], info)
                self.assertEqual(parse_event(line).video_id, "test")

    def test_print_does_not_enable_simulation_or_disable_progress(self):
        from yt_dlp import parse_options

        arguments = DownloadProgressRenderer(Console()).arguments()
        options = parse_options(["--ignore-config", *arguments, "https://example.com/test.mp4"])
        self.assertIs(options.ydl_opts["simulate"], False)
        self.assertIs(options.ydl_opts["quiet"], False)
        self.assertFalse(options.ydl_opts.get("noprogress"))
        self.assertFalse(options.ydl_opts.get("writeinfojson"))
        self.assertIn("after_move", options.ydl_opts["forceprint"])
        self.assertIn("before_dl", options.ydl_opts["forceprint"])
