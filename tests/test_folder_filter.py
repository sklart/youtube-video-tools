import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import video_tools


class FolderFilterTests(unittest.TestCase):
    def test_resolves_masks_case_insensitively(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "Deep Look").mkdir()
            (root / "Deep Space").mkdir()
            (root / "Other").mkdir()

            selected = video_tools.resolve_folder_filter(
                root,
                ["deep*"],
                None,
                all_folders=False,
            )

            self.assertEqual(selected, ("Deep Look", "Deep Space"))

    def test_reads_utf8_folder_file_and_ignores_comments(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "Канал").mkdir()
            folder_file = root / "folders.txt"
            folder_file.write_text(
                "# выбор каналов\nКанал\n",
                encoding="utf-8-sig",
            )

            selected = video_tools.resolve_folder_filter(
                root,
                None,
                folder_file,
                all_folders=False,
            )

            self.assertEqual(selected, ("Канал",))

    def test_inventory_obeys_active_filter(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            selected = root / "Selected"
            ignored = root / "Ignored"
            selected.mkdir()
            ignored.mkdir()
            (selected / "One [abcdefghijk].mp4").write_bytes(b"1")
            (ignored / "Two [lmnopqrst].mp4").write_bytes(b"2")

            with patch.dict(
                video_tools.os.environ,
                {
                    video_tools.FOLDER_FILTER_ENV: json.dumps(
                        ["Selected"]
                    )
                },
            ):
                records = video_tools.inventory.collect_inventory(root)

            self.assertEqual(
                [record.relative_path for record in records],
                [str(Path("Selected") / "One [abcdefghijk].mp4")],
            )

    def test_rejects_filter_for_archive_sync(self):
        result = video_tools.execute_command(
            "archive-sync",
            ["--dry-run"],
            root=Path("."),
            config_path=Path("config.toml"),
            folders=("Selected",),
        )

        self.assertEqual(result, 2)


if __name__ == "__main__":
    unittest.main()
