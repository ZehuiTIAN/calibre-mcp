"""Tests for full-text search and book reading: unit tests with a temp index,
plus integration tests against a real calibre installation."""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest
from helpers import make_epub_with_body

from calibre_mcp import calibre as calibre_cli
from calibre_mcp import fulltext
from calibre_mcp.config import Settings

EN_BODY = (
    "<p>The quick brown fox jumps over the lazy dog. Quantum banana pudding "
    "is a delicious dessert invented for testing full text search.</p>"
)
ZH_BODY = (
    "<p>量子香蕉布丁是一种美味的甜点,专门为全文检索测试而发明。</p>"
    "<p>机器学习与深度学习正在改变世界,数据量每天都在增长。</p>"
)


def make_settings(tmp_path: Path) -> Settings:
    return Settings(calibredb=tmp_path / "calibredb", library_path=tmp_path / "lib")


@pytest.fixture()
def indexed_fixture(tmp_path, monkeypatch):
    """Index two books (EN + ZH) into a temp index without touching calibre."""
    index_dir = tmp_path / "cache"
    index_dir.mkdir()
    monkeypatch.setattr(fulltext, "cache_dir", lambda: index_dir)
    # Unit tests must never invoke the real calibredb binary.
    monkeypatch.setattr(calibre_cli, "all_book_ids", lambda s: {1, 2})
    monkeypatch.setattr(calibre_cli, "books_by_ids", lambda s, ids: [])

    sources = {
        1: make_epub_with_body(tmp_path / "src", "English Test", "A Author", EN_BODY, "en.epub"),
        2: make_epub_with_body(tmp_path / "src", "中文测试", "乙作者", ZH_BODY, "zh.epub"),
    }
    monkeypatch.setattr(fulltext, "pick_source", lambda s, book_id: sources[book_id])

    def fake_extract_text(settings, source):
        text_path = index_dir / f"{source.stem}.txt"
        with zipfile.ZipFile(source) as archive:
            text_path.write_text(archive.read("text.html").decode("utf-8"), encoding="utf-8")
        return text_path

    monkeypatch.setattr(fulltext, "extract_text", fake_extract_text)
    settings = make_settings(tmp_path)
    fulltext.index_book(settings, 1)
    fulltext.index_book(settings, 2)
    return settings


# ---------------------------------------------------------------- unit tests


def test_index_and_search_english(indexed_fixture):
    settings = indexed_fixture
    assert fulltext.indexed_book_ids() == {1, 2}
    result = fulltext.search_in_book(settings, "banana pudding")
    assert result["count"] >= 1
    assert result["matches"][0]["book_id"] == 1
    assert "banana" in result["matches"][0]["snippet"]


def test_search_chinese_substring_without_segmentation(indexed_fixture):
    settings = indexed_fixture
    result = fulltext.search_in_book(settings, "全文检索测试")
    assert result["count"] >= 1
    assert result["matches"][0]["book_id"] == 2


def test_short_cjk_query_uses_like_fallback(indexed_fixture):
    settings = indexed_fixture
    result = fulltext.search_in_book(settings, "布丁")  # 2 chars < trigram minimum
    assert result["count"] >= 1
    assert all(match["book_id"] == 2 for match in result["matches"])
    assert "布丁" in result["matches"][0]["snippet"]


def test_search_restricted_to_book(indexed_fixture):
    settings = indexed_fixture
    result = fulltext.search_in_book(settings, "香蕉布丁", book_id=2)
    assert result["count"] >= 1
    assert all(match["book_id"] == 2 for match in result["matches"])


def test_read_book_pagination(tmp_path, monkeypatch):
    settings = make_settings(tmp_path)
    text_path = tmp_path / "book.txt"
    text_path.write_text("0123456789" * 100, encoding="utf-8")
    monkeypatch.setattr(fulltext, "pick_source", lambda s, b: tmp_path / "x.epub")
    monkeypatch.setattr(fulltext, "extract_text", lambda s, p: text_path)

    page1 = fulltext.read_book(settings, 1, offset=0, limit=100)
    assert page1["chars_returned"] == 100
    assert page1["next_offset"] == 100
    assert page1["total_chars"] == 1000
    assert page1["text"].startswith("0123456789")

    page2 = fulltext.read_book(settings, 1, offset=100, limit=50)
    assert page2["text"].startswith("0123456789")
    assert page2["next_offset"] == 150


def test_build_index_skips_already_indexed(indexed_fixture, monkeypatch):
    settings = indexed_fixture
    calls: list[int] = []

    def counting_index_book(s, book_id):
        calls.append(book_id)
        return {"book_id": book_id}

    monkeypatch.setattr(fulltext, "index_book", counting_index_book)
    result = fulltext.build_index(settings)
    assert calls == []  # both books already indexed
    assert result["indexed_count"] == 0


def test_build_index_processes_pending(indexed_fixture, monkeypatch):
    settings = indexed_fixture
    monkeypatch.setattr(calibre_cli, "all_book_ids", lambda s: {1, 2, 3})
    monkeypatch.setattr(fulltext, "index_book", lambda s, book_id: {"book_id": book_id})
    result = fulltext.build_index(settings)
    assert [entry["book_id"] for entry in result["indexed"]] == [3]


def test_build_index_records_failures_and_continues(indexed_fixture, monkeypatch):
    settings = indexed_fixture
    monkeypatch.setattr(calibre_cli, "all_book_ids", lambda s: {1, 2, 99})

    def failing_index_book(s, book_id):
        if book_id == 99:
            raise RuntimeError("boom")
        return {"book_id": book_id}

    monkeypatch.setattr(fulltext, "index_book", failing_index_book)
    result = fulltext.build_index(settings)
    assert result["failed"][0]["book_id"] == 99
    assert result["failed"][0]["error"] == "boom"
    assert result["indexed_count"] == 0  # books 1 and 2 were already indexed


def test_extract_text_timeout_raises_clear_error(tmp_path, monkeypatch):
    settings = make_settings(tmp_path)
    source = tmp_path / "slow.pdf"
    source.touch()

    import subprocess as sp

    def fake_run(command, capture_output, text, timeout):
        raise sp.TimeoutExpired(command, timeout)

    monkeypatch.setattr(fulltext.subprocess, "run", fake_run)
    with pytest.raises(RuntimeError, match="timed out"):
        fulltext.extract_text(settings, source)


# ---------------------------------------------------------- integration tests


@pytest.mark.integration
def test_full_pipeline_index_search_read(calibre_settings, tmp_path, monkeypatch):
    settings = calibre_settings
    assert settings is not None
    make_epub_with_body(tmp_path / "src", "FTS Pipeline EN", "A Author", EN_BODY, "en.epub")
    make_epub_with_body(tmp_path / "src", "FTS Pipeline ZH", "乙作者", ZH_BODY, "zh.epub")

    index_dir = tmp_path / "cache"
    index_dir.mkdir()
    monkeypatch.setattr(fulltext, "cache_dir", lambda: index_dir)

    added = calibre_cli.add_paths(settings, [tmp_path / "src"])
    assert added[0]["status"] == "added"
    book_ids = set(added[0]["book_ids"])

    build = fulltext.build_index(settings)
    assert build["failed_count"] == 0
    assert set(build["indexed_book_ids"]) == book_ids

    result = fulltext.search_in_book(settings, "quantum banana")
    assert result["count"] >= 1
    match = result["matches"][0]
    assert "banana" in match["snippet"]
    assert match["title"]  # metadata resolved via calibre

    zh_result = fulltext.search_in_book(settings, "全文检索测试")
    assert zh_result["count"] >= 1

    page = fulltext.read_book(settings, match["book_id"], offset=0, limit=2000)
    assert "banana" in page["text"]
    assert page["total_chars"] > 0
