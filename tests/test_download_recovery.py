import json
import tempfile
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import Mock, patch

from tests.support import IsolatedTestCase
from tests.test_progress_renderer import Tty
from youtube_video_tools import download_recovery as recovery
from youtube_video_tools.bookmarks.rewriter import FFprobeClient
from youtube_video_tools.commands import download
from youtube_video_tools.console import Console, use_console
from youtube_video_tools.services.process import ProcessResult
from youtube_video_tools.services.yt_dlp import YtDlpClient

VIDEO_ID = "abcdefghijk"
ERROR = "ERROR: Postprocessing: " + recovery.MISMATCH


def event(path, **info):
    return "VT_POSTPROCESS:" + json.dumps(
        {
            "info": {"id": VIDEO_ID, "filepath": str(path), **info},
            "progress": {"status": "started", "postprocessor": "ModifyChapters"},
        }
    )


class DownloadRecoveryTests(IsolatedTestCase):
    def setUp(self):
        super().setUp()
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.video = self.root / f"test [{VIDEO_ID}].mp4"
        self.video.write_bytes(b"original")
        self.archive = self.root / "yt-dlp-archive.txt"

    def queue(self):
        queue = recovery.RecoveryQueue(self.root)
        queue.on_line(event(self.video))
        queue.on_line(ERROR)
        return queue

    def recover(self, queue, input_fn=lambda _: "r"):
        return recovery.recover(
            queue,
            archive=self.archive,
            yt_dlp="yt-dlp",
            cookies=self.root / "cookies.txt",
            ffprobe="ffprobe",
            timeout=30,
            input_fn=input_fn,
        )

    def test_exact_candidate_and_duplicate_errors(self):
        queue = self.queue()
        queue.on_line(event(self.video))
        queue.on_line(ERROR)
        self.assertEqual(list(queue.pending), [self.video])
        self.assertEqual(queue.pending[self.video].video_id, VIDEO_ID)

    def test_never_inherits_previous_video(self):
        for separator in (
            'VT_START:{"id":"other"}',
            'VT_COMPLETE:{"id":"other"}',
            "VT_POSTPROCESS:{bad",
            "VT_UNKNOWN:{}",
            "[youtube] other: Extracting",
            "ERROR: other failure",
            'VT_POSTPROCESS:{"info":{},"progress":{"status":"finished"}}',
        ):
            with self.subTest(separator=separator):
                queue = recovery.RecoveryQueue(self.root)
                queue.on_line(event(self.video))
                queue.on_line(separator)
                queue.on_line(ERROR)
                self.assertFalse(queue.pending)
                self.assertEqual(queue.unresolved, 1)

    def test_rejects_missing_external_relative_and_mismatched_paths(self):
        with tempfile.TemporaryDirectory() as outside:
            external = Path(outside) / self.video.name
            external.write_bytes(b"outside")
            for path, video_id in (
                (external, VIDEO_ID),
                (self.video.name, VIDEO_ID),
                ("", VIDEO_ID),
                (self.root / "missing.mp4", VIDEO_ID),
                (self.video, "wrong"),
                (self.video, "01234567890"),
            ):
                queue = recovery.RecoveryQueue(self.root)
                queue.on_line(event(path, id=video_id))
                queue.on_line(ERROR)
                self.assertFalse(queue.pending)

    def test_choices_eof_and_noninteractive(self):
        for value, expected in (
            ("r", "redownload"),
            ("S", "skip"),
            ("Ra", "redownload_all"),
            ("sA", "skip_all"),
            ("", "skip"),
        ):
            self.assertEqual(recovery.choose_action(self.video, input_fn=lambda _: value), expected)
        self.assertEqual(
            recovery.choose_action(self.video, input_fn=Mock(side_effect=EOFError)), "skip_all"
        )
        with patch("sys.stdin", StringIO()), patch("builtins.input") as prompt:
            self.assertEqual(recovery.choose_action(self.video), "skip_all")
            prompt.assert_not_called()

    def test_all_choices_apply_to_rest_of_queue(self):
        second = self.root / f"second [{VIDEO_ID}].mp4"
        second.write_bytes(b"original")
        for answer, expected_calls in (("RA", 2), ("SA", 0)):
            queue = self.queue()
            queue.on_line(event(second))
            queue.on_line(ERROR)
            prompt = Mock(return_value=answer)
            with patch.object(
                recovery, "redownload_video", return_value=(False, "failure")
            ) as worker:
                self.recover(queue, prompt)
            self.assertEqual(worker.call_count, expected_calls)
            prompt.assert_called_once()

    def test_changed_before_download_is_rejected(self):
        queue = self.queue()
        self.video.write_bytes(b"changed original")
        with patch.object(recovery, "redownload_video") as worker:
            self.assertEqual(self.recover(queue), (0, 0, 1))
            worker.assert_not_called()
        self.assertFalse(self.archive.exists())

    def test_symlink_is_rejected(self):
        link = self.root / f"link [{VIDEO_ID}].mp4"
        try:
            link.symlink_to(self.video)
        except OSError:
            self.skipTest("symlink creation is unavailable")
        queue = recovery.RecoveryQueue(self.root)
        queue.on_line(event(link))
        queue.on_line(ERROR)
        self.assertFalse(queue.pending)

    def test_archive_write_failure_is_reported(self):
        with (
            patch.object(recovery, "redownload_video", return_value=(True, None)),
            patch.object(recovery, "atomic_write_text", side_effect=OSError("archive denied")),
            redirect_stdout(StringIO()) as output,
        ):
            self.assertEqual(self.recover(self.queue()), (0, 0, 1))
        self.assertIn("archive denied", output.getvalue())
        self.assertFalse(self.archive.exists())

    def test_real_replacement_lifecycle_and_failures(self):
        for outcome in ("success", "download", "validation", "replace", "changed"):
            for tty in (False, True):
                for quiet in (False, True):
                    with self.subTest(outcome=outcome, tty=tty, quiet=quiet):
                        self.video.write_bytes(b"original")
                        self.archive.unlink(missing_ok=True)
                        queue = self.queue()
                        output = Tty() if tty else StringIO()
                        real_replace = Path.replace

                        def stream(client, arguments, **kwargs):
                            self.assertFalse(self.archive.exists())
                            Path(arguments[arguments.index("-o") + 1]).write_bytes(b"new video")
                            kwargs["on_line"]('VT_COMPLETE:{"id":"abcdefghijk"}')
                            if outcome == "changed":
                                self.video.write_bytes(b"user edit")
                            return ProcessResult(("yt-dlp",), int(outcome == "download"), "", "")

                        def replace(source, target):
                            self.assertFalse(self.archive.exists())
                            self.assertNotIn("✓", output.getvalue())
                            if outcome == "replace":
                                raise OSError("replace failed")
                            return real_replace(source, target)

                        with (
                            patch.dict("os.environ", {"CI": "", "TERM": "xterm"}),
                            redirect_stdout(output),
                            use_console(Console(quiet=quiet)),
                            patch.object(YtDlpClient, "stream", stream),
                            patch.object(
                                FFprobeClient,
                                "has_video_stream",
                                return_value=outcome != "validation",
                            ),
                            patch.object(Path, "replace", replace),
                        ):
                            counts = self.recover(queue)
                        if outcome == "success":
                            self.assertEqual(counts, (1, 0, 0))
                            self.assertEqual(self.video.read_bytes(), b"new video")
                            self.assertEqual(
                                self.archive.read_text().splitlines(), [f"youtube {VIDEO_ID}"]
                            )
                            recovery.record_download(self.archive, VIDEO_ID)
                            self.assertEqual(len(self.archive.read_text().splitlines()), 1)
                            self.assertEqual(output.getvalue().count("✓"), 0 if quiet else 1)
                        else:
                            self.assertEqual(counts, (0, 0, 1))
                            self.assertFalse(self.archive.exists())
                            self.assertEqual(
                                self.video.read_bytes(),
                                b"user edit" if outcome == "changed" else b"original",
                            )
                            self.assertNotIn("✓", output.getvalue())
                            self.assertIn("[ERROR]", output.getvalue())
                        self.assertFalse(list(self.root.glob("*.redownload.*")))
                        if not tty:
                            self.assertNotIn("\r", output.getvalue())

    def test_download_waits_for_child_and_keeps_original_error_code(self):
        cookies = self.root / "cookies.txt"
        cookies.touch()
        finished = []

        def stream(client, arguments, **kwargs):
            kwargs["on_line"](event(self.video))
            kwargs["on_line"](ERROR)
            kwargs["on_line"]("ERROR: unrelated download failure")
            finished.append(True)
            return ProcessResult(("yt-dlp",), 1, "", "")

        def recover(queue, **kwargs):
            self.assertEqual(finished, [True])
            self.assertEqual(list(queue.pending), [self.video])
            return 1, 0, 0

        with (
            patch("sys.argv", ["download", "--root", str(self.root)]),
            patch.object(download, "load_config", return_value={}),
            patch.object(download, "configured_path", return_value=cookies),
            patch.object(download, "synchronize_archive", return_value=0),
            patch.object(YtDlpClient, "stream", stream),
            patch.object(download, "recover", side_effect=recover),
        ):
            self.assertEqual(download.main(), 1)
