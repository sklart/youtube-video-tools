"""Compatibility launcher for YouTube Video Tools.

The public implementation lives in :mod:`youtube_video_tools`.
"""

from youtube_video_tools.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
