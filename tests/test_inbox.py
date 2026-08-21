"""Unit tests for the inbox drop-folder helpers."""

from __future__ import annotations

import re
from pathlib import Path

from calibre_mcp import inbox
from calibre_mcp.config import Settings


def make_settings(tmp_path: Path, with_inbox: bool = True) -> Settings:
    return Settings(
        calibredb=tmp_path / "calibredb",
        library_path=tmp_path / "lib",
        inbox_dir=tmp_path / "inbox" if with_inbox else None,
    )


def test_scan_inbox_picks_ebook_files_only(tmp_path):
    settings = make_settings(tmp_path)
    inbox_dir = settings.inbox_dir
    assert inbox_dir is not None
    inbox_dir.mkdir()
    (inbox_dir / "book.epub").touch()
    (inbox_dir / "paper.PDF").touch()
    (inbox_dir / "cover.jpg").touch()
    (inbox_dir / ".DS_Store").touch()
    (inbox_dir / "subdir").mkdir()
    (inbox_dir / "subdir" / "nested.mobi").touch()
    (inbox_dir / "imported").mkdir()
    (inbox_dir / "imported" / "old.epub").touch()

    files = inbox.scan_inbox(settings)
    assert [path.name for path in files] == ["book.epub", "paper.PDF"]


def test_scan_inbox_without_configuration(tmp_path):
    settings = make_settings(tmp_path, with_inbox=False)
    assert inbox.scan_inbox(settings) == []


def test_move_to_imported_uses_month_bucket(tmp_path):
    settings = make_settings(tmp_path)
    source = tmp_path / "new-book.epub"
    source.touch()
    target = inbox.move_to_imported(settings, source)
    assert target is not None
    assert target.exists()
    assert not source.exists()
    assert re.fullmatch(r"\d{4}-\d{2}", target.parent.name)
    assert target.parent.parent.name == "imported"


def test_move_to_imported_avoids_name_collisions(tmp_path):
    settings = make_settings(tmp_path)
    first = tmp_path / "same.epub"
    second = tmp_path / "same.epub"
    first.touch()
    first_target = inbox.move_to_imported(settings, first)
    second.touch()
    second_target = inbox.move_to_imported(settings, second)
    assert first_target is not None and second_target is not None
    assert first_target != second_target
    assert first_target.name == "same.epub"
    assert second_target.name == "same-1.epub"


def test_move_to_failed(tmp_path):
    settings = make_settings(tmp_path)
    source = tmp_path / "broken.pdf"
    source.touch()
    target = inbox.move_to_failed(settings, source)
    assert target is not None
    assert target.exists()
    assert target.parent.name == "failed"
