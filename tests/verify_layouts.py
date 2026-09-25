"""Run isolated unit suites in fresh normal and relocated source trees."""

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def main() -> int:
    source = Path(__file__).resolve().parent.parent
    for relocated in (False, True):
        for user_config in (False, True):
            with tempfile.TemporaryDirectory() as directory:
                project = Path(directory) / ("archive/_video_tools" if relocated else "checkout")
                project.mkdir(parents=True)
                for name in ("youtube_video_tools", "tests"):
                    shutil.copytree(
                        source / name,
                        project / name,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
                    )
                if user_config:
                    (project / "config.toml").write_text(
                        '[paths]\nyt_dlp = "DO_NOT_EXECUTE"\n[bookmarks]\nmax_retries = "invalid"\n',
                        encoding="utf-8",
                    )
                environment = dict(os.environ)
                environment.update(
                    VIDEO_TOOLS_CONFIG=str(project / "config.toml"),
                    VIDEO_TOOLS_YT_DLP="DO_NOT_EXECUTE",
                    YOUTUBE_COOKIES_FILE="DO_NOT_READ",
                    VIDEO_TOOLS_ROOT="DO_NOT_SCAN",
                    PYTHONUTF8="1",
                )
                completed = subprocess.run(
                    [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"],
                    cwd=project,
                    env=environment,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=180,
                )
                label = f"relocated={relocated}, user_config={user_config}"
                print(f"{label}: {'PASS' if completed.returncode == 0 else 'FAIL'}", flush=True)
                if completed.returncode:
                    print(completed.stdout)
                    print(completed.stderr)
                    return completed.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
