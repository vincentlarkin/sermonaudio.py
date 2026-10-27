"""
Settings live in ~/.config/sapy/config.json (or $XDG_CONFIG_HOME/sapy).

Keys:
  download_dir  where downloads go when -o isn't given (default: current dir)
  api_key       your own SermonAudio API key; overrides the site key
  quality       default audio quality, "low" or "high"
  video_quality default video quality: 360p, 720p, 1080p or best
  workers       parallel downloads for bulk jobs
  show_logs / theme_mode / gui_format   GUI preferences
"""

import json
import os
from pathlib import Path

DEFAULTS = {
    "download_dir": "",
    "api_key": "",
    "quality": "high",
    "video_quality": "720p",
    "workers": 3,
    "show_logs": True,
    "theme_mode": "dark",
    "gui_format": "audio:high",
}


def config_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    if os.name == "nt":
        base = os.environ.get("APPDATA") or base
    return Path(base) / "sapy"


def config_path() -> Path:
    return config_dir() / "config.json"


def load_config() -> dict:
    merged = DEFAULTS.copy()
    try:
        with open(config_path(), encoding="utf-8") as f:
            merged.update(json.load(f))
    except FileNotFoundError:
        pass
    except (OSError, ValueError) as e:
        print(f"[config] Could not read {config_path()}: {e}")
    return merged


def save_config(key: str, value) -> None:
    config = load_config()
    config[key] = value
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)
    os.replace(tmp, path)


def download_dir(config: dict = None) -> str:
    config = config or load_config()
    d = config.get("download_dir") or ""
    return os.path.expanduser(d) if d and os.path.isdir(os.path.expanduser(d)) else os.getcwd()
