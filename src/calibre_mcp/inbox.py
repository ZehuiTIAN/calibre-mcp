"""Scanning and housekeeping for a drop folder ("inbox") of new ebooks.

The inbox workflow pairs well with manual downloads: drop the files into the
inbox, ask the assistant to `import_inbox`, and every file is added to the
library, then filed away under ``imported/YYYY-MM/`` (or ``failed/``).
"""

from __future__ import annotations

import datetime as _dt
from pathlib import Path

from calibre_mcp.config import Settings

EBOOK_EXTENSIONS = {
    ".epub", ".pdf", ".mobi", ".azw", ".azw3", ".djvu", ".cbz", ".cbr",
    ".fb2", ".txt", ".md", ".docx", ".rtf", ".lit", ".prc", ".pdb",
    ".chm", ".htmlz",
}

# Subdirectories of the inbox that are housekeeping output, not content.
RESERVED_DIRS = {"imported", "failed"}


def scan_inbox(settings: Settings) -> list[Path]:
    """Return the ebook files currently sitting in the inbox, sorted by name."""
    directory = settings.inbox_dir
    if directory is None or not directory.is_dir():
        return []
    return [
        path
        for path in sorted(directory.iterdir())
        if path.is_file()
        and path.suffix.lower() in EBOOK_EXTENSIONS
        and not path.name.startswith(".")
    ]


def _month_bucket() -> str:
    today = _dt.date.today()
    return f"{today.year:04d}-{today.month:02d}"


def _imported_dir(settings: Settings) -> Path | None:
    if settings.inbox_dir is None:
        return None
    return settings.inbox_dir / "imported" / _month_bucket()


def _failed_dir(settings: Settings) -> Path | None:
    if settings.inbox_dir is None:
        return None
    return settings.inbox_dir / "failed"


def _move(path: Path, bucket: Path | None) -> Path | None:
    """Move a file into a bucket directory, avoiding name collisions."""
    if bucket is None:
        return None
    bucket.mkdir(parents=True, exist_ok=True)
    target = bucket / path.name
    counter = 1
    while target.exists():
        target = bucket / f"{path.stem}-{counter}{path.suffix}"
        counter += 1
    path.rename(target)
    return target


def move_to_imported(settings: Settings, path: Path) -> Path | None:
    """File a successfully handled inbox file under imported/YYYY-MM/."""
    return _move(path, _imported_dir(settings))


def move_to_failed(settings: Settings, path: Path) -> Path | None:
    """File a failed inbox file under failed/."""
    return _move(path, _failed_dir(settings))
