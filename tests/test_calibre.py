"""Tests for the calibredb wrapper: unit tests with a mocked subprocess,
plus integration tests against a real calibre installation."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from helpers import make_epub

from calibre_mcp import calibre as calibre_cli
from calibre_mcp.config import Settings


def make_settings(tmp_path: Path) -> Settings:
    return Settings(calibredb=tmp_path / "calibredb", library_path=tmp_path / "lib")


# ---------------------------------------------------------------- unit tests


def test_all_book_ids_parses_json(tmp_path, monkeypatch):
    calls: list[list[str]] = []

    def fake_run(command, capture_output, text, timeout):
        calls.append(command)
        return SimpleNamespace(returncode=0, stdout='[{"id": 1}, {"id": 2}, {"id": 9}]', stderr="")

    monkeypatch.setattr(calibre_cli.subprocess, "run", fake_run)
    ids = calibre_cli.all_book_ids(make_settings(tmp_path))
    assert ids == {1, 2, 9}
    assert calls[0][-2:] == ["-f", "id"]  # asks for the id field only


def test_all_book_ids_empty_library(tmp_path, monkeypatch):
    def fake_run(command, capture_output, text, timeout):
        return SimpleNamespace(returncode=0, stdout="[]", stderr="")

    monkeypatch.setattr(calibre_cli.subprocess, "run", fake_run)
    assert calibre_cli.all_book_ids(make_settings(tmp_path)) == set()


def test_simplify_maps_format_paths_to_extensions():
    record = {
        "id": 7,
        "title": "A Book",
        "authors": "Some Author",
        "formats": ["/lib/Some Author/A Book.epub", "/lib/Some Author/A Book.pdf"],
        "publisher": "",
        "tags": ["tag1"],
    }
    simplified = calibre_cli.simplify(record)
    assert simplified == {
        "id": 7,
        "title": "A Book",
        "authors": "Some Author",
        "formats": ["EPUB", "PDF"],
        "publisher": "",
        "tags": ["tag1"],
    }


def test_add_paths_missing_file_reports_failed(tmp_path):
    settings = make_settings(tmp_path)
    results = calibre_cli.add_paths(settings, [tmp_path / "nope.epub"])
    assert results[0]["status"] == "failed"
    assert "does not exist" in results[0]["error"]


def test_add_paths_detects_duplicate_via_id_diff(tmp_path, monkeypatch):
    settings = make_settings(tmp_path)
    existing = tmp_path / "existing.epub"
    existing.touch()
    ids = {"1", "2"}

    def fake_run(command, capture_output, text, timeout):
        if "list" in command:  # all_book_ids snapshot
            payload = [{"id": book_id} for book_id in sorted(ids)]
            return SimpleNamespace(returncode=0, stdout=json.dumps(payload), stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(calibre_cli.subprocess, "run", fake_run)
    results = calibre_cli.add_paths(settings, [existing])
    assert results[0]["status"] == "duplicate"


def test_add_paths_surfaces_failure(tmp_path, monkeypatch):
    settings = make_settings(tmp_path)
    existing = tmp_path / "existing.epub"
    existing.touch()

    def fake_run(command, capture_output, text, timeout):
        if "list" in command:  # all_book_ids snapshot
            return SimpleNamespace(returncode=0, stdout="[]", stderr="")
        return SimpleNamespace(returncode=1, stdout="", stderr="database is locked")

    monkeypatch.setattr(calibre_cli.subprocess, "run", fake_run)
    results = calibre_cli.add_paths(settings, [existing])
    assert results[0]["status"] == "failed"
    assert "database is locked" in results[0]["error"]


# ---------------------------------------------------------- integration tests


@pytest.mark.integration
def test_add_then_duplicate_skip(calibre_settings, tmp_path):
    settings = calibre_settings
    assert settings is not None
    epub = make_epub(tmp_path / "src", "Machine Learning Basics", "Alice Example")

    first = calibre_cli.add_paths(settings, [epub])
    assert first[0]["status"] == "added"
    assert len(first[0]["book_ids"]) == 1
    assert first[0]["books"][0]["title"] == "Machine Learning Basics"

    second = calibre_cli.add_paths(settings, [epub])
    assert second[0]["status"] == "duplicate"

    hits = calibre_cli.search_books(settings, 'title:"Machine Learning"')
    assert len(hits) == 1
    assert hits[0]["authors"] == "Alice Example"
    assert "EPUB" in hits[0]["formats"]


@pytest.mark.integration
def test_library_info_grows_after_add(calibre_settings, tmp_path):
    settings = calibre_settings
    assert settings is not None
    assert len(calibre_cli.all_book_ids(settings)) == 0

    epub = make_epub(tmp_path / "src", "Second Book", "Bob Writer")
    calibre_cli.add_paths(settings, [epub])
    assert len(calibre_cli.all_book_ids(settings)) == 1

    records = calibre_cli.list_books(settings, limit=10)
    assert len(records) == 1
    assert records[0]["title"] == "Second Book"


@pytest.mark.integration
def test_add_directory_with_multiple_books(calibre_settings, tmp_path):
    settings = calibre_settings
    assert settings is not None
    folder = tmp_path / "batch"
    make_epub(folder, "Book One", "Author One")
    make_epub(folder, "Book Two", "Author Two")

    results = calibre_cli.add_paths(settings, [folder])
    assert results[0]["status"] == "added"
    assert len(results[0]["book_ids"]) == 2
