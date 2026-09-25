import json
from contextlib import redirect_stdout
from io import StringIO
from unittest.mock import PropertyMock, patch

from tests.support import IsolatedTestCase
from youtube_video_tools.console import Console, display_width, use_console
from youtube_video_tools.progress import DownloadProgressRenderer, parse_event


def download_line(percent=10, *, info=None, **progress):
    return "VT_PROGRESS:" + json.dumps(
        {
            "info": {
                "id": "abc",
                "title": "Тестовый ролик",
                "playlist_index": 3,
                "playlist_count": 8,
                "format_id": "137",
                "vcodec": "h264",
                "acodec": "none",
                **(info or {}),
            },
            "progress": {
                "status": "downloading",
                "downloaded_bytes": percent,
                "total_bytes": 100,
                "speed": 1024,
                "eta": 12,
                "elapsed": 3,
                **progress,
            },
        }
    )


def post_line(processor, status="started"):
    return "VT_POSTPROCESS:" + json.dumps(
        {
            "info": {"id": "abc", "title": "Тест"},
            "progress": {"postprocessor": processor, "status": status},
        }
    )


class Tty(StringIO):
    def __init__(self):
        super().__init__()
        self.flushes = 0

    def isatty(self):
        return True

    def flush(self):
        self.flushes += 1


class ProgressRendererTests(IsolatedTestCase):
    def setUp(self):
        super().setUp()
        self.enterContext(patch.dict("os.environ", {"CI": "", "TERM": "xterm"}))
        self.now = 0

    def renderer(self, **kwargs):
        return DownloadProgressRenderer(Console(**kwargs), clock=lambda: self.now)

    def test_parse_unicode_quotes_backslashes_delimiters_and_missing_numbers(self):
        title = '中文 | "Заголовок" \\ путь'
        event = parse_event(
            download_line(
                info={"title": title},
                total_bytes=None,
                total_bytes_estimate=200,
                speed="NA",
                eta=None,
            )
        )
        self.assertEqual(event.title, title)
        self.assertIsNone(event.speed)
        self.assertIsNone(event.eta)
        self.assertIsNone(event.total_bytes)
        self.assertEqual(event.total_bytes_estimate, 200)
        self.assertEqual(parse_event("VT_PROGRESS:{}").video_id, "")
        self.assertIsNone(parse_event("VT_UNKNOWN:{}"))

    def test_tty_updates_finish_and_next_video(self):
        output = Tty()
        renderer = self.renderer()
        with (
            redirect_stdout(output),
            patch.object(Console, "terminal_width", new_callable=PropertyMock, return_value=120),
        ):
            for value in (10, 40, 75):
                self.now += 0.3
                renderer.on_line(download_line(value))
            self.assertNotIn("\n", output.getvalue())
            self.assertIn("\r", output.getvalue())
            renderer.on_line(download_line(100, status="finished"))
            self.assertNotIn("\n", output.getvalue())
            self.assertNotIn("✓", output.getvalue())
            renderer.on_line('VT_COMPLETE:{"id":"abc","title":"Тестовый ролик"}')
            self.assertEqual(output.getvalue().count("\n"), 1)
            renderer.on_line(download_line(0, info={"id": "next", "title": "Следующее видео"}))
            self.assertEqual(output.getvalue().count("\n"), 1)
            renderer.close()
            renderer.console.info("prompt")
        self.assertTrue(output.getvalue().endswith("[INFO] prompt\n"))
        self.assertGreater(output.flushes, 3)

    def test_warning_and_error_clear_live_line(self):
        for level in ("WARNING", "ERROR"):
            output = Tty()
            renderer = self.renderer()
            with redirect_stdout(output):
                renderer.on_line(download_line())
                renderer.on_line(f"{level}: diagnostic")
            tag = "WARN" if level == "WARNING" else "ERROR"
            self.assertIn(f"\r[{tag}] {level}: diagnostic\n", output.getvalue())
            self.assertEqual(renderer.console._live_width, 0)

    def test_interrupt_clears_live_line_before_next_prompt(self):
        output = Tty()
        renderer = self.renderer()
        with redirect_stdout(output):
            with self.assertRaises(KeyboardInterrupt), use_console(renderer.console):
                renderer.on_line(download_line())
                raise KeyboardInterrupt
            self.assertEqual(renderer.console._live_width, 0)
            print("prompt")
        self.assertTrue(output.getvalue().endswith("\rprompt\n"))

    def test_non_tty_throttles_without_control_codes(self):
        output = StringIO()
        renderer = self.renderer()
        with redirect_stdout(output):
            for value in range(1, 10):
                self.now += 0.05
                renderer.on_line(download_line(value))
            self.now = 2
            renderer.on_line(download_line(11))
            self.assertEqual(output.getvalue().count("[PROGRESS]"), 2)
            renderer.on_line(download_line(100, status="finished"))
        text = output.getvalue()
        self.assertEqual(text.count("[PROGRESS]"), 3)
        self.assertIn("100.0%", text)
        self.assertNotIn("[OK]", text)
        self.assertTrue(text.endswith("\n"))
        self.assertNotIn("\r", text)
        self.assertNotIn("\x1b", text)

    def test_quiet_retains_warnings_and_errors_only(self):
        output = Tty()
        renderer = self.renderer(quiet=True, verbose=True)
        with redirect_stdout(output):
            renderer.on_line(download_line())
            renderer.on_line('VT_START:{"id":"abc","title":"Тест"}')
            renderer.on_line(post_line("Merger"))
            renderer.on_line('VT_COMPLETE:{"id":"abc"}')
            renderer.on_line("technical output")
            renderer.on_line("WARNING: warning")
            renderer.on_line("ERROR: error")
            renderer.close()
        self.assertEqual(output.getvalue(), "[WARN] WARNING: warning\n[ERROR] ERROR: error\n")

    def test_verbose_malformed_unknown_and_technical_events(self):
        output = StringIO()
        renderer = self.renderer(verbose=True)
        with redirect_stdout(output):
            for line in (
                "VT_PROGRESS:{broken",
                "VT_PROGRESS:[]",
                "VT_PROGRESS:null",
                'VT_PROGRESS:{"info":null}',
                "VT_UNKNOWN:{}",
                "[debug] details",
            ):
                renderer.on_line(line)
        self.assertEqual(output.getvalue().count("[VERBOSE]"), 6)
        self.assertIn("Некорректное progress-событие", output.getvalue())
        self.assertIn("[debug] details", output.getvalue())

    def test_postprocessor_mappings_and_duplicate_stages(self):
        output = StringIO()
        renderer = self.renderer()
        with redirect_stdout(output):
            for processor in ("Merger", "SponsorBlock", "ModifyChapters", "CustomTool"):
                renderer.on_line(post_line(processor))
                renderer.on_line(post_line(processor, "processing"))
                renderer.on_line(post_line(processor, "finished"))
        lines = output.getvalue().splitlines()
        self.assertEqual(len(lines), 4)
        for text in (
            "Объединение видео и аудио",
            "SponsorBlock: обработка сегментов",
            "Запись глав",
            "Обработка: CustomTool",
        ):
            self.assertIn(text, output.getvalue())

    def test_composite_streams_and_reliable_video_count(self):
        output = StringIO()
        renderer = self.renderer()
        with redirect_stdout(output):
            renderer.on_line(download_line(100, status="finished"))
            renderer.on_line(
                download_line(0, info={"format_id": "140", "vcodec": "none", "acodec": "aac"})
            )
            renderer.on_line(
                download_line(
                    100,
                    status="finished",
                    info={"format_id": "140", "vcodec": "none", "acodec": "aac"},
                )
            )
            self.assertEqual(len(renderer.completed), 0)
            self.assertNotIn("[OK]", output.getvalue())
            self.assertNotIn("✓", output.getvalue())
            renderer.on_line(post_line("Merger"))
            renderer.on_line('VT_COMPLETE:{"id":"abc","title":"Тест"}')
            renderer.on_line('VT_COMPLETE:{"id":"abc","title":"Тест"}')
            renderer.finish(0)
        self.assertIn("Видео", output.getvalue())
        self.assertIn("Аудио", output.getvalue())
        self.assertIn("Видео завершено: 1", output.getvalue())
        self.assertNotIn("Пропущено", output.getvalue())
        self.assertEqual(output.getvalue().count("✓"), 1)
        self.assertLess(output.getvalue().index("Объединение"), output.getvalue().index("✓"))

    def test_two_single_file_videos_have_one_final_line_each(self):
        for output in (Tty(), StringIO()):
            with self.subTest(tty=output.isatty()), redirect_stdout(output):
                renderer = self.renderer()
                for video_id in ("first", "second"):
                    renderer.on_line("VT_START:" + json.dumps({"id": video_id, "title": video_id}))
                    info = {"id": video_id, "title": video_id, "acodec": "aac"}
                    renderer.on_line(download_line(20, info=info))
                    renderer.on_line(download_line(100, info=info, status="finished"))
                    renderer.on_line(post_line("ModifyChapters"))
                    complete = "VT_COMPLETE:" + json.dumps({"id": video_id, "title": video_id})
                    renderer.on_line(complete)
                    renderer.on_line(complete)
                text = output.getvalue()
                self.assertEqual(text.count("✓"), 2)
                self.assertEqual(text.count("Готово"), 2)
                self.assertIn("Медиа", text)
                self.assertIn("Подготовка", text)
                if output.isatty():
                    self.assertNotIn("[OK]", text)
                    self.assertNotIn("[INFO]", text)
                else:
                    self.assertEqual(text.count("[OK]"), 2)
                    self.assertIn("[INFO]", text)
                    self.assertNotIn("\r", text)
                    self.assertNotIn("\x1b", text)

    def test_width_and_estimate_fallback(self):
        renderer = self.renderer()
        event = parse_event(
            download_line(
                50,
                total_bytes=None,
                total_bytes_estimate=100,
                info={"title": "中文Очень длинное название " * 20},
            )
        )
        for width in (160, 100, 60, 30, 12):
            with (
                redirect_stdout(Tty()),
                patch.object(
                    Console, "terminal_width", new_callable=PropertyMock, return_value=width
                ),
            ):
                frame = renderer.frame(event, 50)
                self.assertLessEqual(display_width(frame), width - 1)
                self.assertIn("50.0%", frame)
                if width > 30:
                    self.assertIn("中文", frame)
        output = StringIO()
        with redirect_stdout(output):
            renderer.on_line(download_line(50, total_bytes=None, total_bytes_estimate=100))
        self.assertIn("50.0%", output.getvalue())

    def test_environment_and_frequency(self):
        for environment in ({"CI": "true"}, {"TERM": "dumb"}):
            output = Tty()
            with patch.dict("os.environ", environment), redirect_stdout(output):
                renderer = self.renderer()
                renderer.on_line(download_line())
                self.assertEqual(
                    renderer.arguments()[renderer.arguments().index("--progress-delta") + 1], "1"
                )
            self.assertNotIn("\r", output.getvalue())
            self.assertNotIn("\x1b", output.getvalue())
        with patch.dict("os.environ", {"NO_COLOR": "1"}), redirect_stdout(Tty()) as output:
            renderer = self.renderer()
            self.assertIn("0.25", renderer.arguments())
            renderer.on_line(download_line())
            renderer.close()
            self.assertNotIn("\x1b", output.getvalue())
