import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import patch

from youtube_video_tools.commands import bookmarks as update_bookmarks


class UpdateBookmarksTests(unittest.TestCase):
    def test_normalize_chapters_supports_ffprobe_and_yt_dlp_shapes(self):
        chapters = update_bookmarks.normalize_chapters(
            [
                {
                    "start_time": "1.25",
                    "end_time": "3.5",
                    "tags": {"title": "Intro"},
                },
                {
                    "start_time": 10.0,
                    "end_time": 12.0,
                    "title": "Sponsor",
                    "category": "sponsor",
                },
            ]
        )
        self.assertEqual(
            chapters,
            [
                {
                    "start_time": 1.25,
                    "end_time": 3.5,
                    "title": "Intro",
                    "category": "",
                },
                {
                    "start_time": 10.0,
                    "end_time": 12.0,
                    "title": "Sponsor",
                    "category": "sponsor",
                },
            ],
        )

    def test_build_ffmetadata_includes_category_prefix(self):
        text = update_bookmarks.build_ffmetadata(
            [
                {
                    "start_time": 1.0,
                    "end_time": 2.5,
                    "title": "Promo = bit",
                    "category": "selfpromo",
                }
            ]
        )
        self.assertIn(";FFMETADATA1", text)
        self.assertIn("START=1000", text)
        self.assertIn("END=2500", text)
        self.assertIn(r"title=[selfpromo] Promo \= bit", text)

    def test_short_chapter_diff_reports_removed_and_timing_only_changes(self):
        removed = update_bookmarks.short_chapter_diff(
            [
                {"start_time": 0.0, "end_time": 1.0, "title": "Intro", "category": ""},
                {"start_time": 1.0, "end_time": 2.0, "title": "Sponsor", "category": "sponsor"},
                {"start_time": 2.0, "end_time": 3.0, "title": "Topic", "category": ""},
            ],
            [
                {"start_time": 0.0, "end_time": 1.0, "title": "Intro", "category": ""},
                {"start_time": 2.0, "end_time": 3.0, "title": "Topic", "category": ""},
            ],
        )
        timing_only = update_bookmarks.short_chapter_diff(
            [
                {"start_time": 0.0, "end_time": 1.0, "title": "Intro", "category": ""},
            ],
            [
                {"start_time": 0.5, "end_time": 1.5, "title": "Intro", "category": ""},
            ],
        )
        self.assertEqual(removed, ["удалена: [sponsor] Sponsor"])
        self.assertEqual(timing_only, ["обновлены таймкоды без изменения названий"])

    def test_confirm_apply_accepts_single_letter_case_insensitive(self):
        self.assertTrue(update_bookmarks.confirm_apply(1, False, input_fn=lambda _: "y"))
        self.assertTrue(update_bookmarks.confirm_apply(1, False, input_fn=lambda _: "Y"))
        self.assertTrue(update_bookmarks.confirm_apply(1, False, input_fn=lambda _: "д"))
        self.assertTrue(update_bookmarks.confirm_apply(1, False, input_fn=lambda _: "Да"))
        self.assertFalse(update_bookmarks.confirm_apply(1, False, input_fn=lambda _: "n"))
        self.assertTrue(update_bookmarks.confirm_apply(1, False, input_fn=lambda _: ""))

    def test_progress_prefix_includes_eta(self):
        with patch.object(update_bookmarks.time, "monotonic", return_value=130.0):
            line = update_bookmarks.progress_prefix(
                "CHECK",
                2,
                5,
                "Video [abcdefghijk].mp4",
                100.0,
            )
        self.assertEqual(
            line,
            "[CHECK] 2/5 Video [abcdefghijk].mp4 (ETA 45s)",
        )

    def test_build_progress_tracker_waits_before_eta(self):
        with patch.object(
            update_bookmarks.time,
            "monotonic",
            side_effect=[100.0, 101.0, 102.0, 102.0],
        ):
            tracker = update_bookmarks.build_progress_tracker(window_size=3, min_samples_for_eta=2)
            first_line = tracker.line("CHECK", 1, 5, "Video [abcdefghijk].mp4")
            tracker.tick()
            second_line = tracker.line("CHECK", 2, 5, "Video [abcdefghijk].mp4")
            tracker.tick()
            third_line = tracker.line("CHECK", 3, 5, "Video [abcdefghijk].mp4")
        self.assertIn("ETA collecting...", first_line)
        self.assertIn("ETA collecting...", second_line)
        self.assertIn("ETA 3s", third_line)

    def test_classify_remote_error_distinguishes_private_and_retry(self):
        private_kind = update_bookmarks.classify_remote_error(
            "ERROR: [youtube] abcdefghijk: Private video. Sign in if you've been granted access to this video."
        )
        retry_kind = update_bookmarks.classify_remote_error(
            "ERROR: [youtube] abcdefghijk: This content isn't available, try again later. The current session has been rate-limited by YouTube for up to an hour."
        )
        other_kind = update_bookmarks.classify_remote_error("some unrelated failure")
        self.assertEqual(private_kind, "private_or_unavailable")
        self.assertEqual(retry_kind, "retry_later")
        self.assertEqual(other_kind, "other_error")

    def test_fetch_remote_video_info_retries_rate_limit(self):
        responses = [
            CompletedProcess(
                ["yt-dlp"],
                1,
                stdout="",
                stderr=(
                    "ERROR: [youtube] abcdefghijk: Video unavailable. "
                    "This content isn't available, try again later. "
                    "The current session has been rate-limited by YouTube for up to an hour."
                ),
            ),
            CompletedProcess(
                ["yt-dlp"],
                0,
                stdout=json.dumps(
                    {
                        "chapters": [
                            {
                                "start_time": 1.0,
                                "end_time": 2.0,
                                "title": "Intro",
                            }
                        ],
                        "duration": 120.0,
                    }
                ),
                stderr="",
            ),
        ]

        with (
            patch.object(subprocess, "run", side_effect=responses) as run_mock,
            patch.object(update_bookmarks.time, "sleep") as sleep_mock,
            redirect_stdout(StringIO()),
        ):
            payload, error = update_bookmarks.fetch_remote_video_info(
                "abcdefghijk",
                yt_dlp="yt-dlp",
                cookies=None,
                timeout=90,
                max_retries=2,
                retry_backoff_seconds=7,
            )

        self.assertIsNone(error)
        self.assertEqual(run_mock.call_count, 2)
        sleep_mock.assert_called_once_with(7)
        self.assertEqual(payload["duration"], 120.0)
        self.assertEqual(len(payload["chapters"]), 1)

    def test_main_dry_run_plans_only_changed_youtube_videos(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            youtube_video = root / "Video [abcdefghijk].mp4"
            other_video = root / "Video [rutube123].mp4"
            youtube_video.write_bytes(b"video")
            other_video.write_bytes(b"video")
            output = StringIO()
            previous_argv = sys.argv
            try:
                sys.argv = [
                    "update_bookmarks.py",
                    "--root",
                    str(root),
                    "--dry-run",
                ]

                def fake_run(cmd, **kwargs):
                    if cmd[0] == "ffprobe":
                        if "stream=codec_type" in cmd:
                            return CompletedProcess(cmd, 0, stdout="video\n", stderr="")
                        if "-show_entries" in cmd:
                            return CompletedProcess(
                                cmd,
                                0,
                                stdout="120.0\n",
                                stderr="",
                            )
                        return CompletedProcess(
                            cmd,
                            0,
                            stdout=json.dumps({"chapters": []}),
                            stderr="",
                        )
                    if cmd[0] == "yt-dlp":
                        return CompletedProcess(
                            cmd,
                            0,
                            stdout=json.dumps(
                                {
                                    "chapters": [
                                        {
                                            "start_time": 1.0,
                                            "end_time": 2.0,
                                            "title": "Intro",
                                        }
                                    ],
                                    "duration": 120.0,
                                }
                            ),
                            stderr="",
                        )
                    raise AssertionError(f"Unexpected command: {cmd}")

                with (
                    patch.object(subprocess, "run", side_effect=fake_run),
                    redirect_stdout(output),
                ):
                    result = update_bookmarks.main()
                report_text = (root / ".video-tools" / "bookmarks-plan.txt").read_text(
                    encoding="utf-8"
                )
            finally:
                sys.argv = previous_argv

        self.assertEqual(result, 0)
        self.assertIn("[PLAN] Video [abcdefghijk].mp4:", output.getvalue())
        self.assertIn("[добавлена: Intro]", output.getvalue())
        self.assertNotIn("rutube123", output.getvalue())
        self.assertIn("[CHECK] 1/1 Video [abcdefghijk].mp4 (ETA ", output.getvalue())
        self.assertIn(
            str(Path(".video-tools") / "bookmarks-plan.txt"),
            output.getvalue(),
        )
        self.assertIn("planned_updates: 1", report_text)
        self.assertIn("private_or_unavailable: 0", report_text)
        self.assertIn("other_errors: 0", report_text)
        self.assertIn("Video [abcdefghijk].mp4 ::", report_text)

    def test_main_counts_private_video_separately(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            video = root / "Video [abcdefghijk].mp4"
            video.write_bytes(b"video")
            output = StringIO()
            previous_argv = sys.argv
            try:
                sys.argv = [
                    "update_bookmarks.py",
                    "--root",
                    str(root),
                    "--dry-run",
                ]

                def fake_run(cmd, **kwargs):
                    if cmd[0] == "ffprobe":
                        if "stream=codec_type" in cmd:
                            return CompletedProcess(cmd, 0, stdout="video\n", stderr="")
                        if "-show_entries" in cmd:
                            return CompletedProcess(cmd, 0, stdout="120.0\n", stderr="")
                        return CompletedProcess(
                            cmd,
                            0,
                            stdout=json.dumps({"chapters": []}),
                            stderr="",
                        )
                    if cmd[0] == "yt-dlp":
                        return CompletedProcess(
                            cmd,
                            1,
                            stdout="",
                            stderr=(
                                "ERROR: [youtube] abcdefghijk: Private video. "
                                "Sign in if you've been granted access to this video."
                            ),
                        )
                    raise AssertionError(f"Unexpected command: {cmd}")

                with (
                    patch.object(subprocess, "run", side_effect=fake_run),
                    redirect_stdout(output),
                ):
                    result = update_bookmarks.main()
                report_text = (root / ".video-tools" / "bookmarks-plan.txt").read_text(
                    encoding="utf-8"
                )
            finally:
                sys.argv = previous_argv

        self.assertEqual(result, 1)
        self.assertIn("private/unavailable: 1", output.getvalue())
        self.assertIn("прочих ошибок: 0", output.getvalue())
        self.assertIn("private_or_unavailable: 1", report_text)
        self.assertIn("other_errors: 0", report_text)

    def test_iter_youtube_videos_skips_temporary_bookmark_files(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            keep = root / "Video [abcdefghijk].mp4"
            skip1 = root / "Video [abcdefghijk].bookmarks.abcd.mp4"
            skip2 = root / "Video [abcdefghijk].redownload.abcd.mp4"
            keep.write_bytes(b"video")
            skip1.write_bytes(b"video")
            skip2.write_bytes(b"video")
            videos = update_bookmarks.iter_youtube_videos(root)
        self.assertEqual(videos, [(keep, "abcdefghijk")])

    def test_main_retries_retry_later_records_on_resume(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            video = root / "Video [abcdefghijk].mp4"
            video.write_bytes(b"video")
            state_dir = root / ".video-tools"
            state_dir.mkdir()
            (state_dir / "bookmarks-scan-state.json").write_text(
                json.dumps(
                    {
                        "mode": "dry-run",
                        "scan_complete": False,
                        "records": {
                            "Video [abcdefghijk].mp4": {
                                "video_id": "abcdefghijk",
                                "result": "retry_later",
                                "error": "rate-limited by YouTube",
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
            output = StringIO()
            previous_argv = sys.argv
            try:
                sys.argv = [
                    "update_bookmarks.py",
                    "--root",
                    str(root),
                    "--dry-run",
                ]

                def fake_run(cmd, **kwargs):
                    if cmd[0] == "ffprobe":
                        if "stream=codec_type" in cmd:
                            return CompletedProcess(cmd, 0, stdout="video\n", stderr="")
                        if "-show_entries" in cmd:
                            return CompletedProcess(cmd, 0, stdout="120.0\n", stderr="")
                        return CompletedProcess(
                            cmd,
                            0,
                            stdout=json.dumps({"chapters": []}),
                            stderr="",
                        )
                    if cmd[0] == "yt-dlp":
                        return CompletedProcess(
                            cmd,
                            0,
                            stdout=json.dumps(
                                {
                                    "chapters": [
                                        {
                                            "start_time": 1.0,
                                            "end_time": 2.0,
                                            "title": "Intro",
                                        }
                                    ],
                                    "duration": 120.0,
                                }
                            ),
                            stderr="",
                        )
                    raise AssertionError(f"Unexpected command: {cmd}")

                with (
                    patch.object(subprocess, "run", side_effect=fake_run) as run_mock,
                    redirect_stdout(output),
                ):
                    result = update_bookmarks.main()
            finally:
                sys.argv = previous_argv

        self.assertEqual(result, 0)
        self.assertEqual(run_mock.call_count, 3)
        self.assertIn("[RESUME] Временный бан YouTube", output.getvalue())
        self.assertIn("[PLAN] Video [abcdefghijk].mp4", output.getvalue())

    def test_main_uses_cached_trimmed_marker_even_when_durations_match(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            video = root / "Video [abcdefghijk].mp4"
            video.write_bytes(b"video")
            cache_dir = root / ".video-tools"
            cache_dir.mkdir()
            (cache_dir / "yt-dlp-cache.json").write_text(
                json.dumps(
                    {
                        "youtube:abcdefghijk": {
                            "sponsorblock_trimmed": "yes",
                            "sponsorblock_removed_segments": [
                                {
                                    "start_time": 10.0,
                                    "end_time": 20.0,
                                    "title": "Sponsor",
                                    "category": "sponsor",
                                }
                            ],
                        }
                    }
                ),
                encoding="utf-8",
            )
            output = StringIO()
            previous_argv = sys.argv
            try:
                sys.argv = [
                    "update_bookmarks.py",
                    "--root",
                    str(root),
                    "--dry-run",
                ]

                def fake_run(cmd, **kwargs):
                    if cmd[0] == "ffprobe":
                        if "stream=codec_type" in cmd:
                            return CompletedProcess(cmd, 0, stdout="video\n", stderr="")
                        if "-show_entries" in cmd:
                            return CompletedProcess(
                                cmd,
                                0,
                                stdout="120.0\n",
                                stderr="",
                            )
                        return CompletedProcess(
                            cmd,
                            0,
                            stdout=json.dumps({"chapters": []}),
                            stderr="",
                        )
                    if cmd[0] == "yt-dlp":
                        return CompletedProcess(
                            cmd,
                            0,
                            stdout=json.dumps(
                                {
                                    "chapters": [
                                        {
                                            "start_time": 1.0,
                                            "end_time": 2.0,
                                            "title": "Intro",
                                        },
                                        {
                                            "start_time": 10.0,
                                            "end_time": 20.0,
                                            "title": "Sponsor",
                                            "category": "sponsor",
                                        },
                                    ],
                                    "duration": 120.0,
                                }
                            ),
                            stderr="",
                        )
                    raise AssertionError(f"Unexpected command: {cmd}")

                with (
                    patch.object(subprocess, "run", side_effect=fake_run),
                    redirect_stdout(output),
                ):
                    result = update_bookmarks.main()
            finally:
                sys.argv = previous_argv

        self.assertEqual(result, 0)
        self.assertIn("[TRIMMED] Video [abcdefghijk].mp4", output.getvalue())
        self.assertNotIn("[PLAN] Video [abcdefghijk].mp4", output.getvalue())

    def test_rewrite_embedded_chapters_reports_ffmpeg_timeout(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            video = Path(temp_dir) / "Video [abcdefghijk].mp4"
            video.write_bytes(b"video")
            with patch.object(
                subprocess,
                "run",
                side_effect=subprocess.TimeoutExpired(["ffmpeg"], 12),
            ):
                with self.assertRaisesRegex(RuntimeError, "ffmpeg превысил таймаут"):
                    update_bookmarks.rewrite_embedded_chapters(
                        video,
                        [{"start_time": 1.0, "end_time": 2.0, "title": "Intro", "category": ""}],
                        ffmpeg="ffmpeg",
                        timeout=12,
                    )

    def test_temporary_video_validation_requires_nonempty_video_stream(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            video = Path(temp_dir) / "replacement.mp4"
            valid, error = update_bookmarks.validate_temporary_video(video, ffprobe="ffprobe")
            self.assertFalse(valid)
            self.assertIn("не создан", error)

            video.write_bytes(b"")
            valid, error = update_bookmarks.validate_temporary_video(video, ffprobe="ffprobe")
            self.assertFalse(valid)
            self.assertIn("пуст", error)

            video.write_bytes(b"video")
            with patch.object(
                update_bookmarks.FFprobeClient,
                "has_video_stream",
                return_value=True,
            ):
                valid, error = update_bookmarks.validate_temporary_video(video, ffprobe="ffprobe")
            self.assertTrue(valid)
            self.assertIsNone(error)

            with patch.object(
                update_bookmarks.FFprobeClient,
                "has_video_stream",
                return_value=False,
            ):
                valid, error = update_bookmarks.validate_temporary_video(video, ffprobe="ffprobe")
            self.assertFalse(valid)
            self.assertIn("видеопоток", error)

            with patch.object(
                update_bookmarks.FFprobeClient,
                "has_video_stream",
                side_effect=update_bookmarks.ExternalToolError("ffprobe error"),
            ):
                valid, error = update_bookmarks.validate_temporary_video(video, ffprobe="ffprobe")
            self.assertFalse(valid)
            self.assertIn("ffprobe error", error)

    def test_rewrite_keeps_original_when_temporary_video_is_invalid(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            video = Path(temp_dir) / "Video [abcdefghijk].mp4"
            video.write_bytes(b"original")
            with patch.object(
                update_bookmarks.FFmpegClient,
                "run",
                return_value=CompletedProcess([], 0, "", ""),
            ):
                with self.assertRaisesRegex(RuntimeError, "не прошёл проверку"):
                    update_bookmarks.rewrite_embedded_chapters(
                        video,
                        [],
                        ffmpeg="ffmpeg",
                        timeout=1,
                    )
            self.assertEqual(video.read_bytes(), b"original")
            self.assertEqual(list(Path(temp_dir).glob("*.bookmarks.*.mp4")), [])

    def test_redownload_keeps_original_when_output_is_missing(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            video = Path(temp_dir) / "Video [abcdefghijk].mp4"
            video.write_bytes(b"original")
            with patch.object(
                update_bookmarks.YtDlpClient,
                "stream",
                return_value=CompletedProcess([], 0, "", ""),
            ):
                success, error = update_bookmarks.redownload_video(
                    video,
                    "abcdefghijk",
                    yt_dlp="yt-dlp",
                    cookies=None,
                    timeout=1,
                )
            self.assertFalse(success)
            self.assertIn("без выходного файла", error)
            self.assertEqual(video.read_bytes(), b"original")

    def test_main_apply_rewrites_when_confirmed(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            video = root / "Video [abcdefghijk].mp4"
            video.write_bytes(b"video")
            output = StringIO()
            previous_argv = sys.argv
            try:
                sys.argv = [
                    "update_bookmarks.py",
                    "--root",
                    str(root),
                    "--apply",
                    "--yes",
                ]

                def fake_run(cmd, **kwargs):
                    if cmd[0] == "ffprobe":
                        if "stream=codec_type" in cmd:
                            return CompletedProcess(cmd, 0, stdout="video\n", stderr="")
                        if "-show_entries" in cmd:
                            return CompletedProcess(
                                cmd,
                                0,
                                stdout="120.0\n",
                                stderr="",
                            )
                        return CompletedProcess(
                            cmd,
                            0,
                            stdout=json.dumps({"chapters": []}),
                            stderr="",
                        )
                    if cmd[0] == "yt-dlp":
                        return CompletedProcess(
                            cmd,
                            0,
                            stdout=json.dumps(
                                {
                                    "chapters": [
                                        {
                                            "start_time": 1.0,
                                            "end_time": 2.0,
                                            "title": "Intro",
                                        }
                                    ],
                                    "duration": 120.0,
                                }
                            ),
                            stderr="",
                        )
                    if cmd[0] == "ffmpeg":
                        self.assertIn("-f", cmd)
                        ffmetadata_index = cmd.index("-f")
                        self.assertEqual(cmd[ffmetadata_index + 1], "ffmetadata")
                        Path(cmd[-1]).write_bytes(b"updated")
                        return CompletedProcess(cmd, 0, stdout="", stderr="")
                    raise AssertionError(f"Unexpected command: {cmd}")

                with (
                    patch.object(subprocess, "run", side_effect=fake_run),
                    redirect_stdout(output),
                ):
                    result = update_bookmarks.main()
            finally:
                sys.argv = previous_argv

        self.assertEqual(result, 0)
        self.assertIn("[APPLY] 1/1 Video [abcdefghijk].mp4 (ETA ", output.getvalue())
        self.assertIn("[UPDATED] Video [abcdefghijk].mp4", output.getvalue())


if __name__ == "__main__":
    unittest.main()
