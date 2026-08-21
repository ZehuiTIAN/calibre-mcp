"""Helpers for building test fixtures."""

from __future__ import annotations

import zipfile
from pathlib import Path


def make_epub_with_body(
    directory: Path, title: str, author: str, body: str, name: str | None = None
) -> Path:
    """Create a minimal EPUB 2 file carrying the given title/author/body."""
    directory.mkdir(parents=True, exist_ok=True)
    safe_title = title.replace("/", "-").replace(" ", "_")
    path = directory / (name or f"{safe_title} - {author}.epub")

    container = """<?xml version="1.0"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>"""
    opf = f"""<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="2.0" unique-identifier="id">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:opf="http://www.idpf.org/2007/opf">
    <dc:identifier id="id" opf:scheme="UUID">urn:uuid:{safe_title}</dc:identifier>
    <dc:title>{title}</dc:title>
    <dc:creator opf:role="aut">{author}</dc:creator>
    <dc:language>en</dc:language>
  </metadata>
  <manifest><item id="t" href="text.html" media-type="application/xhtml+xml"/></manifest>
  <spine><itemref idref="t"/></spine>
</package>"""
    text = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<html xmlns="http://www.w3.org/1999/xhtml"><head><title>'
        f"{title}</title></head><body>{body}</body></html>"
    )

    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        archive.writestr("META-INF/container.xml", container)
        archive.writestr("content.opf", opf)
        archive.writestr("text.html", text)
    return path


def make_epub(directory: Path, title: str, author: str, name: str | None = None) -> Path:
    """Create a minimal EPUB carrying the given title/author (small body)."""
    return make_epub_with_body(directory, title, author, "<p>hello</p>", name)
