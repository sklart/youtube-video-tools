import csv
import tempfile
import unittest
from pathlib import Path

from youtube_video_tools.commands import inventory

collect_inventory = inventory.collect_inventory
extract_date = inventory.extract_date
extract_source = inventory.extract_source
write_inventory = inventory.write_inventory


class InventoryTests(unittest.TestCase):
    def test_collects_metadata_and_related_subtitles(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            channel = root / "Channel"
            channel.mkdir()
            video = channel / "Title_11.06.2026 [abcdefghijk].mp4"
            subtitle = channel / "Title_11.06.2026 [abcdefghijk].ru.vtt"
            unrelated = channel / "Other.ru.vtt"
            video.write_bytes(b"video-data")
            subtitle.write_text("subtitle", encoding="utf-8")
            unrelated.write_text("other", encoding="utf-8")

            records = collect_inventory(root)

            self.assertEqual(len(records), 1)
            record = records[0]
            self.assertEqual(record.folder, "Channel")
            self.assertEqual(record.size_bytes, len(b"video-data"))
            self.assertEqual(record.source_type, "youtube")
            self.assertEqual(record.source_id, "abcdefghijk")
            self.assertEqual(record.upload_date, "11.06.2026")
            self.assertEqual(record.subtitle_count, 1)
            self.assertEqual(record.subtitles, subtitle.name)

    def test_writes_excel_friendly_csv(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            channel = root / "Канал"
            channel.mkdir()
            (channel / "Видео [abcdefghijk].mkv").write_bytes(b"123")
            records = collect_inventory(root)
            output = root / "inventory.csv"

            write_inventory(output, records)

            self.assertTrue(output.read_bytes().startswith(b"\xef\xbb\xbf"))
            with output.open(encoding="utf-8-sig", newline="") as csv_file:
                rows = list(csv.DictReader(csv_file, delimiter=";"))
            self.assertEqual(rows[0]["folder"], "Канал")
            self.assertEqual(rows[0]["extension"], ".mkv")

    def test_extractors_ignore_missing_metadata(self):
        self.assertEqual(extract_source("Plain video.mp4"), ("", ""))
        self.assertEqual(extract_date("Plain video.mp4"), "")

    def test_extract_date_supports_multi_suffix_names(self):
        self.assertEqual(
            extract_date("Title_11.06.2026 [abcdefghijk].ru.vtt"),
            "11.06.2026",
        )


if __name__ == "__main__":
    unittest.main()
