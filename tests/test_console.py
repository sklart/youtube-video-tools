import unittest
from contextlib import redirect_stdout
from io import StringIO

from youtube_video_tools.console import Console, Level, console_print, use_console


class ConsoleTests(unittest.TestCase):
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
