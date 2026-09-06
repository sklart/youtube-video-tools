import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from youtube_video_tools import cli as video_tools
from youtube_video_tools.config import BASE_DIR, DEFAULT_CONFIG_PATH
from youtube_video_tools.console import console_print
from youtube_video_tools.core import extract_filename_date, normalize_windows_name
from youtube_video_tools.locking import ArchiveLock


class VideoToolsTests(unittest.TestCase):
    def test_resort_receives_root_and_config(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config = root / "config.toml"
            config.write_text("", encoding="utf-8")

            fake_module = type("FakeModule", (), {"main": staticmethod(lambda: 0)})
            with patch.dict(
                video_tools.COMMAND_MODULES,
                {"resort": fake_module},
            ):
                result = video_tools.main(
                    [
                        "--root",
                        str(root),
                        "--config",
                        str(config),
                        "resort",
                        "--dry-run",
                    ]
                )

            self.assertEqual(result, 0)

    def test_all_commands_receive_common_root(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config = root / "config.toml"
            config.write_text("", encoding="utf-8")

            for command in video_tools.COMMAND_MODULES:
                received_argv = []

                def fake_main():
                    received_argv.extend(__import__("sys").argv)
                    return 0

                fake_module = type(
                    "FakeModule",
                    (),
                    {"main": staticmethod(fake_main)},
                )
                with (
                    self.subTest(command=command),
                    patch.dict(
                        video_tools.COMMAND_MODULES,
                        {command: fake_module},
                    ),
                ):
                    result = video_tools.execute_command(
                        command,
                        [],
                        root=root,
                        config_path=config,
                    )

                self.assertEqual(result, 0)
                self.assertEqual(received_argv[1:3], ["--root", str(root)])

    def test_main_passes_resolved_folder_filter(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "Channel One").mkdir()
            config = root / "config.toml"
            config.write_text("", encoding="utf-8")

            fake_module = type("FakeModule", (), {"main": staticmethod(lambda: 0)})
            with (
                patch.dict(
                    video_tools.COMMAND_MODULES,
                    {"report": fake_module},
                ),
                patch("youtube_video_tools.cli.execute_command", return_value=0) as execute,
            ):
                result = video_tools.main(
                    [
                        "--root",
                        str(root),
                        "--config",
                        str(config),
                        "--folder",
                        "channel*",
                        "report",
                    ]
                )

            self.assertEqual(result, 0)
            self.assertEqual(
                execute.call_args.kwargs["folders"],
                ("Channel One",),
            )

    def test_verbose_prints_effective_context(self):
        fake_module = type("FakeModule", (), {"main": staticmethod(lambda: 0)})
        output = StringIO()
        with (
            patch.dict(
                video_tools.COMMAND_MODULES,
                {"dates": fake_module},
            ),
            redirect_stdout(output),
        ):
            result = video_tools.execute_command(
                "dates",
                [],
                root=Path("archive"),
                config_path=Path("custom.toml"),
                verbose=True,
            )

        self.assertEqual(result, 0)
        self.assertIn("[VERBOSE] root=archive", output.getvalue())
        self.assertIn("[VERBOSE] config=custom.toml", output.getvalue())
        self.assertIn("[VERBOSE] command=dates", output.getvalue())

    def test_quiet_keeps_only_warnings_and_errors(self):
        def fake_main():
            console_print("[SCAN] ordinary")
            console_print("[WARNING] warning")
            console_print("[ERROR] error")
            return 2

        fake_module = type(
            "FakeModule",
            (),
            {"main": staticmethod(fake_main)},
        )
        output = StringIO()
        with (
            patch.dict(
                video_tools.COMMAND_MODULES,
                {"dates": fake_module},
            ),
            redirect_stdout(output),
        ):
            result = video_tools.execute_command(
                "dates",
                [],
                root=Path("archive"),
                config_path=Path("custom.toml"),
                quiet=True,
            )

        self.assertEqual(result, 2)
        self.assertNotIn("[SCAN]", output.getvalue())
        self.assertIn("[WARN] warning", output.getvalue())
        self.assertIn("[ERROR] error", output.getvalue())

    def test_cli_rejects_each_mutating_command_while_archive_locked(self):
        cases = (
            ("download", []),
            ("rename", ["--apply"]),
            ("resort", ["--apply"]),
            ("resort", ["--undo-last"]),
            ("archive-sync", ["--apply"]),
            ("bookmarks", ["--apply"]),
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config = root / "config.toml"
            config.write_text("", encoding="utf-8")
            with ArchiveLock(root):
                for command, arguments in cases:
                    with self.subTest(command=command, arguments=arguments):
                        output = StringIO()
                        with redirect_stdout(output):
                            result = video_tools.execute_command(
                                command,
                                arguments,
                                root=root,
                                config_path=config,
                            )
                        self.assertEqual(result, 1)
                        self.assertIn("архив уже изменяется", output.getvalue())

    def test_read_only_commands_run_while_archive_locked(self):
        cases = (
            ("rename", ["--dry-run"]),
            ("resort", ["--dry-run"]),
            ("archive-sync", ["--dry-run"]),
            ("bookmarks", ["--dry-run"]),
            ("dates", []),
            ("resolution", []),
            ("inventory", []),
            ("report", []),
            ("duplicates", []),
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            for command, arguments in cases:
                called = []
                fake_module = type(
                    "FakeModule",
                    (),
                    {"main": staticmethod(lambda: called.append(True) or 0)},
                )
                with (
                    self.subTest(command=command),
                    ArchiveLock(root),
                    patch.dict(video_tools.COMMAND_MODULES, {command: fake_module}),
                ):
                    result = video_tools.execute_command(
                        command,
                        arguments,
                        root=root,
                        config_path=root / "config.toml",
                    )
                self.assertEqual(result, 0)
                self.assertEqual(called, [True])

    def test_quiet_never_injects_yes_for_mutating_commands(self):
        for command, arguments in (
            ("resort", ["--apply"]),
            ("archive-sync", ["--apply"]),
            ("bookmarks", ["--apply"]),
        ):
            received: list[str] = []

            def fake_main():
                import sys

                received.extend(sys.argv)
                return 0

            fake_module = type("FakeModule", (), {"main": staticmethod(fake_main)})
            with (
                self.subTest(command=command),
                patch.dict(video_tools.COMMAND_MODULES, {command: fake_module}),
            ):
                result = video_tools.execute_command(
                    command,
                    arguments,
                    root=Path("archive"),
                    config_path=Path("config.toml"),
                    quiet=True,
                )
            self.assertEqual(result, 0)
            self.assertNotIn("--yes", received)

    def test_explicit_yes_is_preserved(self):
        received: list[str] = []

        def fake_main():
            import sys

            received.extend(sys.argv)
            return 0

        fake_module = type("FakeModule", (), {"main": staticmethod(fake_main)})
        with patch.dict(video_tools.COMMAND_MODULES, {"bookmarks": fake_module}):
            result = video_tools.execute_command(
                "bookmarks",
                ["--apply", "--yes"],
                root=Path("archive"),
                config_path=Path("config.toml"),
                quiet=True,
            )
        self.assertEqual(result, 0)
        self.assertIn("--yes", received)

    def test_cli_restores_process_state_after_command_errors(self):
        import os
        import sys

        original_argv = sys.argv
        original_config = os.environ.get("VIDEO_TOOLS_CONFIG")
        original_folders = os.environ.get("VIDEO_TOOLS_FOLDERS")
        fake_module = type(
            "FakeModule",
            (),
            {"main": staticmethod(lambda: (_ for _ in ()).throw(OSError("broken")))},
        )
        with patch.dict(video_tools.COMMAND_MODULES, {"dates": fake_module}):
            result = video_tools.execute_command(
                "dates",
                [],
                root=Path("archive"),
                config_path=Path("config.toml"),
                folders=("Channel",),
            )
        self.assertEqual(result, 2)
        self.assertIs(sys.argv, original_argv)
        self.assertEqual(os.environ.get("VIDEO_TOOLS_CONFIG"), original_config)
        self.assertEqual(os.environ.get("VIDEO_TOOLS_FOLDERS"), original_folders)

    def test_keyboard_interrupt_returns_130_and_releases_lock(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            fake_module = type(
                "FakeModule",
                (),
                {"main": staticmethod(lambda: (_ for _ in ()).throw(KeyboardInterrupt))},
            )
            with patch.dict(video_tools.COMMAND_MODULES, {"download": fake_module}):
                result = video_tools.execute_command(
                    "download", [], root=root, config_path=root / "config.toml"
                )
            self.assertEqual(result, 130)
            with ArchiveLock(root):
                pass

    def test_console_print_handles_legacy_windows_encoding(self):
        class Cp1252Stream(StringIO):
            encoding = "cp1252"

            def write(self, value):
                value.encode(self.encoding)
                return super().write(value)

        output = Cp1252Stream()
        with patch("sys.stdout", output):
            console_print("Проверка Unicode")
        self.assertTrue(output.getvalue())

    def test_menu_runs_doctor(self):
        answers = iter(["1", "", "0"])
        with (
            patch("youtube_video_tools.cli.execute_command", return_value=0) as execute,
            redirect_stdout(StringIO()),
        ):
            result = video_tools.main([], input_fn=lambda _: next(answers))

        self.assertEqual(result, 0)
        execute.assert_called_once_with(
            "doctor",
            [],
            root=BASE_DIR.resolve(),
            config_path=DEFAULT_CONFIG_PATH.resolve(),
        )

    def test_menu_returns_to_start_after_command(self):
        answers = iter(["1", "", "0"])
        prompts = []
        with (
            patch("youtube_video_tools.cli.execute_command", return_value=0) as execute,
            redirect_stdout(StringIO()),
        ):
            result = video_tools.main(
                [],
                input_fn=lambda prompt: prompts.append(prompt) or next(answers),
            )

        self.assertEqual(result, 0)
        self.assertEqual(prompts.count("Выберите действие: "), 2)
        self.assertIn("Нажмите Enter, чтобы вернуться в меню...", prompts)
        execute.assert_called_once()

    def test_menu_cancelled_rename_apply_does_not_run(self):
        answers = iter(["5", "no", "0"])
        with (
            patch("youtube_video_tools.cli.execute_command") as execute,
            redirect_stdout(StringIO()),
        ):
            result = video_tools.main([], input_fn=lambda _: next(answers))

        self.assertEqual(result, 0)
        execute.assert_not_called()

    def test_menu_confirmed_rename_apply_runs(self):
        answers = iter(["5", "y", "", "0"])
        with (
            patch("youtube_video_tools.cli.execute_command", return_value=0) as execute,
            redirect_stdout(StringIO()),
        ):
            result = video_tools.main([], input_fn=lambda _: next(answers))

        self.assertEqual(result, 0)
        execute.assert_called_once_with(
            "rename",
            ["--apply"],
            root=BASE_DIR.resolve(),
            config_path=DEFAULT_CONFIG_PATH.resolve(),
        )

    def test_menu_confirmed_rename_apply_runs_with_cyrillic_yes(self):
        answers = iter(["5", "д", "", "0"])
        with (
            patch("youtube_video_tools.cli.execute_command", return_value=0) as execute,
            redirect_stdout(StringIO()),
        ):
            result = video_tools.main([], input_fn=lambda _: next(answers))

        self.assertEqual(result, 0)
        execute.assert_called_once_with(
            "rename",
            ["--apply"],
            root=BASE_DIR.resolve(),
            config_path=DEFAULT_CONFIG_PATH.resolve(),
        )

    def test_menu_exit(self):
        with (
            patch("youtube_video_tools.cli.execute_command") as execute,
            redirect_stdout(StringIO()),
        ):
            result = video_tools.main([], input_fn=lambda _: "0")

        self.assertEqual(result, 0)
        execute.assert_not_called()

    def test_all_actionable_menu_options_have_help(self):
        actionable = {
            key for key, (_, command, _) in video_tools.MENU_OPTIONS.items() if command is not None
        }
        self.assertEqual(actionable, set(video_tools.MENU_HELP))

    def test_normalize_windows_name(self):
        self.assertEqual(normalize_windows_name("CON"), "_CON")
        self.assertEqual(normalize_windows_name("name. "), "name")
        self.assertEqual(
            normalize_windows_name(
                "bad:name?.mp4",
                preserve_extension=True,
            ),
            "bad_name_.mp4",
        )
        long_name = normalize_windows_name(
            ("x" * 200) + ".mp4",
            preserve_extension=True,
        )
        self.assertEqual(len(long_name), 120)
        self.assertTrue(long_name.endswith(".mp4"))

    def test_extract_filename_date_validates_calendar(self):
        self.assertEqual(
            extract_filename_date("Video_29.02.2024 [abcdefghijk].mp4"),
            ("29.02.2024", True),
        )
        self.assertEqual(
            extract_filename_date("Video_29.02.2023 [abcdefghijk].mp4"),
            ("29.02.2023", False),
        )
        self.assertEqual(
            extract_filename_date("Video [abcdefghijk].mp4"),
            (None, False),
        )
        self.assertEqual(
            extract_filename_date("Video_15.06.2015 [abcdefghijk].ru.vtt"),
            ("15.06.2015", True),
        )


if __name__ == "__main__":
    unittest.main()
