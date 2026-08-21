# calibre-mcp

An [MCP](https://modelcontextprotocol.io) server that gives AI assistants
(Claude Code, Claude Desktop, Cursor, ...) read and write access to your local
[Calibre](https://calibre-ebook.com) ebook library.

Search the library, browse recent additions, import new ebooks with automatic
duplicate detection, and manage a "drop folder" inbox — all from chat.

- **Zero-config on a normal setup**: the calibredb executable and the library
  your Calibre GUI is using are discovered automatically on macOS, Windows
  and Linux.
- **Locale-independent**: all reads go through `calibredb --for-machine`
  (JSON), so results never depend on your Calibre interface language.
- **Safe by default**: duplicate detection relies on Calibre itself — a book
  whose title + author is already in the library is skipped, never imported
  twice.
- **Reads book contents**: a local full-text index (with CJK support) plus
  paged text reading, built on Calibre's own converter — great for quoting
  and summarising.

## Tools

| Tool | Description |
| --- | --- |
| `library_info` | Library path, number of books, calibre version |
| `search_books` | Full [calibre query language](https://manual.calibre-ebook.com/search_interface.html) search (`author:asimov`, `title:"i robot"`, `tags:python`, ...) |
| `list_books` | Browse the library, defaults to most recently added first |
| `add_books` | Import ebook files or whole directories, with duplicate detection |
| `import_inbox` | Import everything in the inbox folder and file it away |
| `build_index` | One-time index of book contents for full-text search |
| `search_in_book` | Search inside book contents, returning match snippets |
| `read_book` | Read a book's plain text page by page (converted on demand) |
| `detect_scanned_books` | Find books whose PDF is scanned (no text layer) |
| `ocr_book` | OCR a scanned PDF via a cloud provider, index it, and re-typeset it into an EPUB |

## Requirements

- [Calibre](https://calibre-ebook.com) (any version that ships `calibredb`; tested with 9.x)
- Python ≥ 3.10

## Installation

### pipx (recommended)

```bash
pipx install calibre-mcp
```

### uv

```bash
uv tool install calibre-mcp
```

### pip

```bash
pip install calibre-mcp
```

### From source

```bash
git clone https://github.com/ZehuiTIAN/calibre-mcp.git
cd calibre-mcp
pip install -e ".[dev]"     # dev extras add pytest + ruff
```

## Configuration

Everything is optional on a normal setup; each value can be pinned with an
environment variable.

| Variable | Meaning | Default |
| --- | --- | --- |
| `CALIBRE_LIBRARY_PATH` | Library directory to use | The library your Calibre GUI uses (most-used library from `gui.json`, else the default library) |
| `CALIBREDB_PATH` | Path to the `calibredb` executable | `calibredb` on `PATH`, then platform install locations (macOS app bundle, `C:\Program Files\Calibre2\`) |
| `CALIBRE_INBOX_DIR` | Drop folder for `import_inbox` | Disabled (tool errors if used) |
| `CALIBRE_TEXT_CACHE_DIR` | Where converted text + search index live | OS temp dir (not persistent across reboots) |
| `CALIBREDB_TIMEOUT` | Seconds before a calibredb call is aborted | `300` |
| `CALIBRE_OCR_PROVIDER` | OCR backend (`anthropic`) | `anthropic` |
| `CALIBRE_OCR_API_KEY` | API key for the OCR backend | — (required for `ocr_book`) |
| `CALIBRE_OCR_MODEL` | Model for the OCR backend | provider default |
| `CALIBRE_OCR_BASE_URL` | Optional API base URL override (proxies/gateways) | — |
| `CALIBRE_OCR_MAX_PAGES` | Page cap per `ocr_book` run | `500` |

### Registering with Claude Code

```bash
claude mcp add calibre-mcp -- calibre-mcp
```

or add to `~/.claude.json` → `mcpServers` (user scope):

```json
"calibre_mcp": {
  "type": "stdio",
  "command": "calibre-mcp",
  "args": [],
  "env": {
    "CALIBRE_LIBRARY_PATH": "/path/to/your/library",
    "CALIBRE_INBOX_DIR": "/path/to/your/inbox"
  }
}
```

### Registering with Claude Desktop / Cursor

Add the same JSON block to the MCP servers section of
`claude_desktop_config.json` (Claude Desktop) or Cursor's MCP settings.

## Inbox workflow

A drop folder makes a pleasant "find → drop → archive" loop for books you
download by hand:

1. Point `CALIBRE_INBOX_DIR` at a folder, e.g. `~/Downloads/Calibre-Inbox`.
2. Download ebooks into that folder.
3. Ask your assistant to run `import_inbox`.

Every file is added to the library; successfully added files and duplicates
are filed under `imported/YYYY-MM/`, failures under `failed/` — the inbox
itself stays clean, and nothing is ever overwritten (name collisions get a
`-1`, `-2`, ... suffix).

Supported extensions: `.epub .pdf .mobi .azw .azw3 .djvu .cbz .cbr .fb2 .txt
.md .docx .rtf .lit .prc .pdb .chm .htmlz`.

## Full-text search & reading

To search inside book contents or quote a passage, index the library once:

1. (Optional) point `CALIBRE_TEXT_CACHE_DIR` at a persistent location —
   the default cache lives in the OS temp directory.
2. Ask your assistant to run `build_index`. A first run converts every book
   with Calibre's converter; expect roughly a second or two per book (PDFs
   take longer). Later calls skip books that are already indexed.

Then:

- `search_in_book("机器学习与深度学习", book_id=42)` — substring search,
  works for Chinese without word segmentation and for English; queries
  shorter than three characters fall back to a linear scan so
  two-character Chinese words still match.
- `read_book(42, offset=0, limit=12000)` — paged plain-text reading;
  responses carry `next_offset` and `total_chars`.

The index and converted texts live **outside** the library (a cache
directory); the library itself is never modified by these tools. Note that
calibre's own built-in full-text search is not used: its background
indexing does not reliably extract text outside the GUI on some platforms,
and a self-contained index behaves identically everywhere.

## OCR for scanned PDFs

Calibre has no OCR: a scanned PDF (pages are photos, no text layer) converts
to an EPUB full of page images and stays unsearchable. calibre-mcp closes
that gap with a pluggable cloud OCR pipeline:

```bash
pip install "calibre-mcp[ocr]"     # adds pymupdf + the anthropic SDK
```

1. Set `CALIBRE_OCR_API_KEY` (and optionally `CALIBRE_OCR_PROVIDER` /
   `CALIBRE_OCR_MODEL` / `CALIBRE_OCR_BASE_URL`).
2. `detect_scanned_books()` — lists books whose PDF has no usable text
   layer (no key needed for detection).
3. `ocr_book(42)` — renders the pages, sends them to the provider (default:
   the Anthropic Messages API, 8 pages per request), caches the structured
   markdown, indexes it for `search_in_book` / `read_book`, then typesets a
   clean EPUB with pandoc and attaches it to the existing book record
   (replacing any previous EPUB).

The provider interface is two methods (`ocr_pages`); adding Azure Document
Intelligence or Aliyun/Baidu OCR means implementing that interface and
registering it in `ocr.PROVIDERS`.

**Provider choice**: for Chinese books, [Alibaba Cloud Bailian
`qwen-vl-ocr`](https://help.aliyun.com/zh/model-studio/qwen-vl-ocr) is the
default recommendation — purpose-built document OCR with layout output,
priced at roughly ¥0.3 for a 200-page book (≈1/20 the cost of a general
vision LLM). Full comparison, official links and sign-up steps:
**[docs/OCR_PROVIDERS.md](docs/OCR_PROVIDERS.md)**(中文).

**API key isolation**: keys are per-user — set them in *your* MCP client
config only, never in the repository. Nothing in this project reads keys
from files or embeds them; each user brings their own key.

## Caveats

- **Database lock**: while the Calibre GUI has the library open, write
  operations (`add_books`, `import_inbox`) can fail with a database lock
  error. Read-only tools keep working. Close Calibre (or switch it to another
  library) before importing.
- **Large libraries**: `add_books`/`import_inbox` snapshot the full set of
  book ids around each add, which is fast in practice but is O(number of
  books) per import.
- This tool only organizes *your* library. It contains no search or download
  functionality; use it with content you have the right to hold.

## Development

```bash
pip install -e ".[dev]"
ruff check .
pytest -m "not integration"      # unit tests, run anywhere
pytest                           # + integration tests (need local calibre)
```

Integration tests create throwaway libraries under `/tmp` and never touch
your real library.

## License

MIT — see [LICENSE](LICENSE).

---

## 中文速览

把本地 Calibre 书库接给 Claude/Cursor 等 AI 助手的 MCP 服务器:十个工具
(元数据搜索/浏览/导入查重/收件箱归档 + `build_index`/`search_in_book`/
`read_book` 全文检索与读正文 + `detect_scanned_books`/`ocr_book` 扫描书
云端 OCR 与重排版),自动发现 calibredb 和书库位置,全平台
(Windows/macOS/Linux)开箱即用。

```bash
pipx install calibre-mcp
claude mcp add calibre-mcp -- calibre-mcp
```

导入时自动查重(书名+作者相同的书不会被重复导入);配合
`CALIBRE_INBOX_DIR` 收件箱目录,把下载的书丢进去、说一句"导入收件箱"即可
归档,处理完的文件自动归类到 `imported/` 或 `failed/`。

想引用书里内容:先跑一次 `build_index` 建全文索引(中文子串可直接搜),
之后 `search_in_book` 定位段落、`read_book` 分页读正文。

注意:Calibre 桌面程序打开着同一书库时,写入操作可能因数据库锁失败,
导入前先关闭 Calibre。
