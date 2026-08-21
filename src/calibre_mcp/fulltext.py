"""Book content reading and full-text search, built on calibre's converter.

Reading converts a book copy to plain text with calibre's ``ebook-convert``
and pages through it by character offset; the converted text is cached per
source file.

Search uses our own SQLite FTS5 index over those converted texts, with the
``trigram`` tokenizer so CJK substrings match without word segmentation
(English words match as substrings too). The index lives in a cache
directory — nothing inside the calibre library is ever modified.

Calibre's own built-in full-text search is not used: its background indexing
does not reliably extract text outside the GUI on some platforms, and our
index works identically everywhere.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from calibre_mcp import calibre as calibre_cli
from calibre_mcp.config import Settings

# Formats we know how to convert, best (fastest/most faithful) first.
FORMAT_PREFERENCE = ["EPUB", "MOBI", "AZW3", "FB2", "LIT", "RTF", "TXT", "PDF", "DJVU"]

MAX_CHUNK = 40000

SNIPPET_MARKERS = ("[[", "]]")


def cache_dir() -> Path:
    """Directory for converted text and the search index (never the library).

    Override with ``$CALIBRE_TEXT_CACHE_DIR`` for a persistent location —
    the default lives in the OS temp directory and does not survive reboots.
    """
    env = os.environ.get("CALIBRE_TEXT_CACHE_DIR")
    base = Path(env).expanduser() if env else Path(tempfile.gettempdir()) / "calibre-mcp-text"
    base.mkdir(parents=True, exist_ok=True)
    return base


def index_path() -> Path:
    """SQLite file holding the FTS5 search index."""
    return cache_dir() / "index.sqlite"


def book_format_paths(settings: Settings, book_id: int) -> list[Path]:
    """Absolute paths of every format file calibre stores for a book."""
    result = calibre_cli.run_calibredb(
        settings,
        ["list", "--for-machine", "-f", "id,formats", "--search", f"id:{book_id}"],
    )
    if result.returncode != 0:
        raise RuntimeError(calibre_cli.clean_error(result))
    try:
        records = json.loads(result.stdout or "[]")
    except json.JSONDecodeError as exc:
        raise RuntimeError("calibredb returned unparsable output") from exc
    if not records:
        raise RuntimeError(f"no book with id {book_id} in the library")
    return [Path(p) for p in records[0].get("formats") or []]


def pick_source(settings: Settings, book_id: int) -> Path:
    """Choose the best format file to convert, preferring fast formats."""
    paths = book_format_paths(settings, book_id)
    if not paths:
        raise RuntimeError(f"book {book_id} has no readable format files")
    ranked = {p.suffix.lstrip(".").upper(): p for p in paths}
    for fmt in FORMAT_PREFERENCE:
        if fmt in ranked:
            return ranked[fmt]
    return paths[0]


def ebook_convert_binary(settings: Settings) -> Path:
    """ebook-convert lives next to calibredb in every calibre install."""
    name = "ebook-convert.exe" if settings.calibredb.suffix == ".exe" else "ebook-convert"
    return settings.calibredb.parent / name


def convert_timeout() -> int:
    """Seconds allowed for one ebook-convert run (large PDFs are slow)."""
    return int(os.environ.get("CALIBRE_CONVERT_TIMEOUT", "900"))


def extract_text(settings: Settings, source: Path) -> Path:
    """Return the path of a cached plain-text copy, converting if needed."""
    digest = hashlib.sha1(
        f"{source}:{source.stat().st_mtime_ns}".encode()
    ).hexdigest()[:16]
    target = cache_dir() / f"{digest}.txt"
    if target.exists():
        return target
    try:
        result = subprocess.run(
            [str(ebook_convert_binary(settings)), str(source), str(target)],
            capture_output=True,
            text=True,
            timeout=convert_timeout(),
        )
    except subprocess.TimeoutExpired as exc:
        target.unlink(missing_ok=True)
        raise RuntimeError(
            f"text extraction timed out after {convert_timeout()}s for {source.name}; "
            "raise CALIBRE_CONVERT_TIMEOUT for very large books"
        ) from exc
    if result.returncode != 0 or not target.exists():
        error = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(f"failed to extract text from {source.name}: {error[-200:]}")
    return target


# ------------------------------------------------------------------ reading


def cached_ocr_text(source: Path) -> Path | None:
    """OCR markdown cached for a source file, if it exists (see ocr.py)."""
    digest = hashlib.sha1(
        f"{source}:{source.stat().st_mtime_ns}".encode()
    ).hexdigest()[:16]
    candidate = cache_dir() / "ocr" / f"{digest}.md"
    return candidate if candidate.exists() else None


def read_book(
    settings: Settings, book_id: int, offset: int = 0, limit: int = 12000
) -> dict[str, Any]:
    """Return a page of a book's plain text, plus navigation info.

    Prefers OCR output when the book was previously OCR'd (scanned PDFs),
    otherwise converts a format file on demand.
    """
    limit = max(1, min(int(limit), MAX_CHUNK))
    offset = max(0, int(offset))
    source = pick_source(settings, book_id)
    ocr_text = cached_ocr_text(source)
    if ocr_text is not None:
        text = ocr_text.read_text(encoding="utf-8", errors="replace")
        text_source = "ocr"
    else:
        text_path = extract_text(settings, source)
        text = text_path.read_text(encoding="utf-8", errors="replace")
        text_source = source.suffix.lstrip(".").upper()
    chunk = text[offset : offset + limit]
    return {
        "book_id": book_id,
        "format": text_source,
        "offset": offset,
        "next_offset": None if offset + limit >= len(text) else offset + len(chunk),
        "total_chars": len(text),
        "chars_returned": len(chunk),
        "text": chunk,
    }


# ------------------------------------------------------------------ indexing


def _connect(index_file: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(index_file)
    connection.execute(
        "CREATE VIRTUAL TABLE IF NOT EXISTS texts USING fts5("
        "book_id UNINDEXED, source UNINDEXED, text, tokenize='trigram')"
    )
    # Raw copy for LIKE fallback on queries shorter than the trigram
    # tokenizer's 3-character minimum (common for 2-character CJK words).
    connection.execute(
        "CREATE TABLE IF NOT EXISTS texts_raw ("
        "book_id INTEGER PRIMARY KEY, source TEXT, text TEXT)"
    )
    return connection


def index_text(settings: Settings, book_id: int, text: str, source: str) -> dict[str, Any]:
    """(Re)index prepared text for a book; idempotent per book."""
    with _connect(index_path()) as connection:
        connection.execute("DELETE FROM texts WHERE book_id = ?", (book_id,))
        connection.execute(
            "INSERT INTO texts (book_id, source, text) VALUES (?, ?, ?)",
            (book_id, source, text),
        )
        connection.execute(
            "INSERT INTO texts_raw (book_id, source, text) VALUES (?, ?, ?) "
            "ON CONFLICT(book_id) DO UPDATE SET source = excluded.source, "
            "text = excluded.text",
            (book_id, source, text),
        )
        connection.commit()
    return {"book_id": book_id, "chars": len(text), "source": source}


def index_book(settings: Settings, book_id: int) -> dict[str, Any]:
    """Extract one book's text and (re)index it; idempotent per source file."""
    source = pick_source(settings, book_id)
    text_path = extract_text(settings, source)
    text = text_path.read_text(encoding="utf-8", errors="replace")
    return index_text(settings, book_id, text, str(source))


def indexed_book_ids() -> set[int]:
    """Book ids that currently have entries in the search index."""
    if not index_path().exists():
        return set()
    with _connect(index_path()) as connection:
        rows = connection.execute("SELECT DISTINCT book_id FROM texts").fetchall()
    return {row[0] for row in rows}


def build_index(
    settings: Settings, book_ids: list[int] | None = None, limit: int | None = None
) -> dict[str, Any]:
    """Index the given books (default: every book not yet indexed)."""
    if book_ids:
        pending = [book_id for book_id in book_ids if book_id not in indexed_book_ids()]
    else:
        pending = sorted(calibre_cli.all_book_ids(settings) - indexed_book_ids())
    if limit is not None:
        pending = pending[: max(0, int(limit))]

    indexed: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    for book_id in pending:
        try:
            indexed.append(index_book(settings, book_id))
        except Exception as exc:  # noqa: BLE001 — one bad book must not stop the batch
            failed.append({"book_id": book_id, "error": str(exc)})
    return {
        "indexed": indexed,
        "failed": failed,
        "indexed_count": len(indexed),
        "failed_count": len(failed),
        "indexed_book_ids": sorted(indexed_book_ids()),
    }


# ------------------------------------------------------------------ search


def _like_escape(query: str) -> str:
    return query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _like_search(
    connection: sqlite3.Connection, query: str, book_id: int | None, limit: int
) -> list[tuple[int, str]]:
    """Fallback substring search for queries shorter than one trigram."""
    sql = "SELECT book_id, text FROM texts_raw WHERE text LIKE ? ESCAPE '\\'"
    params: list[Any] = [f"%{_like_escape(query)}%"]
    if book_id is not None:
        sql += " AND book_id = ?"
        params.append(book_id)
    sql += " LIMIT ?"
    params.append(limit)

    results: list[tuple[int, str]] = []
    for book_id_row, text in connection.execute(sql, params).fetchall():
        position = text.find(query)
        start = max(0, position - 24)
        end = min(len(text), position + len(query) + 48)
        results.append((book_id_row, text[start:end]))
    return results


def search_index(
    settings: Settings, query: str, book_id: int | None = None, limit: int = 20
) -> list[dict[str, Any]]:
    """Search indexed text; trigram tokenizer gives substring matching."""
    limit = max(1, min(int(limit), 100))
    with _connect(index_path()) as connection:
        if len(query) < 3:
            rows = _like_search(connection, query, book_id, limit)
        else:
            escaped = query.replace('"', '""')
            sql = (
                "SELECT book_id, snippet(texts, 2, ?, ?, ' … ', 24) "
                "FROM texts WHERE texts MATCH ?"
            )
            params: list[Any] = [SNIPPET_MARKERS[0], SNIPPET_MARKERS[1], f'"{escaped}"']
            if book_id is not None:
                sql += " AND book_id = ?"
                params.append(book_id)
            sql += " LIMIT ?"
            params.append(limit)
            rows = connection.execute(sql, params).fetchall()

    books = calibre_cli.books_by_ids(settings, {r[0] for r in rows})
    metadata = {book["id"]: book for book in books}
    return [
        {
            "book_id": row[0],
            "title": metadata.get(row[0], {}).get("title", ""),
            "authors": metadata.get(row[0], {}).get("authors", ""),
            "snippet": row[1],
        }
        for row in rows
    ]


def search_in_book(
    settings: Settings,
    query: str,
    book_id: int | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    """Full-text search over indexed books, with match snippets for quoting.

    Books must be indexed first — see build_index. The response includes
    index coverage so callers can tell whether unindexed books exist.
    """
    matches = search_index(settings, query, book_id=book_id, limit=limit)
    total = len(calibre_cli.all_book_ids(settings))
    indexed = indexed_book_ids()
    return {
        "query": query,
        "book_id": book_id,
        "count": len(matches),
        "indexed_books": len(indexed),
        "total_books": total,
        "matches": matches,
    }
