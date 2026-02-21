# Changelog

## 2026-02-22

### Fixed
- **Highlights query tools returning 404**: `readwise_daily_review`, `readwise_book_highlights`, and `readwise_search_highlights` called `/api/v3/highlights/` which does not exist in the Readwise API. Switched all three to use the v2 `/export/` endpoint with nested book→highlights flattening.
- `readwise_daily_review` now includes book title in source attribution.
- `readwise_search_highlights` now searches across highlight text, notes, book title, and author (previously only text and notes).
- `readwise_book_highlights` now returns `highlighted_at` timestamp in results.

## 2026-02-19

### Fixed
- Highlight deduplication bug where highlights sharing the same timestamp were falsely skipped due to base filename collision detection instead of actual saved filename tracking.

## 2026-02-07

### Added
- Highlights import tools: `readwise_import_recent_highlights` and `readwise_backfill_highlights` using v2 `/export/` endpoint.
- Temporal filename format for highlights: `YYYYMMDD-HHMMSS [Source Title] highlight.md`.
- Separate highlights state tracking in state file.

## 2026-02-05

### Added
- API pagination debugging skill.

### Fixed
- Missing attribution by switching document tools to export API.
- Highlight deduplication and backfill pagination alignment.

## 2026-01-30

### Added
- Rate limit handling with exponential backoff (5s, 10s, 20s) and Retry-After header support.
- Request timeout (30s) for all API calls.
- Pagination throttle delay (0.5s) for backfill operations.

## 2026-01-23

### Fixed
- Malformed ISO 8601 timestamps (`+00:00Z` double timezone suffix) causing Readwise API 400 errors.

## 2026-01-22

### Added
- Initial release: 8 essential MCP tools for Readwise integration.
- State management with synced range optimization.
- Filesystem-based deduplication.
- Filename sanitization with fallback for non-alphanumeric titles.
