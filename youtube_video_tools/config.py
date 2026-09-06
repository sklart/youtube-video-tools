"""Video Config implementation."""

import os
import tomllib
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = BASE_DIR / "config.toml"
CONFIG_ENV_NAME = "VIDEO_TOOLS_CONFIG"
ROOT_ENV_NAME = "VIDEO_TOOLS_ROOT"
PATH_ENV_NAMES = {
    "cookies": "YOUTUBE_COOKIES_FILE",
    "yt_dlp": "VIDEO_TOOLS_YT_DLP",
    "ffprobe": "VIDEO_TOOLS_FFPROBE",
    "ffmpeg": "VIDEO_TOOLS_FFMPEG",
}


def env_text(env_name: str | None) -> str | None:
    if not env_name:
        return None
    value = os.environ.get(env_name)
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def env_path(env_name: str | None) -> Path | None:
    value = env_text(env_name)
    return Path(value).expanduser() if value else None


def default_root_path() -> Path:
    return env_path(ROOT_ENV_NAME) or BASE_DIR


def default_config_path() -> Path:
    return env_path(CONFIG_ENV_NAME) or DEFAULT_CONFIG_PATH


def load_config(config_path: Path | None = None) -> dict[str, Any]:
    path = config_path or default_config_path()
    if not path.exists():
        return {}

    with path.open("rb") as config_file:
        return tomllib.load(config_file)


def config_section(config: dict[str, Any], name: str) -> dict[str, Any]:
    """Return a mapping section without trusting user-provided TOML types."""
    value = config.get(name, {})
    return value if isinstance(value, dict) else {}


def configured_path(
    config: dict[str, Any],
    key: str,
    *,
    env_name: str | None = None,
) -> Path | None:
    resolved_env_name = env_name or PATH_ENV_NAMES.get(key)
    configured_env_path = env_path(resolved_env_name)
    if configured_env_path:
        return configured_env_path

    value = config_section(config, "paths").get(key)
    return Path(value).expanduser() if value else None


def configured_command(
    config: dict[str, Any],
    key: str,
    default: str,
    *,
    env_name: str | None = None,
) -> str:
    resolved_env_name = env_name or PATH_ENV_NAMES.get(key)
    configured_env_value = env_text(resolved_env_name)
    if configured_env_value:
        return configured_env_value
    return str(config_section(config, "paths").get(key, default))
