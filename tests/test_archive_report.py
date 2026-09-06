import csv
import tempfile
import unittest
from pathlib import Path

from youtube_video_tools.commands import report as archive_report


class ArchiveReportTests(unittest.TestCase):
    def test_reports_missing_metadata_without_reading_video(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            channel = root / "Channel"
            channel.mkdir()

            complete = channel / "Complete_11.06.2026 [abcdefghijk].mp4"
            complete.write_bytes(b"video")
            complete.with_name("Complete_11.06.2026 [abcdefghijk].ru.vtt").write_text(
                "subtitle", encoding="utf-8"
            )

            (channel / "No metadata.mkv").write_bytes(b"video")
            (root / "Root_31.02.2026 [lmnopqrstuv].webm").write_bytes(b"video")

            checked, issues = archive_report.inspect_archive(root)

            self.assertEqual(checked, 3)
            self.assertEqual(len(issues), 2)
            by_name = {Path(issue.relative_path).name: issue for issue in issues}
            missing = by_name["No metadata.mkv"]
            self.assertTrue(missing.missing_id)
            self.assertTrue(missing.missing_date)
            self.assertFalse(missing.missing_author)
            self.assertTrue(missing.missing_subtitles)

            invalid = by_name["Root_31.02.2026 [lmnopqrstuv].webm"]
            self.assertFalse(invalid.missing_id)
            self.assertFalse(invalid.missing_date)
            self.assertTrue(invalid.invalid_date)
            self.assertTrue(invalid.missing_author)

    def test_writes_csv_report(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            issue = archive_report.ReportIssue(
                relative_path="video.mp4",
                missing_id=True,
                missing_date=True,
                invalid_date=False,
                missing_author=True,
                missing_subtitles=True,
            )
            output = root / "report.csv"

            archive_report.write_report(output, [issue])

            with output.open(encoding="utf-8-sig", newline="") as csv_file:
                rows = list(csv.DictReader(csv_file, delimiter=";"))
            self.assertEqual(rows[0]["relative_path"], "video.mp4")
            self.assertEqual(rows[0]["missing_id"], "1")
            self.assertIn("нет ID", rows[0]["reasons"])


if __name__ == "__main__":
    unittest.main()
