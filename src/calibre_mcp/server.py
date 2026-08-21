"""calibre-mcp: MCP server giving AI assistants access to a local Calibre library.

Run with `calibre-mcp` (console script), `python -m calibre_mcp`, or register
it directly in an MCP client — see the README for client configuration
examples.
"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from calibre_mcp import __version__, inbox
from calibre_mcp import calibre as calibre_cli
from calibre_mcp import fulltext as fulltext_cli
from calibre_mcp.config import Settings, load_settings

mcp = FastMCP(
    "calibre-mcp",
    instructions=(
        "Tools for searching and managing a local Calibre ebook library. "
        "Use search_books before looking for new books anywhere else, to check "
        "whether a title is already in the library. For content-level work "
        "(quoting, summaries), index the library once with build_index, then "
        "use search_in_book and read_book. Write operations (add_books, "
        "import_inbox) can fail while the Calibre GUI has the library open — "
        "ask the user to close Calibre first if that happens."
    ),
)

STATUS_BUCKETS = {"added": "imported", "duplicate": "duplicates", "failed": "failed"}


def _settings() -> Settings:
    # Re-resolve on every call so environment changes take effect without a restart.
    return load_settings()


def _clamp(limit: int, low: int = 1, high: int = 200) -> int:
    return max(low, min(int(limit), high))


@mcp.tool()
def library_info() -> dict[str, Any]:
    """Show the active library: its path on disk, number of books, calibre version."""
    settings = _settings()
    return {
        "library_path": str(settings.library_path),
        "book_count": len(calibre_cli.all_book_ids(settings)),
        "calibre_version": calibre_cli.calibre_version(settings),
        "calibre_mcp_version": __version__,
    }


@mcp.tool()
def search_books(query: str, limit: int = 20) -> dict[str, Any]:
    """Search the library with calibre's query language.

    Args:
        query: A calibre search expression, e.g. `author:asimov`,
            `title:"i robot"`, `tags:python`. An empty string matches everything.
        limit: Maximum number of results (1-200, default 20).
    """
    books = calibre_cli.search_books(_settings(), query, limit=_clamp(limit))
    return {"query": query, "count": len(books), "books": books}


@mcp.tool()
def list_books(
    limit: int = 20,
    sort_by: str = "id",
    order: str = "desc",
    search: str = "",
) -> dict[str, Any]:
    """Browse books in the library; defaults to most recently added first.

    Args:
        limit: Maximum number of results (1-200, default 20).
        sort_by: calibre field to sort by, e.g. id, title, authors, timestamp.
        order: "desc" (default) or "asc".
        search: Optional calibre search expression to filter the listing.
    """
    books = calibre_cli.list_books(
        _settings(),
        search=search,
        limit=_clamp(limit),
        sort_by=sort_by,
        ascending=(order == "asc"),
    )
    return {"count": len(books), "books": books}


@mcp.tool()
def add_books(paths: list[str]) -> dict[str, Any]:
    """Import ebook files (or directories of ebooks) into the library.

    Books whose title + author are already in the library are skipped by
    calibre and reported as "duplicate". The Calibre GUI must not hold the
    library open while adding.

    Args:
        paths: Absolute paths to ebook files or folders containing ebooks.
    """
    results = calibre_cli.add_paths(_settings(), paths)
    return {
        "results": results,
        "added": sum(1 for result in results if result["status"] == "added"),
        "duplicates": sum(1 for result in results if result["status"] == "duplicate"),
        "failed": sum(1 for result in results if result["status"] == "failed"),
    }


@mcp.tool()
def import_inbox() -> dict[str, Any]:
    """Import every ebook file in the configured inbox folder into the library.

    Requires the CALIBRE_INBOX_DIR environment variable. Successfully added
    files and duplicates are moved to `<inbox>/imported/YYYY-MM/`; files that
    fail to import are moved to `<inbox>/failed/` for inspection.
    """
    settings = _settings()
    if settings.inbox_dir is None:
        raise ValueError("CALIBRE_INBOX_DIR is not configured")

    files = inbox.scan_inbox(settings)
    summary: dict[str, Any] = {
        "inbox_dir": str(settings.inbox_dir),
        "scanned": len(files),
        "imported": [],
        "duplicates": [],
        "failed": [],
    }

    for path in files:
        result = calibre_cli.add_paths(settings, [path])[0]
        status = result["status"]
        moved = None
        if status == "failed":
            moved = inbox.move_to_failed(settings, path)
        else:
            moved = inbox.move_to_imported(settings, path)

        item: dict[str, Any] = {"file": path.name, "status": status}
        if result.get("book_ids"):
            item["book_ids"] = result["book_ids"]
            item["titles"] = [book["title"] for book in result.get("books", [])]
        if result.get("error"):
            item["error"] = result["error"]
        if moved is not None:
            item["moved_to"] = str(moved)
        summary[STATUS_BUCKETS[status]].append(item)

    return summary


@mcp.tool()
def search_in_book(query: str, book_id: int | None = None, limit: int = 20) -> dict[str, Any]:
    """Full-text search inside book contents, with match snippets for quoting.

    Searches the local text index; books must be indexed first with
    build_index (a one-time, cached step). Matching is substring-based, so
    Chinese and English queries both work without word segmentation.

    Args:
        query: Text to find inside book contents.
        book_id: Optional book id to restrict the search to a single book.
        limit: Maximum number of matches to return (1-100, default 20).
    """
    return fulltext_cli.search_in_book(_settings(), query, book_id=book_id, limit=limit)


@mcp.tool()
def build_index(book_ids: list[int] | None = None, limit: int | None = None) -> dict[str, Any]:
    """Index book contents for full-text search (one-time, cached).

    Converts each book's text with calibre's converter and stores it in a
    local SQLite FTS5 index kept outside the library. Only books that are
    not indexed yet are processed; an initial run over a whole library can
    take several minutes.

    Args:
        book_ids: Optional list of book ids to index; default: all books
            that are not indexed yet.
        limit: Optional cap on how many books to index in this call.
    """
    return fulltext_cli.build_index(_settings(), book_ids=book_ids, limit=limit)


@mcp.tool()
def read_book(book_id: int, offset: int = 0, limit: int = 12000) -> dict[str, Any]:
    """Read a page of a book's plain text, converted on demand by calibre.

    The first call converts the book (a copy in a cache directory — the
    library is never modified); later calls reuse the cache. Returns the
    chunk plus `next_offset` for paging and `total_chars` for context.

    Args:
        book_id: The book's id (find it with search_books).
        offset: Character offset to start reading from (0 = beginning).
        limit: Maximum characters to return (1-40000, default 12000).
    """
    return fulltext_cli.read_book(_settings(), book_id, offset=offset, limit=limit)


def main() -> None:
    """Run the MCP server over stdio."""
    mcp.run()
