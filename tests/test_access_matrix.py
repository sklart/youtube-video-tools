from tests.support import IsolatedTestCase
from youtube_video_tools.access import AccessMode, command_access


class AccessMatrixTests(IsolatedTestCase):
    def test_command_access_matrix(self):
        cases = {
            "download": "ARCHIVE_WRITE",
            "subtitles": "ARCHIVE_WRITE",
            "bookmarks": "STATE_WRITE",
            "bookmarks --apply": "ARCHIVE_WRITE",
            "rename": "STATE_WRITE",
            "rename --apply": "ARCHIVE_WRITE",
            "resort": "STATE_WRITE",
            "resort --apply": "ARCHIVE_WRITE",
            "duplicates": "STATE_WRITE",
            "doctor": "STATE_WRITE",
            "inventory": "STATE_WRITE",
            "report": "READ_ONLY",
            "report --output file.csv": "STATE_WRITE",
            "report --o file.csv": "STATE_WRITE",
            "report --ou=file.csv": "STATE_WRITE",
            "archive-sync": "READ_ONLY",
            "archive-sync --apply": "ARCHIVE_WRITE",
            "archive-sync --ap": "ARCHIVE_WRITE",
            "dates": "READ_ONLY",
            "resolution": "READ_ONLY",
        }
        for command, expected in cases.items():
            name, *arguments = command.split()
            with self.subTest(command=command):
                self.assertIs(command_access(name, arguments), AccessMode[expected])
        for name in ("bookmarks", "rename", "resort", "archive-sync"):
            with self.subTest(command=name, option="--ap"):
                self.assertIs(command_access(name, ["--ap"]), AccessMode.ARCHIVE_WRITE)
