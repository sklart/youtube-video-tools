import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

from tests.support import IsolatedTestCase
from youtube_video_tools import journal
from youtube_video_tools.commands import bookmarks
from youtube_video_tools.services.process import ExternalToolError, run, run_stream


class CrashCleanupTests(IsolatedTestCase):
    def test_captured_process_timeout_reaps_process_and_closes_pipes(self):
        children = []
        real_popen = subprocess.Popen

        def start(*args, **kwargs):
            child = real_popen(*args, **kwargs)
            children.append(child)
            return child

        with patch("youtube_video_tools.services.process.subprocess.Popen", side_effect=start):
            with self.assertRaises(ExternalToolError):
                run([sys.executable, "-c", "import time; time.sleep(60)"], timeout=0.2)
        self.assertIsNotNone(children[0].returncode)
        self.assertTrue(children[0].stdout.closed)
        self.assertTrue(children[0].stderr.closed)

    def test_partial_tail_can_be_read_and_repaired_on_append(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            journal.write_journal_event(root, {"event": "first"})
            path = journal.journal_path(root)
            with path.open("ab") as stream:
                stream.write(b'{"event": "\xd0')
            self.assertEqual(len(journal.read_journal(root)), 1)
            journal.write_journal_event(root, {"event": "second"})
            self.assertEqual([r["event"] for r in journal.read_journal(root)], ["first", "second"])
            path.write_bytes(b"{}\nBAD\n{}\n")
            with self.assertRaisesRegex(ValueError, "2"):
                journal.read_journal(root)
            with self.assertRaises(ValueError):
                journal.write_journal_event(root, {"event": "unsafe"})

    def test_stream_timeout_and_callback_failure_reap_child(self):
        real_popen = subprocess.Popen
        for error in ("timeout", "callback", "cancel"):
            children = []

            def start(*args, **kwargs):
                child = real_popen(*args, **kwargs)
                children.append(child)
                return child

            def callback(line):
                if error == "callback":
                    raise RuntimeError("callback failed")
                if error == "cancel":
                    raise KeyboardInterrupt

            with patch("youtube_video_tools.services.process.subprocess.Popen", side_effect=start):
                with self.assertRaises((ExternalToolError, RuntimeError, KeyboardInterrupt)):
                    run_stream(
                        [sys.executable, "-u", "-c", "import time; print('ready'); time.sleep(60)"],
                        timeout=0.3 if error == "timeout" else 10,
                        on_line=callback,
                    )
            self.assertTrue(all(child.poll() is not None for child in children))
            self.assertTrue(children[0].stdout.closed)

    def test_redownload_failure_cleans_partial_files(self):
        with tempfile.TemporaryDirectory() as directory:
            video = Path(directory) / "video.mp4"
            video.write_bytes(b"original")

            def fail(client, arguments, **kwargs):
                target = Path(arguments[arguments.index("-o") + 1])
                target.with_suffix(".mp4.part").write_bytes(b"partial")
                raise ExternalToolError("timeout")

            with patch.object(bookmarks.YtDlpClient, "stream", fail):
                success, error = bookmarks.redownload_video(
                    video, "abcdefghijk", yt_dlp="yt-dlp", cookies=None, timeout=1
                )
            self.assertFalse(success)
            self.assertEqual(error, "timeout")
            self.assertEqual(list(video.parent.iterdir()), [video])
            self.assertEqual(video.read_bytes(), b"original")
