"""Helpers for building test fixtures."""

from __future__ import annotations

import zipfile
from pathlib import Path


def make_epub(directory: Path, title: str, author: str, name: str | None = None) -> Path:
    """Create a minimal, valid EPUB file carrying the given title/author."""
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
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="id">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="id">urn:uuid:{safe_title}</dc:identifier>
    <dc:title>{title}</dc:title>
    <dc:creator>{author}</dc:creator>
    <dc:language>en</dc:language>
  </metadata>
  <manifest><item id="t" href="text.html" media-type="application/xhtml+xml"/></manifest>
  <spine><itemref idref="t"/></spine>
</package>"""
    text = "<html xmlns='http://www.w3.org/1999/xhtml'><body><p>hello</p></body></html>"

    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        archive.writestr("META-INF/container.xml", container)
        archive.writestr("content.opf", opf)
        archive.writestr("text.html", text)
    return path
