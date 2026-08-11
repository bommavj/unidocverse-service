import os
import platform
from pathlib import Path


def get_storage_dir():
    system = platform.system()

    if system == "Darwin":  # macOS
        base = Path.home() / "Library" / "Application Support" / "UniDocVerse"
    elif system == "Windows":
        base = Path(os.getenv("APPDATA")) / "UniDocVerse"
    else:  # Linux and others
        base = Path.home() / ".local" / "share" / "UniDocVerse"

    uploads = base / "uploads"
    uploads.mkdir(parents=True, exist_ok=True)
    return uploads


UPLOADS_DIR = get_storage_dir()
