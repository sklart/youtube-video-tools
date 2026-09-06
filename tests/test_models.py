import unittest

from youtube_video_tools.models import SourceType, parse_source_ref


class SourceRefTests(unittest.TestCase):
    def test_parses_youtube_id(self):
        source = parse_source_ref("Video [abcdefghijk].mp4")
        self.assertIsNotNone(source)
        self.assertEqual(source.source_type, SourceType.YOUTUBE)
        self.assertEqual(source.source_id, "abcdefghijk")

    def test_ignores_descriptive_brackets(self):
        for filename in (
            "Video [Official].mp4",
            "Video [Full HD].mp4",
            "Video [Remix].mp4",
        ):
            self.assertIsNone(parse_source_ref(filename))

    def test_parses_only_rutube_uuid_shape(self):
        source = parse_source_ref("Video [8d1f2c3a4b5e6f7890a1b2c3d4e5f678].mp4")
        self.assertIsNotNone(source)
        self.assertEqual(source.source_type, SourceType.RUTUBE)
