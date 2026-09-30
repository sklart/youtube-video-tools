"""Optional offline contract tests with the real external yt-dlp formatter."""

import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.support import IsolatedTestCase
from youtube_video_tools.console import Console
from youtube_video_tools.download_recovery import RecoveryQueue
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
    def test_real_modify_chapters_error_has_exact_recovery_identity(self):
        from yt_dlp import YoutubeDL
        from yt_dlp.postprocessor.modify_chapters import ModifyChaptersPP
        from yt_dlp.utils import PostProcessingError

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            video = root / "test [abcdefghijk].mp4"
            video.write_bytes(b"fake video; never probed")
            queue = RecoveryQueue(root)
            with YoutubeDL(
                {"progress_template": {"postprocess": POSTPROCESS_TEMPLATE.split(":", 1)[1]}}
            ) as downloader:
                processor = ModifyChaptersPP(downloader, remove_sponsor_segments={"sponsor"})
                with (
                    patch.object(
                        downloader, "to_screen", side_effect=lambda text, **kw: queue.on_line(text)
                    ),
                    patch.object(processor, "_get_real_video_duration", return_value=5),
                ):
                    try:
                        processor.run(
                            {
                                "id": "abcdefghijk",
                                "title": "test",
                                "filepath": str(video),
                                "duration": 10,
                                "ext": "mp4",
                                "sponsorblock_chapters": [
                                    {
                                        "start_time": 0,
                                        "end_time": 1,
                                        "category": "sponsor",
                                        "name": "Sponsor",
                                        "categories": ["sponsor"],
                                    }
                                ],
                            }
                        )
                    except PostProcessingError as error:
                        queue.on_line(f"ERROR: Postprocessing: {error}")
                    else:
                        self.fail("Expected a duration mismatch without real ffprobe or media")
            self.assertEqual(list(queue.pending), [video])

    def test_real_ytdlp_json_templates_handle_missing_values_and_escaping(self):
        from yt_dlp import YoutubeDL

        title = '中文 | "Русский" \\ путь\nстрока'
        info = {"id": "test", "title": title, "filepath": "C:\\Видео\\title [test].mp4"}
        with YoutubeDL({"quiet": True}) as downloader:
            for template in (DOWNLOAD_TEMPLATE, POSTPROCESS_TEMPLATE):
                line = downloader.evaluate_outtmpl(
                    template.split(":", 1)[1], {"info": info, "progress": {"status": "finished"}}
                )
                self.assertNotIn("\n", line)
                event = parse_event(line)
                self.assertEqual(event.video_id, "test")
                self.assertEqual(event.title, title.replace("\n", " "))
                if template == POSTPROCESS_TEMPLATE:
                    self.assertEqual(event.filepath, info["filepath"])
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
