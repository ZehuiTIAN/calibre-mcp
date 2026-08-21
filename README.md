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

## Tools

| Tool | Description |
| --- | --- |
| `library_info` | Library path, number of books, calibre version |
| `search_books` | Full [calibre query language](https://manual.calibre-ebook.com/search_interface.html) search (`author:asimov`, `title:"i robot"`, `tags:python`, ...) |
| `list_books` | Browse the library, defaults to most recently added first |
| `add_books` | Import ebook files or whole directories, with duplicate detection |
| `import_inbox` | Import everything in the inbox folder and file it away |

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
| `CALIBREDB_TIMEOUT` | Seconds before a calibredb call is aborted | `300` |

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

把本地 Calibre 书库接给 Claude/Cursor 等 AI 助手的 MCP 服务器:五个工具
(`library_info` / `search_books` / `list_books` / `add_books` / `import_inbox`),
自动发现 calibredb 和书库位置,全平台(Windows/macOS/Linux)开箱即用。

```bash
pipx install calibre-mcp
claude mcp add calibre-mcp -- calibre-mcp
```

导入时自动查重(书名+作者相同的书不会被重复导入);配合
`CALIBRE_INBOX_DIR` 收件箱目录,把下载的书丢进去、说一句"导入收件箱"即可
归档,处理完的文件自动归类到 `imported/` 或 `failed/`。

注意:Calibre 桌面程序打开着同一书库时,写入操作可能因数据库锁失败,
导入前先关闭 Calibre。
