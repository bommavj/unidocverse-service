# license_paths.py
import os
import platform
from pathlib import Path

APP_NAME = "UniDocVerse"


def get_license_dir() -> Path:
    system = platform.system()

    if system == "Darwin":  # macOS
        base = Path.home() / "Library" / "Application Support" / APP_NAME / "license"
    elif system == "Windows":
        base = Path(os.getenv("APPDATA")) / APP_NAME / "license"
    else:  # Linux
        base = Path.home() / ".config" / APP_NAME.lower() / "license"

    base.mkdir(parents=True, exist_ok=True)
    return base


def get_license_path() -> Path:
    return get_license_dir() / "license.udv.json"
