"""Find Duplicates implementation."""

import argparse
import hashlib
import os
import re
from collections import defaultdict
from difflib import SequenceMatcher
from pathlib import Path

from .. import cache as video_metadata_cache
from .. import config as video_config
from ..console import console_print as print
from ..core import path_selected
from ..models import SourceType, parse_source_ref
from ..services.process import ExternalToolError
from ..services.yt_dlp import YtDlpClient
from . import inventory

BASE_DIR = video_config.BASE_DIR
configured_command = video_config.configured_command
configured_path = video_config.configured_path
VIDEO_EXTENSIONS = inventory.VIDEO_EXTENSIONS
load_config = video_config.load_config
get_cached_field = video_metadata_cache.get_field
set_cached_field = video_metadata_cache.set_field


def get_youtube_title(
    video_id: str,
    *,
    cookies_file,
    yt_dlp: str,
    metadata_cache: dict | None = None,
) -> str | None:
    if metadata_cache:
        cached = get_cached_field(metadata_cache, "youtube", video_id, "title")
        if cached:
            return cached
    command = [
        "--skip-download",
        "--print",
        "%(title)s",
        "--encoding",
        "utf-8",
        f"https://www.youtube.com/watch?v={video_id}",
    ]
    try:
        result = YtDlpClient(yt_dlp, cookies_file=cookies_file).run(command, timeout=30)
    except ExternalToolError as error:
        print(f"[ERROR] {error}")
        return None

    if result.returncode == 0:
        title = result.stdout.strip()
        if metadata_cache and title:
            set_cached_field(metadata_cache, "youtube", video_id, "title", title)
        return title or None

    message = result.stderr.strip().splitlines()
    print(f"[ERROR] {message[-1] if message else 'Ошибка yt-dlp'}")
    return None


def highlight(text: str, color: str = "green") -> str:
    """Keep duplicate reports readable in redirected and plain terminals."""
    return text


def clean_filename(name: str) -> str:
    name = os.path.splitext(name)[0]
    name = re.sub(r"\[[A-Za-z0-9_-]{11}\]", "", name)
    name = re.sub(r"\[.*?\]", "", name)
    return name.strip().lower()


def iter_video_files(root: Path) -> list[Path]:
    files = []
    for path in root.rglob("*"):
        if path.is_file() and path_selected(root, path) and path.suffix.lower() in VIDEO_EXTENSIONS:
            files.append(path)
    return sorted(files, key=lambda path: str(path).casefold())


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while True:
            chunk = source.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def find_exact_duplicates(
    root: Path, *, video_files: list[Path] | None = None
) -> list[tuple[int, str, list[Path]]]:
    size_groups: dict[int, list[Path]] = defaultdict(list)
    for path in video_files if video_files is not None else iter_video_files(root):
        size_groups[path.stat().st_size].append(path)

    duplicates = []
    for size_bytes, paths in sorted(size_groups.items()):
        if len(paths) <= 1:
            continue
        hash_groups: dict[str, list[Path]] = defaultdict(list)
        for path in paths:
            hash_groups[sha256_file(path)].append(path)
        for digest, matched_paths in sorted(hash_groups.items()):
            if len(matched_paths) > 1:
                duplicates.append((size_bytes, digest, matched_paths))
    return duplicates


def print_exact_duplicates(root: Path, *, video_files: list[Path] | None = None) -> int:
    duplicates = find_exact_duplicates(root, video_files=video_files)
    for size_bytes, digest, paths in duplicates:
        print(f"\n[EXACT] файлов: {len(paths)}; размер: {size_bytes}; sha256: {digest}")
        for path in paths:
            print(f"  {path.relative_to(root)}")
    return len(duplicates)


def find_duplicate_ids(
    root,
    *,
    cookies_file=None,
    yt_dlp="yt-dlp",
    metadata_cache: dict | None = None,
    video_files: list[Path] | None = None,
) -> int:
    folder = str(root)
    id_to_files: dict[str, list[str]] = defaultdict(list)

    for path in video_files if video_files is not None else iter_video_files(Path(folder)):
        source = parse_source_ref(path.name)
        if source and source.source_type is SourceType.YOUTUBE:
            id_to_files[source.source_id].append(str(path))

    duplicate_id_count = 0
    for video_id, files in id_to_files.items():
        if len(files) <= 1:
            continue

        duplicate_id_count += 1

        print(f"\nID: {video_id} - найдено файлов: {len(files)}")
        youtube_title = get_youtube_title(
            video_id,
            cookies_file=cookies_file,
            yt_dlp=yt_dlp,
            metadata_cache=metadata_cache,
        )
        best_match = None
        if youtube_title:
            best_match = max(
                files,
                key=lambda file: SequenceMatcher(
                    None,
                    clean_filename(os.path.basename(file)),
                    youtube_title.lower(),
                ).ratio(),
            )

        for file in files:
            relative_path = os.path.relpath(file, folder)
            if file == best_match:
                print("  " + highlight(relative_path) + f'  <- ближе всего к "{youtube_title}"')
            else:
                print(f"  {relative_path}")
    return duplicate_id_count


def main() -> int:
    parser = argparse.ArgumentParser(description="Ищет повторяющиеся ID видео.")
    parser.add_argument("--root", type=Path, default=BASE_DIR)
    args = parser.parse_args()
    config = load_config()
    root = args.root.resolve()
    video_files = iter_video_files(root)
    print(f"[CHECK] Видеофайлов для проверки: {len(video_files)}")
    metadata_cache_state = video_metadata_cache.load_cache(root)
    exact_count = print_exact_duplicates(root, video_files=video_files)
    duplicate_id_count = find_duplicate_ids(
        root,
        cookies_file=configured_path(config, "cookies", env_name="YOUTUBE_COOKIES_FILE"),
        yt_dlp=configured_command(config, "yt_dlp", "yt-dlp"),
        metadata_cache=metadata_cache_state,
        video_files=video_files,
    )
    video_metadata_cache.save_cache(root, metadata_cache_state)
    print(
        "\n[SUMMARY] "
        f"Точных групп дублей: {exact_count}; "
        f"повторяющихся YouTube ID: {duplicate_id_count}"
    )
    if not exact_count and not duplicate_id_count:
        print("[OK] Дубли не найдены.")
    return 0
