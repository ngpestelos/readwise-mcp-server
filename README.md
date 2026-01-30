# Readwise MCP Server

Minimal Python MCP server for Readwise integration - token-efficient, single-file implementation using FastMCP.

## Features

- **Token-efficient**: 10 essential tools (vs 13+ in Node.js version)
- **Single-file architecture**: ~1,000 lines of code
- **Proven logic**: Reuses battle-tested deduplication and pagination from backfill script
- **State compatibility**: Preserves existing state file format
- **Smart optimization**: Uses synced ranges to skip unnecessary API calls
- **Highlights import**: Dedicated tools for importing all highlights with temporal organization

## Installation

### 1. Clone repository

```bash
git clone https://github.com/ngpestelos/readwise-mcp-server.git
cd readwise-mcp-server
```

### 2. Create virtual environment

```bash
python3 -m venv venv
source venv/bin/activate
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Configure environment variables

Set these in your `.mcp.json` file:

- `READWISE_TOKEN`: Your Readwise API token
- `VAULT_PATH`: Path to your PARA vault (e.g., `/path/to/your/vault`)

## Configuration

### Update .mcp.json

Add or update the `readwise` entry in your vault's `.mcp.json`:

```json
{
  "mcpServers": {
    "readwise": {
      "command": "/absolute/path/to/readwise-mcp-server/venv/bin/python",
      "args": ["/absolute/path/to/readwise-mcp-server/server.py"],
      "env": {
        "READWISE_TOKEN": "your_token_here",
        "VAULT_PATH": "/absolute/path/to/your/vault"
      }
    }
  }
}
```

Replace the paths with your actual installation locations.

## Tools Reference

### 1. `readwise_daily_review()`

Fetch today's highlights and save to Daily Reviews directory.

**Parameters**: None

**Returns**:
```json
{
  "status": "success",
  "count": 42,
  "file": "/path/to/daily-review.md"
}
```

**Example**:
```
Call readwise_daily_review()
```

### 2. `readwise_import_recent(category="tweet", limit=20)`

Import recent documents since last import with automatic deduplication.

**Parameters**:
- `category` (string, optional): Document category (default: "tweet")
- `limit` (int, optional): Maximum documents to fetch (default: 20)

**Returns**:
```json
{
  "status": "success",
  "imported": 5,
  "skipped": 15,
  "total_analyzed": 20
}
```

**Example**:
```
Call readwise_import_recent(category="article", limit=50)
```

### 3. `readwise_backfill(target_date, category="tweet")`

Paginate backwards to target date with synced range optimization.

**Parameters**:
- `target_date` (string, required): Target date in YYYY-MM-DD format
- `category` (string, optional): Document category (default: "tweet")

**Returns**:
```json
{
  "status": "success",
  "imported": 67,
  "skipped": 433,
  "pages": 10,
  "reached_target": true
}
```

**Example**:
```
Call readwise_backfill(target_date="2026-01-01")
```

### 4. `readwise_book_highlights(title=None, book_id=None)`

Get highlights for a specific book.

**Parameters**:
- `title` (string, optional): Book title to search for
- `book_id` (string, optional): Specific book ID

**Returns**:
```json
{
  "status": "success",
  "count": 15,
  "highlights": [...]
}
```

**Example**:
```
Call readwise_book_highlights(title="Atomic Habits")
```

### 5. `readwise_search_highlights(query, limit=50)`

Search highlights by text query.

**Parameters**:
- `query` (string, required): Search query
- `limit` (int, optional): Maximum results (default: 50)

**Returns**:
```json
{
  "status": "success",
  "count": 8,
  "highlights": [...]
}
```

**Example**:
```
Call readwise_search_highlights(query="productivity")
```

### 6. `readwise_state_info()`

Show current import state and synced ranges.

**Parameters**: None

**Returns**:
```json
{
  "status": "success",
  "last_import": "2026-01-22T04:29:12Z",
  "oldest_imported": "2026-01-01",
  "synced_ranges": [...],
  "backfill_in_progress": false,
  "documents_on_disk": 1044,
  "documents_with_ids": 614
}
```

**Example**:
```
Call readwise_state_info()
```

### 7. `readwise_init_ranges()`

Scan filesystem to build synced_ranges from existing documents.

**Parameters**: None

**Returns**:
```json
{
  "status": "success",
  "range": {
    "start": "2026-01-01T00:00:00Z",
    "end": "2026-01-21T00:00:00Z",
    "doc_count": 614
  },
  "documents_analyzed": 614
}
```

**Example**:
```
Call readwise_init_ranges()
```

### 8. `readwise_reset_state(clear_ranges=False)`

Clear state file (optionally preserve synced_ranges).

**Parameters**:
- `clear_ranges` (bool, optional): Whether to clear synced ranges (default: False)

**Returns**:
```json
{
  "status": "success",
  "message": "State reset",
  "cleared_ranges": false
}
```

**Example**:
```
Call readwise_reset_state(clear_ranges=True)
```

### 9. `readwise_import_recent_highlights(limit=100)`

Import recent highlights across all sources since last import.

**Parameters**:
- `limit` (int, optional): Maximum highlights to fetch (default: 100)

**Returns**:
```json
{
  "status": "success",
  "imported": 42,
  "skipped": 58,
  "total_analyzed": 100
}
```

**Example**:
```
Call readwise_import_recent_highlights(limit=200)
```

**Saves to**: `2 Resources/Readwise/Highlights/YYYYMMDD-HHMMSS [Source Title] highlight.md`

### 10. `readwise_backfill_highlights(target_date)`

Paginate highlights backwards to target date with synced range optimization.

**Parameters**:
- `target_date` (string, required): Target date in YYYY-MM-DD format

**Returns**:
```json
{
  "status": "success",
  "imported": 127,
  "skipped": 873,
  "pages": 15,
  "reached_target": true
}
```

**Example**:
```
Call readwise_backfill_highlights(target_date="2026-01-01")
```

**Saves to**: `2 Resources/Readwise/Highlights/YYYYMMDD-HHMMSS [Source Title] highlight.md`

**Filename Format**: Highlights are saved with temporal prefix for chronological sorting:
- `20260130-143020 [Building a Second Brain] highlight.md`
- `20260130-142815 [The Phoenix Project] highlight.md`

## State File Format

The server maintains state at `.claude/state/readwise-import.json`:

```json
{
  "last_import_timestamp": "2026-01-22T04:29:12.864733Z",
  "oldest_imported_date": "2026-01-01",
  "synced_ranges": [
    {
      "start": "2026-01-01T06:17:43.693000+00:00",
      "end": "2026-01-21T02:33:27.975000+00:00",
      "doc_count": 614,
      "verified_at": "2026-01-21T10:43:56.626549Z"
    }
  ],
  "backfill_in_progress": false,
  "highlights": {
    "last_import_timestamp": "2026-01-30T05:00:00Z",
    "synced_ranges": [],
    "backfill_in_progress": false
  }
}
```

**Highlights State**: The `highlights` section tracks separate import state for highlights:
- `last_import_timestamp`: Last highlights import time
- `synced_ranges`: Optimized ranges for highlights backfill
- `backfill_in_progress`: Whether a highlights backfill was interrupted

**Backward Compatibility**: Existing state files without the `highlights` section will have it automatically created on first use.

## Testing

Run the test suite:

```bash
source venv/bin/activate
pytest test_server.py -v
```

### Test Categories

**Unit Tests**:
- State file reading/writing
- Synced range optimization logic
- Filename sanitization
- ID extraction from URLs
- Document scanning for deduplication

**Integration Tests**:
- API calls with mocked responses
- Markdown formatting
- Document saving with collision handling

## Troubleshooting

### Connection Issues

1. Check MCP connection:
   ```
   /mcp
   ```
   Should show "Connected to readwise"

2. Verify environment variables are set correctly in `.mcp.json`

3. Check logs in stderr output

### State Issues

If state file appears corrupted:

1. View current state:
   ```
   Call readwise_state_info()
   ```

2. Reset state (preserve ranges):
   ```
   Call readwise_reset_state()
   ```

3. Rebuild ranges from filesystem:
   ```
   Call readwise_init_ranges()
   ```

### Deduplication Issues

If documents are being imported multiple times:

1. Rebuild synced ranges:
   ```
   Call readwise_init_ranges()
   ```

2. Check filesystem for duplicate filenames manually

3. Verify readwise_url frontmatter is present in existing documents

## Architecture

### Single-File Design

The server is intentionally kept to a single file (~350 lines) for:
- Simplicity and maintainability
- Easy deployment and updates
- Minimal dependencies
- Clear code organization

### Reused Logic

Key functions reused from `.claude/scripts/readwise-backfill.py`:
- `load_state()` / `write_state()` - State management
- `optimize_backfill()` - Synced range optimization
- `scan_existing_documents()` - Filesystem deduplication
- `sanitize_filename()` - Safe filename generation
- `extract_id_from_url()` - ID extraction

### Token Efficiency

Optimizations for reduced token usage:
- 8 tools vs 13+ in Node.js version (38% reduction)
- Combined operations (fetch + dedupe + save in one call)
- Smart defaults minimize required parameters
- Tool descriptions limited to 20-30 words
- Returns structured summaries, not full markdown dumps

**Estimated token overhead reduction: ~60%**

## Comparison with Node.js Version

| Feature | Python MCP | Node.js MCP |
|---------|------------|-------------|
| Lines of code | ~1,000 | ~6,749 |
| Tool count | 10 | 13+ |
| Dependencies | 4 | 10+ |
| State compatibility | ✓ | ✓ |
| Token efficiency | High | Medium |
| Maintenance | Simple | Complex |
| Highlights import | ✓ | ✗ |

## Development

### Running locally for development

```bash
source venv/bin/activate
python server.py
```

### Adding new tools

1. Add tool function using `@mcp.tool()` decorator
2. Follow existing patterns for error handling and logging
3. Return structured dict with `status` field
4. Add tests to `test_server.py`
5. Update README.md with tool documentation

## License

MIT

## Credits

- Built with [FastMCP](https://github.com/anthropics/fastmcp) by Anthropic
- Based on proven logic from `.claude/scripts/readwise-backfill.py`
- Designed for token efficiency and simplicity
