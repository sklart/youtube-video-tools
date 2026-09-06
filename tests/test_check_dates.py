import tempfile
import unittest
from pathlib import Path

import video_tools


class CheckDatesTests(unittest.TestCase):
    def test_checks_only_video_files_and_separates_invalid_dates(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            valid = root / "Valid_11.06.2026 [abcdefghijk].mp4"
            missing = root / "Missing [abcdefghijk].mkv"
            invalid = root / "Invalid_31.02.2026 [abcdefghijk].webm"
            subtitle = root / "Subtitle [abcdefghijk].ru.vtt"
            script = root / "script.py"
            for path in (valid, missing, invalid, subtitle, script):
                path.write_bytes(b"x")

            checked, missing_dates, invalid_dates = (
                video_tools.check_date.inspect_video_dates(root)
            )

            self.assertEqual(checked, 3)
            self.assertEqual(missing_dates, [missing])
            self.assertEqual(invalid_dates, [(invalid, "31.02.2026")])


if __name__ == "__main__":
    unittest.main()
