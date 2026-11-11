"""Где что лежит."""
import os
import sys
from pathlib import Path

APP = "PlateReader"


def base_dir():
    """Папка программы (рядом с .exe или исходники)."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", os.path.dirname(sys.executable)))
    return Path(__file__).resolve().parent.parent


def models_dir():
    d = base_dir() / "models"
    return d if d.is_dir() else None


def resource(name):
    return base_dir() / "resources" / name


def data_dir():
    root = os.environ.get("APPDATA") or os.path.expanduser("~/.config")
    d = Path(root) / APP
    d.mkdir(parents=True, exist_ok=True)
    return d


def settings_file():
    return data_dir() / "settings.json"


def log_file():
    return data_dir() / "log.txt"


def debug_dir():
    return str(data_dir() / "отладка")


def documents():
    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes
            buf = ctypes.create_unicode_buffer(wintypes.MAX_PATH)
            ctypes.windll.shell32.SHGetFolderPathW(None, 5, None, 0, buf)  # CSIDL_PERSONAL
            if buf.value:
                return Path(buf.value)
        except Exception:
            pass
    return Path.home() / "Documents"
