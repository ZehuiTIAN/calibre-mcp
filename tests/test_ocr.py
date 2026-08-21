"""Tests for the OCR pipeline: unit tests with fake providers/documents,
plus integration tests with real pymupdf, pandoc and calibre."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from calibre_mcp import calibre as calibre_cli
from calibre_mcp import fulltext
from calibre_mcp import ocr as ocr_cli
from calibre_mcp.config import Settings

OCR_MARKDOWN = "# Chapter One\n\n量子香蕉布丁 scanned text with banana keyword.\n"


class FakeProvider:
    """Deterministic stand-in for a cloud OCR backend."""

    name = "fake"

    def __init__(self, api_key, model, base_url=None):
        self.api_key = api_key
        self.model = model

    def ocr_pages(self, page_images, context):
        return OCR_MARKDOWN


def make_settings(tmp_path: Path) -> Settings:
    return Settings(calibredb=tmp_path / "calibredb", library_path=tmp_path / "lib")


class FakePdfPage:
    def __init__(self, text: str) -> None:
        self._text = text

    def get_text(self, kind):
        return self._text

    def get_pixmap(self, dpi):
        return SimpleNamespace(tobytes=lambda fmt="png": b"fake-image")


class FakePdf:
    def __init__(self, pages: list[FakePdfPage]) -> None:
        self._pages = pages

    def __len__(self):
        return len(self._pages)

    def __getitem__(self, key):
        return self._pages[key]

    def close(self):
        pass


# ---------------------------------------------------------------- unit tests


def test_ocr_config_from_env(monkeypatch):
    monkeypatch.setenv("CALIBRE_OCR_PROVIDER", "anthropic")
    monkeypatch.setenv("CALIBRE_OCR_API_KEY", "sk-test")
    monkeypatch.setenv("CALIBRE_OCR_MODEL", "some-model")
    monkeypatch.setenv("CALIBRE_OCR_MAX_PAGES", "123")
    config = ocr_cli.OcrConfig.from_env()
    assert config.provider == "anthropic"
    assert config.api_key == "sk-test"
    assert config.model == "some-model"
    assert config.max_pages == 123


def test_get_provider_unknown_raises():
    config = ocr_cli.OcrConfig(provider="nope", api_key="x")
    with pytest.raises(RuntimeError, match="unknown OCR provider"):
        ocr_cli.get_provider(config)


def test_get_provider_missing_key_raises():
    config = ocr_cli.OcrConfig(provider="anthropic", api_key=None)
    with pytest.raises(RuntimeError, match="CALIBRE_OCR_API_KEY"):
        ocr_cli.get_provider(config)


def test_is_scanned_threshold(monkeypatch):
    blank_pages = [FakePdfPage("") for _ in range(8)]
    text_pages = [FakePdfPage("real text " * 20) for _ in range(2)]
    monkeypatch.setattr(ocr_cli, "_pdf_document", lambda p: FakePdf(blank_pages + text_pages))
    scanned, stats = ocr_cli.is_scanned(Path("x.pdf"))
    assert scanned is True
    assert stats["pages"] == 10 and stats["text_pages"] == 2


def test_is_scanned_text_pdf(monkeypatch):
    pages = [FakePdfPage("real text " * 20) for _ in range(10)]
    monkeypatch.setattr(ocr_cli, "_pdf_document", lambda p: FakePdf(pages))
    scanned, _ = ocr_cli.is_scanned(Path("x.pdf"))
    assert scanned is False


def test_render_pages_truncates_to_max(monkeypatch):
    pages = [FakePdfPage("") for _ in range(600)]
    monkeypatch.setattr(ocr_cli, "_pdf_document", lambda p: FakePdf(pages))
    images = ocr_cli.render_pages(Path("x.pdf"), max_pages=500)
    assert len(images) == 500


def test_ocr_book_short_circuits_when_not_scanned(tmp_path, monkeypatch):
    settings = make_settings(tmp_path)
    monkeypatch.setattr(calibre_cli, "books_by_ids", lambda s, ids: [{"id": 1, "title": "T"}])
    monkeypatch.setattr(
        fulltext, "book_format_paths", lambda s, book_id: [tmp_path / "b.pdf"]
    )
    monkeypatch.setattr(ocr_cli, "is_scanned", lambda p: (False, {"pages": 5}))
    result = ocr_cli.ocr_book(settings, 1)
    assert result["status"] == "not_scanned"


def test_ocr_book_full_pipeline_with_fake_provider(tmp_path, monkeypatch):
    settings = make_settings(tmp_path)
    pdf = tmp_path / "scanned.pdf"
    pdf.touch()
    cache = tmp_path / "cache"
    cache.mkdir()
    monkeypatch.setattr(fulltext, "cache_dir", lambda: cache)
    monkeypatch.setitem(ocr_cli.PROVIDERS, "fake", FakeProvider)
    monkeypatch.setenv("CALIBRE_OCR_PROVIDER", "fake")
    monkeypatch.setenv("CALIBRE_OCR_API_KEY", "key")

    monkeypatch.setattr(
        calibre_cli, "books_by_ids",
        lambda s, ids: [{"id": 1, "title": "Scanned Book", "authors": "Some Author"}],
    )
    monkeypatch.setattr(calibre_cli, "all_book_ids", lambda s: {1})
    monkeypatch.setattr(fulltext, "book_format_paths", lambda s, book_id: [pdf])
    monkeypatch.setattr(ocr_cli, "is_scanned", lambda p: (True, {"pages": 3, "text_pages": 0}))
    monkeypatch.setattr(ocr_cli, "render_pages", lambda p, **kw: [b"img1", b"img2"])

    attached: list[str] = []
    monkeypatch.setattr(
        ocr_cli, "typeset_epub",
        lambda md, title, author, out_dir: (out_dir / "f.epub"),
    )
    monkeypatch.setattr(
        ocr_cli, "attach_format", lambda s, book_id, path: attached.append(str(path))
    )

    result = ocr_cli.ocr_book(settings, 1)
    assert result["status"] == "scanned_ocr"
    assert result["indexed"] is True
    assert result["format_attached"] is True
    assert len(attached) == 1
    # OCR text landed in the FTS index
    assert fulltext.indexed_book_ids() == {1}
    hit = fulltext.search_in_book(settings, "量子香蕉布丁", book_id=1)
    assert hit["count"] >= 1


def test_ocr_book_rejects_non_pdf(tmp_path, monkeypatch):
    settings = make_settings(tmp_path)
    monkeypatch.setattr(calibre_cli, "books_by_ids", lambda s, ids: [{"id": 1}])
    monkeypatch.setattr(
        fulltext, "book_format_paths", lambda s, book_id: [tmp_path / "b.epub"]
    )
    with pytest.raises(RuntimeError, match="PDF"):
        ocr_cli.ocr_book(settings, 1)


# ---------------------------------------------------------- integration tests


@pytest.mark.integration
def test_is_scanned_real_pymupdf(tmp_path):
    import pymupdf

    scanned_pdf = tmp_path / "scanned.pdf"
    document = pymupdf.open()
    document.new_page()  # blank page: image-only, no text layer
    document.new_page()
    document.save(scanned_pdf)
    document.close()

    text_pdf = tmp_path / "text.pdf"
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((72, 72), "Real text layer " * 40)
    document.save(text_pdf)
    document.close()

    assert ocr_cli.is_scanned(scanned_pdf)[0] is True
    assert ocr_cli.is_scanned(text_pdf)[0] is False


@pytest.mark.integration
def test_full_pipeline_real_pandoc_and_calibre(calibre_settings, tmp_path, monkeypatch):
    settings = calibre_settings
    assert settings is not None
    cache = tmp_path / "cache"
    cache.mkdir()
    monkeypatch.setattr(fulltext, "cache_dir", lambda: cache)
    monkeypatch.setitem(ocr_cli.PROVIDERS, "fake", FakeProvider)
    monkeypatch.setenv("CALIBRE_OCR_PROVIDER", "fake")
    monkeypatch.setenv("CALIBRE_OCR_API_KEY", "key")

    # Build a real image-only PDF and add it to the throwaway library.
    import pymupdf

    pdf = tmp_path / "scan.pdf"
    document = pymupdf.open()
    for _ in range(3):
        document.new_page()
    document.save(pdf)
    document.close()

    added = calibre_cli.add_paths(settings, [pdf])
    assert added[0]["status"] == "added"
    book_id = added[0]["book_ids"][0]

    result = ocr_cli.ocr_book(settings, book_id)
    assert result["status"] == "scanned_ocr"
    assert result["format_attached"] is True
    assert Path(result["epub_path"]).exists()

    # The re-typeset EPUB is now attached to the book record.
    formats = fulltext.book_format_paths(settings, book_id)
    assert any(path.suffix == ".epub" for path in formats)

    # OCR text is searchable in the index.
    hit = fulltext.search_in_book(settings, "量子香蕉布丁", book_id=book_id)
    assert hit["count"] >= 1
    assert hit["matches"][0]["title"]
