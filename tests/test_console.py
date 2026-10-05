from contextlib import redirect_stdout
from io import StringIO
from unittest.mock import patch

from tests.support import IsolatedTestCase
from youtube_video_tools.console import Console, Level, console_print, use_console


class ConsoleTests(IsolatedTestCase):
    def test_download_colors_reset_and_do_not_affect_width(self):
        output = StringIO()
        console = Console()
        with (
            redirect_stdout(output),
            patch.object(Console, "is_interactive", True),
            patch.dict("os.environ", {}, clear=True),
            patch("youtube_video_tools.console._enable_terminal_color", return_value=True),
            patch.object(Console, "terminal_width", 12),
        ):
            console.write_live("A" * 30)
            self.assertEqual(console._live_width, 11)
            console.warning("warning")
            console.stage("> processing")
            console.finish_live("done")
        text = output.getvalue()
        self.assertIn("\033[1;36m" + "A" * 10 + "…\033[0m", text)
        self.assertIn("[WARN] warning\n> processing\n", text)
        self.assertIn("\033[1;32mdone\033[0m\n", text)

    def test_color_disabled_in_plain_environments(self):
        for env, interactive, supported, quiet in (
            ({"NO_COLOR": ""}, True, True, False),
            ({"CI": "true"}, False, True, False),
            ({"TERM": "dumb"}, False, True, False),
            ({}, False, True, False),
            ({}, True, False, False),
            ({}, True, True, True),
        ):
            with self.subTest(env=env, interactive=interactive, supported=supported, quiet=quiet):
                output = StringIO()
                with (
                    redirect_stdout(output),
                    patch.object(Console, "is_interactive", interactive),
                    patch.dict("os.environ", env, clear=True),
                    patch(
                        "youtube_video_tools.console._enable_terminal_color", return_value=supported
                    ),
                ):
                    console = Console(quiet=quiet)
                    console.write_live("download")
                    console.finish_live("done")
                self.assertNotIn("\033[", output.getvalue())
                if quiet:
                    self.assertEqual(output.getvalue(), "")

    def test_exact_output_has_one_prefix_per_level(self):
        output = StringIO()
        console = Console()
        with redirect_stdout(output):
            console.info("info")
            console.warning("warning")
            console.error("error")
            console.success("success")
            console.progress("progress")
            console.verbose("hidden")
        self.assertEqual(
            output.getvalue(),
            "[INFO] info\n[WARN] warning\n[ERROR] error\n[OK] success\n[PROGRESS] progress\n",
        )

    def test_quiet_shows_only_warning_and_error(self):
        output = StringIO()
        console = Console(quiet=True)
        with redirect_stdout(output):
            for level in Level:
                console.emit(level, level.value)
        self.assertEqual(output.getvalue(), "[WARN] WARN\n[ERROR] ERROR\n")

    def test_verbose_includes_verbose_only_when_enabled(self):
        output = StringIO()
        with redirect_stdout(output):
            Console().verbose("hidden")
            Console(verbose=True).verbose("shown")
        self.assertEqual(output.getvalue(), "[VERBOSE] shown\n")

    def test_legacy_fallback_is_raw_info_without_severity_guessing(self):
        output = StringIO()
        with redirect_stdout(output), use_console(Console()):
            console_print("legacy text")
        self.assertEqual(output.getvalue(), "[INFO] legacy text\n")
