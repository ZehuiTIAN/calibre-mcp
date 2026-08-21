"""OCR for scanned/image PDFs, with a pluggable cloud backend.

Detection and page rendering use PyMuPDF (no system dependencies); the OCR
backend is selected with ``CALIBRE_OCR_PROVIDER`` and must implement the
``OcrProvider`` protocol. The Anthropic Messages API ships as the reference
implementation — other providers (Azure Document Intelligence, Aliyun/Baidu
OCR, ...) can be added by implementing the same two methods.

Pipeline: detect scanned PDF → render pages → provider returns structured
markdown → cached, fed into the full-text index, and optionally typeset into
a replacement EPUB with pandoc, attached to the existing book record via
``calibredb add_format``.

Requires the ``ocr`` extra: ``pip install calibre-mcp[ocr]``.
"""

from __future__ import annotations

import base64
import hashlib
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from calibre_mcp import calibre as calibre_cli
from calibre_mcp import fulltext
from calibre_mcp.config import Settings

# ------------------------------------------------------------------ providers


class OcrProvider(Protocol):
    """Minimal interface a cloud OCR backend must implement."""

    name: str

    def ocr_pages(self, page_images: list[bytes], context: dict[str, Any]) -> str:
        """Return structured markdown for the given page images."""
        ...


OCR_PROMPT = (
    "Transcribe these book pages into clean Markdown. Preserve chapter "
    "headings (as # / ## headings), paragraphs, and reading order. Skip "
    "running headers, page numbers and footnotes. Do not add any commentary "
    "before or after the transcription."
)


class AnthropicProvider:
    """Reference implementation using the Anthropic Messages API (vision)."""

    name = "anthropic"

    def __init__(self, api_key: str, model: str, base_url: str | None = None) -> None:
        self._api_key = api_key
        self._model = model or "claude-haiku-4-5-20251001"
        self._base_url = base_url

    def ocr_pages(self, page_images: list[bytes], context: dict[str, Any]) -> str:
        import anthropic  # lazy: only needed when this provider is used

        client = anthropic.Anthropic(api_key=self._api_key, base_url=self._base_url)
        parts: list[str] = []
        batch_size = 8  # keep a request comfortably inside context limits
        for index in range(0, len(page_images), batch_size):
            batch = page_images[index : index + batch_size]
            content: list[dict[str, Any]] = [{"type": "text", "text": OCR_PROMPT}]
            for image in batch:
                content.append(
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": base64.b64encode(image).decode(),
                        },
                    }
                )
            response = client.messages.create(
                model=self._model,
                max_tokens=8192,
                messages=[{"role": "user", "content": content}],
            )
            parts.extend(
                block.text for block in response.content if getattr(block, "type", "") == "text"
            )
        return "\n\n".join(parts)


class DashscopeProvider:
    """Alibaba Cloud Bailian (百炼) qwen-vl-ocr via the OpenAI-compatible API.

    Requires no extra SDK: plain HTTPS against dashscope.aliyuncs.com.
    The default model (qwen-vl-ocr) is purpose-built for document OCR with
    layout/structure output, and is priced per token (~0.3 元 per 200-page
    book — see docs/OCR_PROVIDERS.md).
    """

    name = "dashscope"

    def __init__(self, api_key: str, model: str, base_url: str | None = None) -> None:
        self._api_key = api_key
        self._model = model or "qwen-vl-ocr"
        self._base_url = base_url or "https://dashscope.aliyuncs.com/compatible-mode/v1"

    def ocr_pages(self, page_images: list[bytes], context: dict[str, Any]) -> str:
        import json
        import urllib.request

        parts: list[str] = []
        batch_size = 8  # keep a request comfortably inside context limits
        for index in range(0, len(page_images), batch_size):
            batch = page_images[index : index + batch_size]
            content: list[dict[str, Any]] = [{"type": "text", "text": OCR_PROMPT}]
            for image in batch:
                content.append(
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": "data:image/png;base64," + base64.b64encode(image).decode()
                        },
                    }
                )
            payload = json.dumps(
                {
                    "model": self._model,
                    "max_tokens": 8192,
                    "messages": [{"role": "user", "content": content}],
                }
            ).encode("utf-8")
            request = urllib.request.Request(
                f"{self._base_url}/chat/completions",
                data=payload,
                method="POST",
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
            )
            with urllib.request.urlopen(request, timeout=600) as response:
                body = json.loads(response.read().decode("utf-8"))
            parts.append(body["choices"][0]["message"]["content"])
        return "\n\n".join(parts)


PROVIDERS: dict[str, type[OcrProvider]] = {
    "anthropic": AnthropicProvider,
    "dashscope": DashscopeProvider,
}


@dataclass
class OcrConfig:
    """OCR configuration, read from environment variables."""

    provider: str = "anthropic"
    api_key: str | None = None
    model: str | None = None
    base_url: str | None = None
    max_pages: int = 500

    @classmethod
    def from_env(cls) -> OcrConfig:
        return cls(
            provider=os.environ.get("CALIBRE_OCR_PROVIDER", "anthropic"),
            api_key=os.environ.get("CALIBRE_OCR_API_KEY"),
            model=os.environ.get("CALIBRE_OCR_MODEL"),
            base_url=os.environ.get("CALIBRE_OCR_BASE_URL"),
            max_pages=int(os.environ.get("CALIBRE_OCR_MAX_PAGES", "500")),
        )


def get_provider(config: OcrConfig) -> OcrProvider:
    """Instantiate the configured OCR provider."""
    provider_class = PROVIDERS.get(config.provider)
    if provider_class is None:
        available = ", ".join(sorted(PROVIDERS))
        raise RuntimeError(
            f"unknown OCR provider {config.provider!r} (available: {available}); "
            "set CALIBRE_OCR_PROVIDER"
        )
    if config.api_key is None:
        raise RuntimeError(
            f"OCR provider {config.provider!r} needs CALIBRE_OCR_API_KEY"
        )
    return provider_class(config.api_key, config.model or "", config.base_url)


# ----------------------------------------------------------- PDF inspection


def _pdf_document(pdf_path: Path) -> Any:
    try:
        import pymupdf
    except ImportError as exc:
        raise RuntimeError(
            "pymupdf is required for OCR; install the extra: pip install 'calibre-mcp[ocr]'"
        ) from exc
    pymupdf.TOOLS.mupdf_display_errors(False)  # malformed PDFs would flood stderr
    return pymupdf.open(pdf_path)


SAMPLE_PAGES = 20


def pdf_text_stats(pdf_path: Path) -> dict[str, Any]:
    """Text-layer statistics, sampled over the first pages for speed.

    A scanned book has image-only pages throughout, so the first pages are
    representative; sampling keeps detection fast even for huge PDFs.
    """
    document = _pdf_document(pdf_path)
    try:
        page_count = len(document)
        sample = min(page_count, SAMPLE_PAGES)
        text_pages = sum(
            1 for page in document[:sample] if len(page.get_text("text").strip()) >= 40
        )
    finally:
        document.close()
    return {"pages": page_count, "sampled_pages": sample, "text_pages": text_pages}


def is_scanned(pdf_path: Path) -> tuple[bool, dict[str, Any]]:
    """A PDF is treated as scanned when most pages have no text layer."""
    stats = pdf_text_stats(pdf_path)
    if stats["pages"] == 0:
        return False, stats
    sampled = max(stats.get("sampled_pages", 1), 1)
    ratio = stats["text_pages"] / sampled
    return ratio < 0.5, {**stats, "text_page_ratio": round(ratio, 3)}


def render_pages(pdf_path: Path, dpi: int = 150, max_pages: int = 500) -> list[bytes]:
    """Render the PDF's pages to PNG bytes (truncated at max_pages)."""
    document = _pdf_document(pdf_path)
    images: list[bytes] = []
    try:
        for page in document[:max_pages]:
            images.append(page.get_pixmap(dpi=dpi).tobytes("png"))
    finally:
        document.close()
    return images


# ------------------------------------------------------------------ pipeline


def ocr_cache_path(settings: Settings, source: Path) -> Path:
    """Location of the cached OCR markdown for a source file."""
    digest = hashlib.sha1(
        f"{source}:{source.stat().st_mtime_ns}".encode()
    ).hexdigest()[:16]
    target = fulltext.cache_dir() / "ocr" / f"{digest}.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    return target


def typeset_epub(markdown: str, title: str, author: str, out_dir: Path) -> Path:
    """Turn structured markdown into a re-typeset EPUB using pandoc."""
    pandoc = shutil.which("pandoc")
    if pandoc is None:
        raise RuntimeError("pandoc is required for typesetting; install it first")
    out_dir.mkdir(parents=True, exist_ok=True)
    md_path = out_dir / "ocr-source.md"
    epub_path = out_dir / "ocr-typeset.epub"
    md_path.write_text(markdown, encoding="utf-8")
    result = subprocess.run(
        [
            pandoc,
            str(md_path),
            "-o",
            str(epub_path),
            "--metadata",
            f"title={title}",
            "--metadata",
            f"author={author}",
            "--toc",
            "--toc-depth=2",
        ],
        capture_output=True,
        text=True,
        timeout=600,
    )
    if result.returncode != 0 or not epub_path.exists():
        error = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(f"pandoc failed to typeset the EPUB: {error[-200:]}")
    return epub_path


def attach_format(settings: Settings, book_id: int, file_path: Path) -> None:
    """Attach (or replace) a format file on the existing book record."""
    result = calibre_cli.run_calibredb(
        settings, ["add_format", str(book_id), str(file_path)]
    )
    if result.returncode != 0:
        raise RuntimeError(calibre_cli.clean_error(result))


def ocr_book(
    settings: Settings,
    book_id: int,
    typeset: bool = True,
    import_format: bool = True,
) -> dict[str, Any]:
    """OCR a scanned PDF: detect → render → OCR → index → optional EPUB.

    Books with a usable text layer are left untouched. The OCR'd markdown is
    cached, fed into the full-text index, and (by default) typeset into a
    replacement EPUB attached to the book's existing record.
    """
    books = calibre_cli.books_by_ids(settings, {book_id})
    if not books:
        raise RuntimeError(f"no book with id {book_id} in the library")
    meta = books[0]

    paths = fulltext.book_format_paths(settings, book_id)
    pdf_path = next((p for p in paths if p.suffix.lower() == ".pdf"), None)
    if pdf_path is None:
        raise RuntimeError(f"book {book_id} has no PDF format; OCR supports PDFs only")

    scanned, stats = is_scanned(pdf_path)
    if not scanned:
        return {"book_id": book_id, "status": "not_scanned", "stats": stats}

    config = OcrConfig.from_env()
    provider = get_provider(config)
    images = render_pages(pdf_path, max_pages=config.max_pages)
    markdown = provider.ocr_pages(
        images,
        {"book_id": book_id, "title": meta.get("title", ""), "pages": len(images)},
    )

    cache = ocr_cache_path(settings, pdf_path)
    cache.write_text(markdown, encoding="utf-8")
    fulltext.index_text(settings, book_id, markdown, f"ocr:{pdf_path}")

    result: dict[str, Any] = {
        "book_id": book_id,
        "status": "scanned_ocr",
        "stats": stats,
        "pages_processed": len(images),
        "ocr_chars": len(markdown),
        "indexed": True,
        "cache_path": str(cache),
    }

    if typeset:
        epub = typeset_epub(
            markdown,
            meta.get("title") or f"Book {book_id}",
            meta.get("authors") or "Unknown",
            fulltext.cache_dir() / "ocr-epubs",
        )
        result["epub_path"] = str(epub)
        if import_format:
            attach_format(settings, book_id, epub)
            result["format_attached"] = True
    return result


def detect_scanned_books(settings: Settings, limit: int = 50) -> dict[str, Any]:
    """Find library books whose PDF format has no usable text layer."""
    limit = max(1, min(int(limit), 200))
    scanned: list[dict[str, Any]] = []
    for book_id in sorted(calibre_cli.all_book_ids(settings)):
        try:
            paths = fulltext.book_format_paths(settings, book_id)
        except RuntimeError:
            continue
        pdf_path = next((p for p in paths if p.suffix.lower() == ".pdf"), None)
        if pdf_path is None:
            continue
        try:
            is_image_pdf, stats = is_scanned(pdf_path)
        except RuntimeError:
            continue
        if is_image_pdf:
            scanned.append({"book_id": book_id, **stats})
            if len(scanned) >= limit:
                break
    return {"scanned_count": len(scanned), "books": scanned}
