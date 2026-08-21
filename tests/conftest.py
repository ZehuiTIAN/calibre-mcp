"""Shared pytest fixtures."""

from __future__ import annotations

import pytest

from calibre_mcp.config import DiscoveryError, Settings, discover_calibredb


@pytest.fixture()
def calibre_settings(tmp_path, monkeypatch) -> Settings | None:
    """A Settings object pointing at a fresh, empty calibre library.

    Skipped when calibre is not installed locally (integration-only fixture).
    """
    try:
        calibredb = discover_calibredb()
    except DiscoveryError:
        pytest.skip("calibre is not installed on this machine")
    library = tmp_path / "Calibre Library"
    library.mkdir()
    monkeypatch.setenv("CALIBREDB_PATH", str(calibredb))
    monkeypatch.setenv("CALIBRE_LIBRARY_PATH", str(library))
    return Settings(calibredb=calibredb, library_path=library, inbox_dir=None)
