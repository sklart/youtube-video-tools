"""Video Config implementation."""

import os
import tomllib
from pathlib import Path
from typing import Any

from .settings import ConfigError, Settings

PROJECT_DIR = Path(__file__).resolve().parent.parent
BASE_DIR = PROJECT_DIR.parent if PROJECT_DIR.name == "_video_tools" else PROJECT_DIR
DEFAULT_CONFIG_PATH = PROJECT_DIR / "config.toml"
CONFIG_ENV_NAME = "VIDEO_TOOLS_CONFIG"
ROOT_ENV_NAME = "VIDEO_TOOLS_ROOT"
PATH_ENV_NAMES = {
    "cookies": "YOUTUBE_COOKIES_FILE",
    "yt_dlp": "VIDEO_TOOLS_YT_DLP",
    "ffprobe": "VIDEO_TOOLS_FFPROBE",
    "ffmpeg": "VIDEO_TOOLS_FFMPEG",
}


def state_directory(root: Path) -> Path:
    """Keep the local archive state beside the relocated application."""
    if PROJECT_DIR != BASE_DIR and root.resolve() == BASE_DIR.resolve():
        return PROJECT_DIR / ".video-tools"
    return root / ".video-tools"


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

    try:
        with path.open("rb") as config_file:
            config = tomllib.load(config_file)
    except tomllib.TOMLDecodeError as error:
        raise ConfigError(f"Некорректный TOML в {path}: {error}") from error
    Settings.from_mapping(config)
    return config


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
