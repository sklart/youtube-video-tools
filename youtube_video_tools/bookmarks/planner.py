"""Bookmark planner helpers."""

from .constants import REMOVE_CATEGORY_SET


def chapter_title(chapter: dict) -> str:
    tags = chapter.get("tags") or {}
    value = chapter.get("title") or tags.get("title") or ""
    return str(value).strip()


def chapter_category(chapter: dict) -> str:
    tags = chapter.get("tags") or {}
    value = chapter.get("category") or tags.get("sponsorblock_category") or ""
    return str(value).strip()


def normalize_chapters(chapters: list[dict]) -> list[dict[str, object]]:
    normalized = []
    for chapter in chapters:
        try:
            start = round(float(chapter.get("start_time", 0.0)), 3)
            end = round(float(chapter.get("end_time", start)), 3)
        except (TypeError, ValueError):
            continue
        if end < start:
            end = start
        normalized.append(
            {
                "start_time": start,
                "end_time": end,
                "title": chapter_title(chapter),
                "category": chapter_category(chapter),
            }
        )
    return normalized


def extract_removed_segments(chapters: list[dict[str, object]]) -> list[dict[str, object]]:
    removed = []
    for chapter in chapters:
        category = str(chapter.get("category") or "").strip()
        if category not in REMOVE_CATEGORY_SET:
            continue
        try:
            start = round(float(chapter.get("start_time", 0.0)), 3)
            end = round(float(chapter.get("end_time", start)), 3)
        except (TypeError, ValueError):
            continue
        if end <= start:
            continue
        removed.append(
            {
                "start_time": start,
                "end_time": end,
                "title": str(chapter.get("title") or "").strip(),
                "category": category,
            }
        )
    return removed


def chapters_equal(
    current: list[dict[str, object]],
    target: list[dict[str, object]],
) -> bool:
    return current == target


def chapter_label(chapter: dict[str, object]) -> str:
    title = str(chapter.get("title") or "").strip()
    category = str(chapter.get("category") or "").strip()
    if title and category:
        return f"[{category}] {title}"
    if title:
        return title
    if category:
        return f"[{category}]"
    return "(без названия)"


def short_chapter_diff(
    current: list[dict[str, object]],
    target: list[dict[str, object]],
    *,
    limit: int = 3,
) -> list[str]:
    current_labels = [chapter_label(chapter) for chapter in current]
    target_labels = [chapter_label(chapter) for chapter in target]
    details: list[str] = []

    removed = [label for label in current_labels if label not in target_labels]
    added = [label for label in target_labels if label not in current_labels]

    for label in removed[:limit]:
        details.append(f"удалена: {label}")
    remaining = limit - len(details)
    if remaining > 0:
        for label in added[:remaining]:
            details.append(f"добавлена: {label}")

    if details:
        return details[:limit]

    if len(current) == len(target) and current != target:
        return ["обновлены таймкоды без изменения названий"]
    return []


def summarize_transition(
    current: list[dict[str, object]],
    target: list[dict[str, object]],
) -> str:
    if not current and target:
        base = f"добавить главы: {len(target)}"
    elif current and not target:
        base = f"удалить главы: {len(current)}"
    else:
        base = f"обновить главы: {len(current)} -> {len(target)}"
    details = short_chapter_diff(current, target)
    if details:
        return base + " [" + "; ".join(details) + "]"
    return base


def is_trimmed_local_video(
    local_duration: float | None,
    remote_duration: float | None,
    *,
    tolerance_seconds: float = 1.0,
) -> bool:
    if local_duration is None or remote_duration is None:
        return False
    return (remote_duration - local_duration) > tolerance_seconds
