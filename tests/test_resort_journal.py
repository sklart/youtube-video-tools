import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from youtube_video_tools.commands import resort
from youtube_video_tools.commands.resort import (
    apply_move_plan,
    confirm_apply,
    find_last_undoable_run,
    get_uploader,
    journal_path,
    preflight_apply,
    read_journal,
    undo_last_run,
    validate_undo_plan,
    write_journal_event,
)


class ResortJournalTests(unittest.TestCase):
    def test_main_scans_non_mp4_video_in_root(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            video = root / "Video [abcdefghijk].mkv"
            video.write_bytes(b"video")

            previous_argv = sys.argv
            try:
                sys.argv = [
                    "resort.py",
                    "--root",
                    str(root),
                    "--dry-run",
                ]
                with (
                    patch.object(
                        resort,
                        "get_uploader",
                        return_value="Channel",
                    ),
                    redirect_stdout(StringIO()),
                ):
                    result = resort.main()
            finally:
                sys.argv = previous_argv

            self.assertEqual(result, 0)

    def test_apply_and_undo_last_run(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "video.mp4"
            subtitle = root / "video.ru.vtt"
            source.write_bytes(b"video")
            subtitle.write_text("subtitle", encoding="utf-8")

            run_id = "test-run"
            write_journal_event(
                root,
                {
                    "event": "run_start",
                    "action": "resort",
                    "run_id": run_id,
                    "videos": 1,
                },
            )
            plan = [
                (source, root / "Channel" / source.name),
                (subtitle, root / "Channel" / subtitle.name),
            ]
            apply_move_plan(root, run_id, plan)
            write_journal_event(
                root,
                {
                    "event": "run_end",
                    "action": "resort",
                    "run_id": run_id,
                    "result": "success",
                },
            )

            self.assertFalse(source.exists())
            self.assertTrue(plan[0][1].exists())
            with redirect_stdout(StringIO()):
                self.assertEqual(undo_last_run(root), 0)
            self.assertTrue(source.exists())
            self.assertTrue(subtitle.exists())
            self.assertFalse(plan[0][1].exists())
            self.assertIsNone(find_last_undoable_run(read_journal(root)))

    def test_undo_refuses_to_overwrite_existing_source(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            original = root / "video.mp4"
            current = root / "Channel" / "video.mp4"
            current.parent.mkdir()
            original.write_bytes(b"new")
            current.write_bytes(b"old")

            with self.assertRaises(FileExistsError):
                validate_undo_plan(
                    root,
                    [("video.mp4", "Channel/video.mp4")],
                )

    def test_apply_rolls_back_completed_moves_after_failure(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "video.mp4"
            missing_subtitle = root / "missing.vtt"
            destination = root / "Channel" / "video.mp4"
            source.write_bytes(b"video")

            with self.assertRaises(OSError):
                apply_move_plan(
                    root,
                    "failed-run",
                    [
                        (source, destination),
                        (
                            missing_subtitle,
                            root / "Channel" / missing_subtitle.name,
                        ),
                    ],
                )

            self.assertTrue(source.exists())
            self.assertFalse(destination.exists())
            self.assertEqual(
                find_last_undoable_run(read_journal(root)),
                None,
            )

    def test_journal_is_jsonl(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_journal_event(
                root,
                {"event": "run_start", "action": "resort", "run_id": "abc"},
            )
            line = journal_path(root).read_text(encoding="utf-8").strip()
            record = json.loads(line)
            self.assertEqual(record["run_id"], "abc")
            self.assertIn("timestamp", record)

    def test_preflight_accepts_valid_plan(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "video.mp4"
            source.write_bytes(b"video")
            destination = root / "Channel" / source.name

            preflight_apply(root, [[(source, destination)]])

            self.assertTrue(source.exists())
            self.assertFalse(destination.exists())

    def test_preflight_rejects_existing_destination(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "video.mp4"
            destination = root / "Channel" / source.name
            destination.parent.mkdir()
            source.write_bytes(b"source")
            destination.write_bytes(b"destination")

            with self.assertRaises(FileExistsError):
                preflight_apply(root, [[(source, destination)]])

    def test_preflight_rejects_destination_outside_root(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "archive"
            root.mkdir()
            source = root / "video.mp4"
            source.write_bytes(b"video")
            with self.assertRaises(ValueError):
                preflight_apply(root, [[(source, root / ".." / "outside.mp4")]])

    def test_preflight_rejects_duplicate_normalized_destinations(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            first = root / "first.mp4"
            second = root / "second.mp4"
            first.write_bytes(b"first")
            second.write_bytes(b"second")
            target = root / "Channel" / "video.mp4"
            equivalent = root / "Channel" / "subdir" / ".." / "video.mp4"
            with self.assertRaises(FileExistsError):
                preflight_apply(root, [[(first, target), (second, equivalent)]])

    def test_confirmation_accepts_short_yes(self):
        with redirect_stdout(StringIO()):
            self.assertTrue(
                confirm_apply(
                    video_count=1,
                    file_count=2,
                    folder_count=1,
                    assume_yes=False,
                    input_fn=lambda _: "y",
                )
            )
            self.assertTrue(
                confirm_apply(
                    video_count=1,
                    file_count=2,
                    folder_count=1,
                    assume_yes=False,
                    input_fn=lambda _: "д",
                )
            )
            self.assertTrue(
                confirm_apply(
                    video_count=1,
                    file_count=2,
                    folder_count=1,
                    assume_yes=False,
                    input_fn=lambda _: "",
                )
            )
            self.assertFalse(
                confirm_apply(
                    video_count=1,
                    file_count=2,
                    folder_count=1,
                    assume_yes=False,
                    input_fn=lambda _: "n",
                )
            )
            self.assertTrue(
                confirm_apply(
                    video_count=1,
                    file_count=2,
                    folder_count=1,
                    assume_yes=True,
                    input_fn=lambda _: self.fail("input should not be called"),
                )
            )

    def test_unknown_uploader_is_not_a_default_destination(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            cache = {}
            with redirect_stdout(StringIO()):
                uploader = get_uploader(
                    None,
                    "unknown",
                    yt_dlp="yt-dlp",
                    cookies=None,
                    timeout=1,
                    max_retries=1,
                    pause=0,
                    cache=cache,
                    root=root,
                    write_log=False,
                )
            self.assertIsNone(uploader)
            self.assertFalse((root / "errors.log").exists())


if __name__ == "__main__":
    unittest.main()
