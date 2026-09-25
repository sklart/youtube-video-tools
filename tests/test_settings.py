from tests.support import IsolatedTestCase
from youtube_video_tools.settings import ConfigError, Settings


class SettingsTests(IsolatedTestCase):
    def test_invalid_types_ranges_and_values_are_diagnosed(self):
        cases = [
            ("bookmarks", "pause_seconds", "abc"),
            ("bookmarks", "pause_seconds", float("nan")),
            ("bookmarks", "pause_seconds", -1),
            ("bookmarks", "max_retries", True),
            ("bookmarks", "ffmpeg_timeout_seconds", 0),
            ("paths", "ffprobe", " "),
            ("subtitles", "languages", [""]),
            ("subtitles", "folders", "channel"),
            ("download", "sync_archive_before_download", "false"),
        ]
        for section, key, value in cases:
            with self.subTest(section=section, key=key, value=value):
                with self.assertRaisesRegex(ConfigError, f"{section}.{key}"):
                    Settings.from_mapping({section: {key: value}})

    def test_defaults_and_valid_overrides(self):
        settings = Settings.from_mapping(
            {"bookmarks": {"pause_seconds": 0}, "subtitles": {"languages": ["ru", "en"]}}
        )
        self.assertEqual(settings.bookmarks.pause_seconds, 0)
        self.assertEqual(settings.bookmarks.ffmpeg_timeout_seconds, 600)
        self.assertEqual(settings.subtitles.languages, ("ru", "en"))
