"""Configuration loading and discovery for calibre-mcp.

Everything can be configured through environment variables, but the goal is
that a plain calibre installation "just works": the calibredb executable and
the library the calibre GUI is currently using are discovered automatically on
macOS, Windows and Linux.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path


class DiscoveryError(RuntimeError):
    """Raised when calibredb or the calibre library cannot be located."""


@dataclass(frozen=True)
class Settings:
    """Runtime configuration for one calibre library."""

    calibredb: Path
    library_path: Path
    inbox_dir: Path | None = None
    timeout_seconds: int = 300


def calibre_config_dir() -> Path:
    """Return the platform-specific directory holding calibre preferences."""
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Preferences" / "calibre"
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        base = Path(appdata) if appdata else Path.home() / "AppData" / "Roaming"
        return base / "calibre"
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg) if xdg else Path.home() / ".config"
    return base / "calibre"


def discover_library_path(config_dir: Path | None = None) -> Path:
    """Locate the calibre library to operate on.

    Resolution order:

    1. ``$CALIBRE_LIBRARY_PATH`` (explicit, always wins)
    2. ``library_usage_stats`` in ``gui.json`` (the GUI's most-used library)
    3. ``library_path`` in ``global.py.json`` (the default library)
    """
    env = os.environ.get("CALIBRE_LIBRARY_PATH")
    if env:
        return Path(env).expanduser()

    config_dir = config_dir or calibre_config_dir()

    gui_json = config_dir / "gui.json"
    if gui_json.exists():
        try:
            data = json.loads(gui_json.read_text(encoding="utf-8"))
            stats = data.get("library_usage_stats") or {}
            if stats:
                return Path(max(stats, key=lambda path: stats[path])).expanduser()
        except (json.JSONDecodeError, OSError, TypeError):
            pass  # fall through to the next source

    global_json = config_dir / "global.py.json"
    if global_json.exists():
        try:
            data = json.loads(global_json.read_text(encoding="utf-8"))
            path = data.get("library_path")
            if path:
                return Path(path).expanduser()
        except (json.JSONDecodeError, OSError):
            pass

    raise DiscoveryError(
        "No calibre library found. Set CALIBRE_LIBRARY_PATH to the library "
        f"directory, or make sure calibre preferences exist in {config_dir}."
    )


def discover_calibredb() -> Path:
    """Locate the calibredb executable.

    Resolution order:

    1. ``$CALIBREDB_PATH``
    2. ``calibredb`` on ``PATH``
    3. Platform defaults (macOS app bundle, Windows installer location)
    """
    candidates: list[Path] = []

    env = os.environ.get("CALIBREDB_PATH")
    if env:
        candidates.append(Path(env).expanduser())

    found = shutil.which("calibredb")
    if found:
        candidates.append(Path(found))

    if sys.platform == "darwin":
        candidates.append(Path("/Applications/calibre.app/Contents/MacOS/calibredb"))
    elif sys.platform == "win32":
        program_files = os.environ.get("PROGRAMFILES", r"C:\Program Files")
        candidates.append(Path(program_files) / "Calibre2" / "calibredb.exe")

    for candidate in candidates:
        if candidate.exists():
            return candidate

    raise DiscoveryError(
        "calibredb executable not found. Install calibre from "
        "https://calibre-ebook.com or set CALIBREDB_PATH."
    )


def load_settings() -> Settings:
    """Load settings from the environment and auto-discovery."""
    inbox = os.environ.get("CALIBRE_INBOX_DIR")
    timeout = int(os.environ.get("CALIBREDB_TIMEOUT", "300"))
    return Settings(
        calibredb=discover_calibredb(),
        library_path=discover_library_path(),
        inbox_dir=Path(inbox).expanduser() if inbox else None,
        timeout_seconds=timeout,
    )
