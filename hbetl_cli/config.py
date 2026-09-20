"""Local configuration and token storage for the API client."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_API_URL = "http://localhost:5002"
ENV_API_URL = "HBETL_API_URL"
ENV_TOKEN = "HBETL_TOKEN"
ENV_CONFIG = "HBETL_CONFIG"


@dataclass
class Config:
    api_url: str = DEFAULT_API_URL
    token: str | None = None


def default_config_path() -> Path:
    configured = os.environ.get(ENV_CONFIG)
    if configured:
        return Path(configured).expanduser()
    config_home = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return config_home / "hbetl" / "config.json"


def load_config(path: Path | None = None) -> Config:
    config_path = path or default_config_path()
    values: dict[str, Any] = {}
    if config_path.exists():
        try:
            values = json.loads(config_path.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"Cannot read config file {config_path}: {exc}") from exc
        if not isinstance(values, dict):
            raise ValueError(f"Config file {config_path} must contain a JSON object")
    api_url = os.environ.get(ENV_API_URL, values.get("api_url", DEFAULT_API_URL))
    token = os.environ.get(ENV_TOKEN, values.get("token"))
    return Config(api_url=str(api_url).rstrip("/"), token=token)


def save_config(config: Config, path: Path | None = None) -> Path:
    config_path = path or default_config_path()
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps({"api_url": config.api_url, "token": config.token}, indent=2) + "\n")
    config_path.chmod(0o600)
    return config_path


def clear_token(config: Config, path: Path | None = None) -> Path:
    config.token = None
    return save_config(config, path)
