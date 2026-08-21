"""Locale-independent wrapper around the calibredb command-line tool.

All listing operations use ``--for-machine`` (JSON output) so results never
depend on the user's calibre interface language. Duplicate detection relies
on comparing the set of book ids before and after an add, which sidesteps
parsing localized "already exists" messages.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from calibre_mcp.config import Settings

BOOK_FIELDS = ["id", "title", "authors", "formats", "publisher", "tags"]

MAX_LIMIT = 200


def run_calibredb(
    settings: Settings, args: list[str], timeout: int | None = None
) -> subprocess.CompletedProcess[str]:
    """Run calibredb against the configured library, capturing UTF-8 output."""
    command = [str(settings.calibredb), "--library-path", str(settings.library_path), *args]
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=timeout if timeout is not None else settings.timeout_seconds,
    )


def clean_error(result: subprocess.CompletedProcess[str]) -> str:
    """Best-effort error message from a failed calibredb invocation."""
    stderr = (result.stderr or "").strip() or (result.stdout or "").strip()
    lines = [line.strip() for line in stderr.splitlines() if line.strip()]
    if lines:
        return lines[-1]
    return f"calibredb exited with code {result.returncode}"


def calibre_version(settings: Settings) -> str:
    """Return the version string reported by calibredb."""
    result = subprocess.run(
        [str(settings.calibredb), "--version"], capture_output=True, text=True, timeout=30
    )
    return (result.stdout or "").strip()


def all_book_ids(settings: Settings) -> set[int]:
    """Return the set of every book id currently in the library.

    Uses `list --for-machine` rather than `search ""` because search exits
    non-zero on an empty library (with a localized message), while the JSON
    listing cleanly returns `[]`.
    """
    result = run_calibredb(settings, ["list", "--for-machine", "-f", "id"])
    if result.returncode != 0:
        raise RuntimeError(clean_error(result))
    try:
        records = json.loads(result.stdout or "[]")
    except json.JSONDecodeError as exc:
        raise RuntimeError("calibredb returned unparsable output") from exc
    return {int(record["id"]) for record in records}


def simplify(record: dict[str, Any]) -> dict[str, Any]:
    """Reduce a --for-machine record to a compact, stable shape."""
    formats = record.get("formats") or []
    return {
        "id": record.get("id"),
        "title": record.get("title") or "",
        "authors": record.get("authors") or "",
        "formats": sorted({Path(p).suffix.lstrip(".").upper() for p in formats}),
        "publisher": record.get("publisher") or "",
        "tags": record.get("tags") or [],
    }


def list_books(
    settings: Settings,
    search: str = "",
    limit: int = 20,
    sort_by: str = "id",
    ascending: bool = False,
) -> list[dict[str, Any]]:
    """List books matching an optional calibre search expression."""
    limit = max(1, min(int(limit), MAX_LIMIT))
    args = ["list", "--for-machine", "-f", ",".join(BOOK_FIELDS), "--limit", str(limit)]
    if sort_by:
        args += ["--sort-by", sort_by]
    if ascending:
        args.append("--ascending")
    if search:
        args += ["--search", search]
    result = run_calibredb(settings, args)
    if result.returncode != 0:
        raise RuntimeError(clean_error(result))
    try:
        records = json.loads(result.stdout or "[]")
    except json.JSONDecodeError as exc:
        raise RuntimeError("calibredb returned unparsable output") from exc
    return [simplify(record) for record in records]


def search_books(settings: Settings, query: str, limit: int = 20) -> list[dict[str, Any]]:
    """Search the library using calibre's query language."""
    return list_books(settings, search=query, limit=limit)


def books_by_ids(settings: Settings, ids: set[int]) -> list[dict[str, Any]]:
    """Fetch full records for the given book ids."""
    if not ids:
        return []
    query = " or ".join(f"id:{book_id}" for book_id in sorted(ids))
    return list_books(settings, search=query, limit=len(ids))


def add_paths(settings: Settings, paths: list[str | Path]) -> list[dict[str, Any]]:
    """Add files or directories to the library with duplicate detection.

    calibre itself skips books whose title + author are already in the
    library, so this function simply compares the set of book ids before and
    after the add: new ids mean the file was imported, no new ids with a
    clean exit means calibre considered it a duplicate.
    """
    results: list[dict[str, Any]] = []
    for raw in paths:
        path = Path(raw).expanduser()
        if not path.exists():
            results.append(
                {"path": str(path), "status": "failed", "error": "path does not exist"}
            )
            continue

        before = all_book_ids(settings)
        args = ["add"]
        if path.is_dir():
            args.append("--recurse")
        args.append(str(path))
        result = run_calibredb(settings, args)
        after = all_book_ids(settings)
        new_ids = after - before

        if result.returncode != 0:
            results.append(
                {"path": str(path), "status": "failed", "error": clean_error(result)}
            )
        elif new_ids:
            books = books_by_ids(settings, new_ids)
            results.append(
                {
                    "path": str(path),
                    "status": "added",
                    "book_ids": sorted(new_ids),
                    "books": books,
                }
            )
        else:
            results.append({"path": str(path), "status": "duplicate"})
    return results
