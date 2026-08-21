"""Unit tests for configuration loading and discovery."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from calibre_mcp.config import (
    DiscoveryError,
    calibre_config_dir,
    discover_calibredb,
    discover_library_path,
    load_settings,
)


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def test_env_library_path_wins(tmp_path, monkeypatch):
    target = tmp_path / "my-books"
    monkeypatch.setenv("CALIBRE_LIBRARY_PATH", str(target))
    assert discover_library_path(tmp_path / "no-config") == target


def test_gui_json_most_used_library_wins(tmp_path, monkeypatch):
    monkeypatch.delenv("CALIBRE_LIBRARY_PATH", raising=False)
    config_dir = tmp_path / "calibre"
    write_json(
        config_dir / "gui.json",
        {
            "library_usage_stats": {
                str(tmp_path / "rarely-used"): 3,
                str(tmp_path / "current"): 42,
            }
        },
    )
    assert discover_library_path(config_dir) == tmp_path / "current"


def test_fallback_to_global_py_json(tmp_path, monkeypatch):
    monkeypatch.delenv("CALIBRE_LIBRARY_PATH", raising=False)
    config_dir = tmp_path / "calibre"
    write_json(config_dir / "global.py.json", {"library_path": str(tmp_path / "default-lib")})
    assert discover_library_path(config_dir) == tmp_path / "default-lib"


def test_broken_gui_json_falls_back(tmp_path, monkeypatch):
    monkeypatch.delenv("CALIBRE_LIBRARY_PATH", raising=False)
    config_dir = tmp_path / "calibre"
    config_dir.mkdir(parents=True)
    (config_dir / "gui.json").write_text("{not valid json", encoding="utf-8")
    write_json(config_dir / "global.py.json", {"library_path": str(tmp_path / "default-lib")})
    assert discover_library_path(config_dir) == tmp_path / "default-lib"


def test_no_library_raises_discovery_error(tmp_path, monkeypatch):
    monkeypatch.delenv("CALIBRE_LIBRARY_PATH", raising=False)
    with pytest.raises(DiscoveryError):
        discover_library_path(tmp_path / "empty-config")


def test_calibredb_env_override(tmp_path, monkeypatch):
    fake = tmp_path / "calibredb"
    fake.touch()
    monkeypatch.setenv("CALIBREDB_PATH", str(fake))
    assert discover_calibredb() == fake


def test_calibredb_missing_raises(monkeypatch):
    monkeypatch.delenv("CALIBREDB_PATH", raising=False)
    monkeypatch.setattr("calibre_mcp.config.shutil.which", lambda name: None)
    monkeypatch.setattr("calibre_mcp.config.sys.platform", "plan9")
    with pytest.raises(DiscoveryError):
        discover_calibredb()


def test_load_settings_reads_env(tmp_path, monkeypatch):
    calibredb = tmp_path / "calibredb"
    calibredb.touch()
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    library = tmp_path / "books"
    monkeypatch.setenv("CALIBREDB_PATH", str(calibredb))
    monkeypatch.setenv("CALIBRE_LIBRARY_PATH", str(library))
    monkeypatch.setenv("CALIBRE_INBOX_DIR", str(inbox))
    monkeypatch.setenv("CALIBREDB_TIMEOUT", "42")
    settings = load_settings()
    assert settings.calibredb == calibredb
    assert settings.library_path == library
    assert settings.inbox_dir == inbox
    assert settings.timeout_seconds == 42


def test_calibre_config_dir_platforms(monkeypatch):
    monkeypatch.setattr("calibre_mcp.config.sys", _FakeSys("darwin"), raising=False)
    assert calibre_config_dir().name == "calibre"


class _FakeSys:
    """Minimal sys stand-in exposing just `platform`."""

    def __init__(self, platform: str) -> None:
        self.platform = platform
