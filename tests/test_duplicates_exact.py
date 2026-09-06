import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

import video_tools


class ExactDuplicateTests(unittest.TestCase):
    def test_finds_exact_duplicates_by_size_and_hash(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            channel_a = root / "A"
            channel_b = root / "B"
            channel_a.mkdir()
            channel_b.mkdir()

            duplicate_bytes = b"same-video"
            first = channel_a / "One [abcdefghijk].mp4"
            second = channel_b / "Two [lmnopqrst].mkv"
            unique = channel_b / "Three [uvwxyzabcd].webm"
            first.write_bytes(duplicate_bytes)
            second.write_bytes(duplicate_bytes)
            unique.write_bytes(b"other-video")

            groups = video_tools.find_duplicates.find_exact_duplicates(root)

            self.assertEqual(len(groups), 1)
            size_bytes, digest, paths = groups[0]
            self.assertEqual(size_bytes, len(duplicate_bytes))
            self.assertEqual(len(digest), 64)
            self.assertEqual(
                sorted(path.name for path in paths),
                sorted([first.name, second.name]),
            )

    def test_main_reports_when_no_duplicates_are_found(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "One [abcdefghijk].mp4").write_bytes(b"one")

            output = StringIO()
            previous_argv = video_tools.sys.argv
            try:
                video_tools.sys.argv = ["find_duplicates.py", "--root", str(root)]
                with redirect_stdout(output):
                    result = video_tools.find_duplicates.main()
            finally:
                video_tools.sys.argv = previous_argv

            self.assertEqual(result, 0)
            self.assertIn("[CHECK] Видеофайлов для проверки: 1", output.getvalue())
            self.assertIn("[OK] Дубли не найдены.", output.getvalue())


if __name__ == "__main__":
    unittest.main()
