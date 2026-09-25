"""Bookmark state helpers."""

import json
from pathlib import Path

from .. import config as video_config
from ..cache import save_cache
from ..console import get_console
from ..journal import relative_path
from ..state import atomic_write_json, atomic_write_text
from .planner import summarize_transition

BOOKMARKS_SCAN_STATE_FILE = "bookmarks-scan-state.json"
BOOKMARKS_PLAN_REPORT_FILE = "bookmarks-plan.txt"
SCAN_SCHEMA_VERSION = 1


def file_identity(root: Path, video_path: Path, video_id: str) -> dict:
    stat = video_path.stat()
    return {
        "video_id": video_id,
        "relative_path": relative_path(root, video_path),
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
    }


def identity_matches(root: Path, video_path: Path, video_id: str, record: dict) -> bool:
    try:
        return all(
            record.get(key) == value
            for key, value in file_identity(root, video_path, video_id).items()
        )
    except OSError:
        return False


def scan_state_path(root: Path) -> Path:
    return video_config.state_directory(root) / BOOKMARKS_SCAN_STATE_FILE


def plan_report_path(root: Path) -> Path:
    return video_config.state_directory(root) / BOOKMARKS_PLAN_REPORT_FILE


def load_scan_state(root: Path, *, apply_mode: bool) -> dict | None:
    path = scan_state_path(root)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    if (
        type(payload.get("schema_version")) is not int
        or payload.get("schema_version") != SCAN_SCHEMA_VERSION
    ):
        get_console().warning(
            "Старый или неизвестный формат плана SponsorBlock: повторное сканирование."
        )
        return None
    if payload.get("mode") != ("apply" if apply_mode else "dry-run"):
        return None
    records = payload.get("records")
    if not isinstance(records, dict):
        return None
    payload["records"] = {
        str(key): value for key, value in records.items() if isinstance(value, dict)
    }
    return payload


def save_scan_state(root: Path, state: dict) -> None:
    path = scan_state_path(root)
    atomic_write_json(path, state)


def clear_scan_state(root: Path) -> None:
    path = scan_state_path(root)
    try:
        path.unlink()
    except FileNotFoundError:
        return


def write_plan_report(
    root: Path,
    *,
    apply_mode: bool,
    up_to_date: int,
    planned: list[tuple[Path, list[dict[str, object]], list[dict[str, object]]]],
    pending_redownloads: list[tuple[Path, str, str, dict[str, object]]],
    trimmed: int,
    retry_later: int,
    failed: int,
    private_or_unavailable: int,
    other_errors: int,
) -> Path:
    path = plan_report_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "SponsorBlock bookmarks plan report",
        f"mode: {'apply' if apply_mode else 'dry-run'}",
        f"up_to_date: {up_to_date}",
        f"planned_updates: {len(planned)}",
        f"pending_redownloads: {len(pending_redownloads)}",
        f"trimmed_detected: {trimmed}",
        f"retry_later: {retry_later}",
        f"private_or_unavailable: {private_or_unavailable}",
        f"other_errors: {other_errors}",
        f"failed: {failed}",
        "",
        "Planned chapter updates:",
    ]
    if planned:
        for video_path, current, target in planned:
            lines.append(
                f"- {relative_path(root, video_path)} :: {summarize_transition(current, target)}"
            )
    else:
        lines.append("- none")
    lines.extend(["", "Pending redownloads:"])
    if pending_redownloads:
        for _, _, relative, _ in pending_redownloads:
            lines.append(f"- {relative}")
    else:
        lines.append("- none")
    atomic_write_text(path, "\n".join(lines) + "\n")
    return path


def state_record_for_scan(
    *,
    video_id: str,
    result: str,
    current: list[dict[str, object]] | None = None,
    target: list[dict[str, object]] | None = None,
    remote_info: dict[str, object] | None = None,
    error: str | None = None,
    identity: dict | None = None,
) -> dict[str, object]:
    record: dict[str, object] = {
        **(identity or {}),
        "video_id": video_id,
        "result": result,
    }
    if current is not None:
        record["current"] = current
    if target is not None:
        record["target"] = target
    if remote_info is not None:
        record["remote_info"] = remote_info
    if error:
        record["error"] = error
    if result == "planned":
        record["apply_result"] = "pending"
    if result == "trimmed_redownload":
        record["redownload_result"] = "pending"
    return record


def rebuild_scan_results(
    root: Path,
    videos: list[tuple[Path, str]],
    state: dict | None,
    *,
    apply_mode: bool,
) -> tuple[
    dict[str, dict[str, object]],
    list[tuple[Path, list[dict[str, object]], list[dict[str, object]]]],
    list[tuple[Path, str, str, dict[str, object]]],
    int,
    int,
    int,
    int,
    int,
    str | None,
]:
    records = (
        state.get("records", {})
        if state and state.get("schema_version") == SCAN_SCHEMA_VERSION
        else {}
    )
    current_relatives = {relative_path(root, path): (path, video_id) for path, video_id in videos}
    filtered_records = {
        relative: record
        for relative, record in records.items()
        if relative in current_relatives
        and isinstance(record, dict)
        and identity_matches(root, *current_relatives[relative], record)
    }
    if len(filtered_records) < len(records):
        get_console().warning(
            "Сохранённый план устарел для изменённых файлов: они будут проверены заново."
        )
    processed_records = {
        relative: record
        for relative, record in filtered_records.items()
        if str(record.get("result") or "") != "retry_later"
    }
    planned: list[tuple[Path, list[dict[str, object]], list[dict[str, object]]]] = []
    pending_redownloads: list[tuple[Path, str, str, dict[str, object]]] = []
    up_to_date = failed = trimmed = retry_later = private_or_unavailable = other_errors = 0
    trimmed_policy: str | None = None
    path_map = {
        relative_path(root, video_path): (video_path, video_id) for video_path, video_id in videos
    }
    for relative, record in filtered_records.items():
        video_path, video_id = path_map[relative]
        result = str(record.get("result") or "")
        if result == "up_to_date":
            up_to_date += 1
        elif result == "error":
            failed += 1
            error_kind = str(record.get("error_kind") or "other_error")
            if error_kind == "private_or_unavailable":
                private_or_unavailable += 1
            else:
                other_errors += 1
        elif result == "retry_later":
            retry_later += 1
        elif result == "planned":
            current = record.get("current")
            target = record.get("target")
            if isinstance(current, list) and isinstance(target, list):
                if record.get("apply_result") != "success":
                    planned.append((video_path, current, target))
        elif result == "trimmed":
            trimmed += 1
        elif result == "trimmed_skip":
            trimmed += 1
            if apply_mode:
                policy = str(record.get("trimmed_policy") or "")
                if policy == "skip_all":
                    trimmed_policy = "skip_all"
        elif result == "trimmed_redownload":
            trimmed += 1
            if apply_mode and record.get("redownload_result") != "success":
                remote_info = record.get("remote_info")
                if isinstance(remote_info, dict):
                    pending_redownloads.append((video_path, video_id, relative, remote_info))
                policy = str(record.get("trimmed_policy") or "")
                if policy == "redownload_all":
                    trimmed_policy = "redownload_all"
    return (
        processed_records,
        planned,
        pending_redownloads,
        up_to_date,
        failed,
        trimmed,
        retry_later,
        private_or_unavailable,
        other_errors,
        trimmed_policy,
    )


def persist_scan_progress(
    root: Path,
    state: dict,
    metadata_cache_state: dict,
) -> None:
    save_scan_state(root, state)
    save_cache(root, metadata_cache_state)
