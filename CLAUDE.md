# Readwise MCP Server - Project Instructions

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**Minimal Python MCP Server for Readwise Integration**

This is a token-efficient, single-file Python MCP server that provides Readwise API access through the Model Context Protocol (MCP). It replaces a broken Node.js implementation with a clean, maintainable Python implementation using the FastMCP framework.

**Status**: Production-ready (2026-01-23)

## Core Philosophy

### Simplicity Over Abstraction
- Single-file architecture (~371 lines)
- No unnecessary abstractions or over-engineering
- Clear, readable code over clever tricks
- Direct approach: one function = one purpose

### Token Efficiency
- 8 essential tools vs 13+ in previous implementation
- Combined operations (fetch + dedupe + save)
- Smart defaults minimize required parameters
- Structured returns, no raw markdown dumps
- ~60% token overhead reduction

### Battle-Tested Logic
Reuses proven logic from `.claude/scripts/readwise-backfill.py`:
- State management
- Synced range optimization
- Filesystem deduplication
- Filename sanitization
- ID extraction

## Project Structure

```
readwise-mcp-server/
├── server.py              # Main MCP server (371 lines)
├── test_server.py         # Test suite (340 lines, 21 tests)
├── requirements.txt       # Python dependencies (6 packages)
├── README.md              # User documentation
├── CLAUDE.md              # This file - Claude Code instructions
├── IMPLEMENTATION_SUMMARY.md  # Technical implementation details
└── venv/                  # Python virtual environment
```

## Technical Stack

- **Framework**: FastMCP (official Anthropic MCP library)
- **HTTP Client**: requests (simple, reliable)
- **Testing**: pytest + pytest-asyncio
- **Python**: 3.9+ (uses timezone.utc for compatibility)
- **Type Hints**: Full typing for IDE support

## 8 Essential Tools

1. **readwise_daily_review()** - Fetch today's highlights to Daily Reviews/
2. **readwise_import_recent(category, limit)** - Import recent docs since last import
3. **readwise_backfill(target_date, category)** - Paginate to target with optimization
4. **readwise_book_highlights(title, book_id)** - Get book highlights
5. **readwise_search_highlights(query, limit)** - Search highlights by text
6. **readwise_state_info()** - Show current state and synced ranges
7. **readwise_init_ranges()** - Scan filesystem to build synced_ranges
8. **readwise_reset_state(clear_ranges)** - Clear state file

## Core Principles

### 1. Never Break State Compatibility
The state file at `.claude/state/readwise-import.json` has a specific format:
```json
{
  "last_import_timestamp": "2026-01-22T04:29:12.864733Z",
  "oldest_imported_date": "2026-01-01",
  "synced_ranges": [...],
  "backfill_in_progress": false
}
```
**Do NOT change this format** without careful consideration and user approval.

### 2. Maintain Single-File Architecture
All server code should remain in `server.py`. Do not split into multiple modules unless:
- File exceeds 1000 lines
- Clear separation of concerns emerges naturally
- User explicitly requests modularization

### 3. Preserve Test Coverage
All changes to `server.py` must have corresponding tests in `test_server.py`. Current coverage:
- State management
- Optimization logic
- Filename handling
- Document scanning
- Markdown formatting
- Document saving

### 4. Token Efficiency First
When adding features:
- Combine operations where possible
- Use smart defaults
- Return structured summaries, not full content
- Keep tool descriptions under 30 words

### 5. Error Handling Pattern
All tools follow this pattern:
```python
@mcp.tool()
async def tool_name(param: type = default) -> dict:
    """Brief description (20-30 words max)"""
    try:
        # Tool logic here
        return {
            "status": "success",
            "key": value,
            ...
        }
    except Exception as e:
        logger.error(f"Error in tool_name: {e}")
        return {"status": "error", "message": str(e)}
```

## Development Workflow

### Making Changes

1. **Update server.py**
   - Maintain single-file structure
   - Add type hints
   - Follow existing error handling pattern
   - Log operations to stderr

2. **Update tests**
   - Add unit tests for new functions
   - Add integration tests for new tools
   - Maintain 100% test passing rate

3. **Update README.md**
   - Document new tools
   - Add examples
   - Update tool count if changed

4. **Run tests**
   ```bash
   source venv/bin/activate
   pytest test_server.py -v
   ```

### Testing Strategy

**Unit Tests** (fast, no API calls):
- State file operations
- Optimization logic
- Filename sanitization
- ID extraction
- Markdown formatting

**Integration Tests** (mocked API):
- Tool behavior
- Deduplication flow
- Document saving

**Manual Testing** (real API):
- MCP connection
- State reading
- Real imports
- Backfill optimization

### Git Workflow

```bash
# Standard workflow
git add .
git commit -m "Clear, descriptive message"
git push origin master

# Before committing
pytest test_server.py -v  # Ensure tests pass
```

## Common Tasks

### Adding a New Tool

1. Define tool function with `@mcp.tool()` decorator
2. Add type hints for all parameters
3. Include docstring (20-30 words)
4. Return structured dict with `status` field
5. Add error handling with try/except
6. Add tests to `test_server.py`
7. Update README.md with tool documentation

Example:
```python
@mcp.tool()
async def new_tool_name(param: str, limit: int = 10) -> dict:
    """Brief description of what this tool does"""
    try:
        # Implementation
        return {"status": "success", "result": data}
    except Exception as e:
        logger.error(f"Error in new_tool_name: {e}")
        return {"status": "error", "message": str(e)}
```

### Modifying Existing Tools

1. Read relevant test in `test_server.py` to understand behavior
2. Make changes to `server.py`
3. Update or add tests as needed
4. Run full test suite
5. Update README.md if behavior changed

### Debugging Issues

1. Check stderr logs (FastMCP logs all operations)
2. Use `readwise_state_info()` to inspect state
3. Run unit tests to isolate issue: `pytest test_server.py::TestClassName::test_name -v`
4. Add temporary logging if needed
5. Test with real API using small limits

### Updating Dependencies

```bash
source venv/bin/activate
pip install --upgrade package-name
pip freeze > requirements.txt
pytest test_server.py -v  # Verify nothing broke
```

## Important Constraints

### DO NOT

- **Break state file format** without user approval
- **Add unnecessary dependencies** (keep it minimal)
- **Split into multiple files** without clear justification
- **Remove type hints** (they're valuable for IDE support)
- **Skip tests** when adding features
- **Change tool return format** (always return dict with `status`)
- **Use subprocess** for API calls (use requests library)
- **Import credentials** directly in code (use environment variables)

### DO

- **Keep it simple** (single file, clear code)
- **Maintain tests** (all changes need tests)
- **Log operations** (use logger.info/error)
- **Type everything** (parameters, returns, variables)
- **Follow patterns** (look at existing tools)
- **Document changes** (update README.md)
- **Think token efficiency** (combined operations, smart defaults)

## Configuration

### Environment Variables (set in .mcp.json)
- `READWISE_TOKEN` - API authentication token (required)
- `VAULT_PATH` - Path to PARA vault (required)

### Paths (derived from VAULT_PATH)
- `STATE_FILE` - `.claude/state/readwise-import.json`
- `DOCUMENTS_DIR` - `2 Resources/Readwise/Documents`
- `DAILY_REVIEWS_DIR` - `2 Resources/Readwise/Daily Reviews`
- `ARCHIVES_DIR` - `3 Archives/Readwise`

## API Endpoints Used

- **Reader API v3** (`/api/v3/list/`) - Documents (tweets, articles, PDFs)
- **Highlights API v2** (`/api/v3/highlights/`) - Highlights from books

See `.claude/commands/readwise-import.md` in PARA vault for complete API documentation.

## Performance Characteristics

### Deduplication Strategy
1. **Filesystem scan** - Build set of known IDs and filenames at start
2. **In-memory check** - O(1) lookup for each document
3. **Session tracking** - Track imported docs to avoid duplicates within session

### Pagination Optimization
- **Synced ranges** - Skip paginating through already-imported date ranges
- **Target detection** - Stop pagination when reaching target date
- **Cursor-based** - Use API's nextPageCursor for efficient traversal

### API Efficiency
- **Default limits** - 20 for recent imports, 50 for backfill
- **Smart updatedAfter** - Use last_import_timestamp to skip old docs
- **Combined operations** - Single tool call does fetch + dedupe + save

## Maintenance Guidelines

### When to Refactor

Refactor if:
- File exceeds 1000 lines (currently 371)
- Duplicate code appears 3+ times
- Function exceeds 100 lines
- Test suite becomes slow (currently 0.33s)

Do NOT refactor for:
- "Better" abstractions (YAGNI principle)
- Following trendy patterns
- Splitting into modules "because that's how it's done"

### Code Review Checklist

Before committing:
- [ ] All tests pass
- [ ] New code has tests
- [ ] Type hints added
- [ ] Error handling present
- [ ] Logging added for operations
- [ ] README.md updated if needed
- [ ] State file format preserved
- [ ] Token efficiency considered

### Version Management

This project does not use semantic versioning. Changes are tracked via:
- Git commits
- IMPLEMENTATION_SUMMARY.md updates
- README.md changelog section

## Integration with PARA Vault

### Import Destinations
- **Documents**: `2 Resources/Readwise/Documents/` (primary)
- **Daily Reviews**: `2 Resources/Readwise/Daily Reviews/` (highlights)
- **Archives**: `3 Archives/Readwise/` (old content)

### State Management
- **State file**: `.claude/state/readwise-import.json`
- **Shared with**: Other Readwise import tools in vault
- **Critical**: Do not break format compatibility

### Workflow Integration
This MCP server is used by Claude Code when:
- User requests "import recent Readwise documents"
- User wants to search highlights
- User needs daily review
- User wants to backfill to specific date

## Troubleshooting Guide

### MCP Connection Failed
1. Check `.mcp.json` configuration
2. Verify venv Python path: `/Users/ngpestelos/src/readwise-mcp-server/venv/bin/python`
3. Test server directly: `python server.py` (should show FastMCP startup)
4. Check Claude Code logs for connection errors

### State File Issues
1. View state: `readwise_state_info()`
2. Rebuild ranges: `readwise_init_ranges()`
3. Reset if corrupted: `readwise_reset_state(clear_ranges=True)`

### Deduplication Not Working
1. Check frontmatter format in existing docs (need `readwise_url` field)
2. Verify filename matching logic
3. Scan filesystem: `readwise_init_ranges()`
4. Check logs for ID extraction failures

### Tests Failing
1. Check Python version (need 3.9+)
2. Verify dependencies: `pip install -r requirements.txt`
3. Run single test: `pytest test_server.py::TestClass::test_name -v`
4. Check for changed state file format

### API Errors
1. Verify READWISE_TOKEN in `.mcp.json`
2. Check API rate limits (unlikely with default limits)
3. Test with smaller limit: `readwise_import_recent(limit=5)`
4. Check Readwise API status

## Testing Commands

```bash
# Run all tests
pytest test_server.py -v

# Run specific test class
pytest test_server.py::TestStateManagement -v

# Run specific test
pytest test_server.py::TestStateManagement::test_load_state_existing -v

# Run with coverage
pytest test_server.py --cov=server --cov-report=term-missing

# Run tests and stop on first failure
pytest test_server.py -x

# Run tests with output
pytest test_server.py -v -s
```

## Success Metrics

Current status:
- ✅ 21/21 tests passing (100%)
- ✅ Single file implementation (371 lines)
- ✅ 8 essential tools (38% fewer than Node.js version)
- ✅ Token efficiency: ~60% overhead reduction
- ✅ Zero dependencies on Node.js
- ✅ Production-ready

Maintain these metrics when making changes.

## Related Documentation

- **README.md** - User guide and tool reference
- **IMPLEMENTATION_SUMMARY.md** - Technical implementation details
- **test_server.py** - Working examples of all functions
- **PARA/.claude/commands/readwise-import.md** - Readwise API documentation
- **PARA/.claude/scripts/readwise-backfill.py** - Original proven logic

## Contact & Support

This is a personal tool for integration with a private PARA vault. For issues or questions:
1. Check this CLAUDE.md file
2. Review README.md and IMPLEMENTATION_SUMMARY.md
3. Run tests to verify behavior
4. Check logs in stderr output

## Changelog

### 2026-01-23 - Initial Implementation
- Created minimal Python MCP server using FastMCP
- Implemented 8 essential tools
- Achieved 100% test coverage (21 tests)
- Replaced broken Node.js implementation
- Token efficiency: ~60% overhead reduction
- Single-file architecture: 371 lines
