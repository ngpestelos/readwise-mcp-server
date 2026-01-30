#!/usr/bin/env python3
# Copyright (c) 2026 ngpestelos
# Licensed under the MIT License - see LICENSE file for details
"""
Minimal Python Readwise MCP Server
Token-efficient, single-file implementation using FastMCP
"""

import json
import os
import re
import sys
import time
import urllib.parse
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Dict, List, Tuple

import requests
import yaml
from mcp.server.fastmcp import FastMCP

# Configure logging to stderr
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    stream=sys.stderr
)
logger = logging.getLogger(__name__)

# Configuration from environment variables
READWISE_TOKEN = os.environ.get("READWISE_TOKEN")
VAULT_PATH = Path(os.environ.get("VAULT_PATH", "/Users/ngpestelos/src/PARA"))
STATE_FILE = VAULT_PATH / ".claude/state/readwise-import.json"
DOCUMENTS_DIR = VAULT_PATH / "2 Resources/Readwise/Documents"
DAILY_REVIEWS_DIR = VAULT_PATH / "2 Resources/Readwise/Daily Reviews"
HIGHLIGHTS_DIR = VAULT_PATH / "2 Resources/Readwise/Highlights"
ARCHIVES_DIR = VAULT_PATH / "3 Archives/Readwise"

# Rate limit configuration
RATE_LIMIT_MAX_RETRIES = 3
RATE_LIMIT_BASE_DELAY = 5  # seconds
RATE_LIMIT_MAX_DELAY = 60  # seconds
RATE_LIMIT_BACKOFF_MULTIPLIER = 2  # exponential: 5s, 10s, 20s
REQUEST_TIMEOUT = 30  # seconds
PAGINATION_THROTTLE_DELAY = 0.5  # seconds between pagination requests

# Validate configuration (only when running as main)
def validate_config():
    if not READWISE_TOKEN:
        logger.error("READWISE_TOKEN environment variable not set")
        sys.exit(1)

# ============================================================================
# UTILITY FUNCTIONS (reused from backfill.py)
# ============================================================================

def load_state() -> Dict:
    """Load state file or create default"""
    if STATE_FILE.exists():
        with open(STATE_FILE, 'r') as f:
            state = json.load(f)
            # Backward compatibility: ensure highlights section exists
            if "highlights" not in state:
                state["highlights"] = {
                    "last_import_timestamp": datetime.now(timezone.utc).isoformat(),
                    "synced_ranges": [],
                    "backfill_in_progress": False
                }
            return state
    return {
        "last_import_timestamp": datetime.now(timezone.utc).isoformat(),
        "synced_ranges": [],
        "backfill_in_progress": False,
        "highlights": {
            "last_import_timestamp": datetime.now(timezone.utc).isoformat(),
            "synced_ranges": [],
            "backfill_in_progress": False
        }
    }

def write_state(state: Dict) -> None:
    """Write state file"""
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(STATE_FILE, 'w') as f:
        json.dump(state, f, indent=2)

def optimize_backfill(target_date: str, synced_ranges: List[Dict]) -> Tuple[bool, Optional[str]]:
    """
    Check synced_ranges before pagination to skip already-synced content.

    Returns:
        (should_proceed, optimized_updated_after)
    """
    if not synced_ranges:
        return (True, None)

    # Convert target date to timestamp (timezone-aware)
    target_ts = datetime.fromisoformat(target_date + "T00:00:00+00:00")

    # Sort ranges by start timestamp
    ranges = sorted(synced_ranges, key=lambda r: r['start'])

    for range_item in ranges:
        range_start = datetime.fromisoformat(range_item['start'].replace('Z', '+00:00'))
        range_end = datetime.fromisoformat(range_item['end'].replace('Z', '+00:00'))

        # Case 1: Target date falls within synced range
        if range_start <= target_ts <= range_end:
            logger.info(f"Target date {target_date} already synced (range: {range_item['start']} to {range_item['end']})")
            return (False, None)  # Skip - already synced

        # Case 2: Target date is before synced range
        if target_ts < range_start:
            # Gap exists between target and synced range start
            # Don't use updatedAfter - we need to fill the gap
            # Pagination will stop when hitting target date
            # Deduplication will handle overlap with synced range
            logger.info(f"Gap detected: target {target_date} is before synced range {range_item['start']}")
            logger.info(f"Will paginate to fill gap (no updatedAfter filter)")
            return (True, None)  # No filter - fill the gap

    # Case 3: Target date is after all ranges
    return (True, None)

def scan_existing_documents() -> Tuple[set, set]:
    """Scan filesystem to build known IDs and filenames"""
    known_ids = set()
    known_filenames = set()

    for directory in [DOCUMENTS_DIR, ARCHIVES_DIR, DAILY_REVIEWS_DIR]:
        if not directory.exists():
            continue

        for filepath in directory.glob("*.md"):
            # Track filename
            known_filenames.add(filepath.name)

            # Extract ID from frontmatter if present
            try:
                with open(filepath, 'r') as f:
                    content = f.read()
                    # Extract readwise_url from YAML frontmatter
                    match = re.search(r'^readwise_url:\s*"?([^"\n]+)"?', content, re.MULTILINE)
                    if match:
                        url = match.group(1)
                        # Extract ID from URL (last path segment)
                        doc_id = url.rstrip('/').split('/')[-1]
                        known_ids.add(doc_id)
            except Exception as e:
                pass  # Skip files with read errors

    return known_ids, known_filenames

def sanitize_filename(title: str, doc: Optional[Dict] = None) -> str:
    """
    Sanitize title for filename with fallback for invalid names.

    Args:
        title: The document title to sanitize
        doc: Optional document dict for fallback metadata (author, saved_at)

    Returns:
        Sanitized filename ending in .md
    """
    # Replace special characters
    filename = title.replace('/', '-').replace(':', ' -')
    # Remove invalid characters
    filename = re.sub(r'[<>"\\\|?*]', '', filename)
    # Trim to 100 characters
    filename = filename[:100].strip()

    # Check if filename has at least one alphanumeric character
    if not any(c.isalnum() for c in filename):
        # Fallback: use author + date or generic name
        if doc:
            author = doc.get('author', 'Unknown')
            # Sanitize author name
            author = re.sub(r'[<>"\\\|?*/:]', '', author)[:30].strip()

            saved_at = doc.get('saved_at', '')
            date_str = saved_at[:10] if saved_at else datetime.now().strftime('%Y-%m-%d')

            # Use category to make name more descriptive
            category = doc.get('category', 'Document')
            category_label = 'Tweet' if category == 'tweet' else category.capitalize()

            filename = f"{category_label} by {author} - {date_str}"
        else:
            # Generic fallback
            filename = f"Untitled - {datetime.now().strftime('%Y-%m-%d-%H%M%S')}"

    return filename + ".md"

def extract_id_from_url(url: Optional[str]) -> Optional[str]:
    """Extract document ID from Readwise URL"""
    if not url:
        return None
    return url.rstrip('/').split('/')[-1]

# ============================================================================
# NEW FUNCTIONS (for MCP server)
# ============================================================================

def fetch_api(endpoint: str, params: Optional[Dict] = None, api_version: str = "v3") -> Dict:
    """
    Make authenticated API call to Readwise with retry on rate limits.

    Implements exponential backoff for 429 rate limit errors.
    Retries up to RATE_LIMIT_MAX_RETRIES times.
    Respects Retry-After header if provided by API.

    Args:
        endpoint: API endpoint (e.g., "/list/" or "/highlights/")
        params: Query parameters
        api_version: API version to use ("v2" or "v3", defaults to "v3")
    """
    from requests.exceptions import HTTPError

    base_url = f"https://readwise.io/api/{api_version}"
    url = f"{base_url}{endpoint}"

    headers = {
        "Authorization": f"Token {READWISE_TOKEN}"
    }

    last_exception = None

    for attempt in range(RATE_LIMIT_MAX_RETRIES + 1):
        try:
            response = requests.get(
                url,
                headers=headers,
                params=params,
                timeout=REQUEST_TIMEOUT
            )
            response.raise_for_status()
            return response.json()

        except HTTPError as e:
            last_exception = e

            # Check if this is a rate limit error (429)
            if e.response is not None and e.response.status_code == 429:
                # Calculate retry delay
                retry_after = e.response.headers.get('Retry-After')

                if retry_after:
                    # Use API-provided delay (in seconds)
                    try:
                        delay = int(retry_after)
                    except ValueError:
                        # Retry-After might be HTTP date format, fall back to exponential backoff
                        delay = RATE_LIMIT_BASE_DELAY * (RATE_LIMIT_BACKOFF_MULTIPLIER ** attempt)
                else:
                    # Exponential backoff: 5s, 10s, 20s
                    delay = RATE_LIMIT_BASE_DELAY * (RATE_LIMIT_BACKOFF_MULTIPLIER ** attempt)

                # Cap at max delay
                delay = min(delay, RATE_LIMIT_MAX_DELAY)

                # Don't retry on last attempt
                if attempt < RATE_LIMIT_MAX_RETRIES:
                    logger.warning(
                        f"Rate limit hit (429) on {endpoint}, "
                        f"retrying in {delay}s (attempt {attempt + 1}/{RATE_LIMIT_MAX_RETRIES})"
                    )
                    time.sleep(delay)
                    continue
                else:
                    logger.error(f"Rate limit retries exhausted for {endpoint}")
                    raise
            else:
                # Non-429 error, don't retry
                logger.error(f"API error {e.response.status_code if e.response else 'unknown'}: {e}")
                raise

        except Exception as e:
            # Other errors (timeout, connection, etc) - don't retry
            logger.error(f"Request error for {endpoint}: {e}")
            raise

    # Should never reach here, but satisfy type checker
    if last_exception:
        raise last_exception

def format_document_markdown(doc: Dict) -> str:
    """Convert API document to markdown with YAML frontmatter"""
    # Build frontmatter
    frontmatter = {
        "title": doc.get("title", "Untitled"),
        "author": doc.get("author"),
        "source": doc.get("source"),
        "category": doc.get("category"),
        "saved_at": doc.get("saved_at"),
        "updated_at": doc.get("updated_at"),
        "readwise_url": doc.get("readwise_url"),
        "source_url": doc.get("source_url"),
        "tags": doc.get("tags", [])
    }

    # Remove None values
    frontmatter = {k: v for k, v in frontmatter.items() if v is not None}

    # Build markdown
    yaml_str = yaml.dump(frontmatter, allow_unicode=True, default_flow_style=False)
    content = doc.get("content", "")
    summary = doc.get("summary", "")
    notes = doc.get("notes", "")

    markdown = f"---\n{yaml_str}---\n\n"

    if summary:
        markdown += f"## Summary\n\n{summary}\n\n"

    if content:
        markdown += f"## Content\n\n{content}\n\n"

    if notes:
        markdown += f"## Notes\n\n{notes}\n\n"

    return markdown

def save_document(doc: Dict, directory: Path) -> Path:
    """Save document as markdown file"""
    directory.mkdir(parents=True, exist_ok=True)

    filename = sanitize_filename(doc.get("title", ""), doc)
    filepath = directory / filename

    # Handle filename collisions
    counter = 1
    while filepath.exists():
        name_without_ext = filename[:-3]  # Remove .md
        filepath = directory / f"{name_without_ext} ({counter}).md"
        counter += 1

    markdown = format_document_markdown(doc)
    with open(filepath, 'w') as f:
        f.write(markdown)

    return filepath

# ============================================================================
# HIGHLIGHTS UTILITY FUNCTIONS
# ============================================================================

def scan_existing_highlights() -> Tuple[set, set]:
    """Scan Highlights directory to build known IDs and filenames"""
    known_ids = set()
    known_filenames = set()

    if not HIGHLIGHTS_DIR.exists():
        return known_ids, known_filenames

    for filepath in HIGHLIGHTS_DIR.glob("*.md"):
        # Track filename
        known_filenames.add(filepath.name)

        # Extract highlight_id from frontmatter if present
        try:
            with open(filepath, 'r') as f:
                content = f.read()
                # Extract highlight_id from YAML frontmatter
                match = re.search(r'^highlight_id:\s*"?([^"\n]+)"?', content, re.MULTILINE)
                if match:
                    highlight_id = match.group(1)
                    known_ids.add(highlight_id)
        except Exception:
            pass  # Skip files with read errors

    return known_ids, known_filenames

def sanitize_source_title(title: str, max_length: int = 100) -> str:
    """Sanitize source title for filename (matches document title length)"""
    # Replace special characters
    sanitized = title.replace('/', '-').replace(':', ' -')
    # Remove invalid characters
    sanitized = re.sub(r'[<>"\\\|?*]', '', sanitized)
    # Trim to max_length
    sanitized = sanitized[:max_length].strip()
    # If empty or no alphanumeric characters, use generic name
    if not sanitized or not any(c.isalnum() for c in sanitized):
        sanitized = "Untitled Source"
    return sanitized

def format_highlight_markdown(highlight: Dict) -> str:
    """Convert API highlight to markdown with YAML frontmatter"""
    # Build frontmatter
    frontmatter = {
        "highlight_id": str(highlight.get("id", "")),
        "text": highlight.get("text", "")[:100],  # First 100 chars
        "source_title": highlight.get("source_title") or highlight.get("book_title"),
        "source_author": highlight.get("author"),
        "source_type": highlight.get("category") or highlight.get("source_type"),
        "source_url": highlight.get("source_url"),
        "highlighted_at": highlight.get("highlighted_at") or highlight.get("created_at"),
        "updated_at": highlight.get("updated"),
        "location": highlight.get("location"),
        "readwise_url": highlight.get("readwise_url"),
        "tags": highlight.get("tags", [])
    }

    # Remove None values
    frontmatter = {k: v for k, v in frontmatter.items() if v is not None}

    # Build markdown
    yaml_str = yaml.dump(frontmatter, allow_unicode=True, default_flow_style=False)

    full_text = highlight.get("text", "")
    note = highlight.get("note", "")
    source_title = frontmatter.get("source_title", "Unknown Source")
    author = frontmatter.get("source_author", "")
    location = frontmatter.get("location", "")
    highlighted_at = frontmatter.get("highlighted_at", "")
    source_url = frontmatter.get("source_url", "")
    readwise_url = frontmatter.get("readwise_url", "")

    # Format highlighted date
    date_str = ""
    if highlighted_at:
        try:
            dt = datetime.fromisoformat(highlighted_at.replace('Z', '+00:00'))
            date_str = dt.strftime("%Y-%m-%d")
        except:
            date_str = highlighted_at[:10] if len(highlighted_at) >= 10 else highlighted_at

    markdown = f"---\n{yaml_str}---\n\n"
    markdown += f"# {source_title}\n"

    if author:
        markdown += f"*{author}*\n\n"

    markdown += "## Highlight\n\n"
    markdown += f'> "{full_text}"\n\n'

    # Location and date info
    info_parts = []
    if location:
        info_parts.append(f"**Location**: {location}")
    if date_str:
        info_parts.append(f"**Highlighted**: {date_str}")

    if info_parts:
        markdown += " | ".join(info_parts) + "\n\n"

    if note:
        markdown += f"**Note**: {note}\n\n"

    markdown += "---\n\n"

    if source_url:
        markdown += f"**Source**: {source_url}\n"
    if readwise_url:
        markdown += f"**Readwise**: {readwise_url}\n"

    markdown += f"\n*Imported from Readwise Highlights on {datetime.now(timezone.utc).strftime('%Y-%m-%d')}*\n"

    return markdown

def save_highlight(highlight: Dict, directory: Path) -> Path:
    """Save highlight with temporal filename: YYYYMMDD-HHMMSS [Source] highlight.md"""
    directory.mkdir(parents=True, exist_ok=True)

    # Get updated_at timestamp
    updated_at = highlight.get("updated") or highlight.get("updated_at") or highlight.get("created_at")

    # Parse timestamp and format for filename
    try:
        dt = datetime.fromisoformat(updated_at.replace('Z', '+00:00'))
        timestamp_prefix = dt.strftime("%Y%m%d-%H%M%S")
    except:
        # Fallback to current time
        timestamp_prefix = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")

    # Get source title
    source_title = highlight.get("source_title") or highlight.get("book_title") or "Unknown Source"
    sanitized_source = sanitize_source_title(source_title, max_length=100)

    # Build filename
    filename = f"{timestamp_prefix} [{sanitized_source}] highlight.md"
    filepath = directory / filename

    # Handle filename collisions
    counter = 1
    while filepath.exists():
        filename = f"{timestamp_prefix} [{sanitized_source}] highlight ({counter}).md"
        filepath = directory / filename
        counter += 1

    markdown = format_highlight_markdown(highlight)
    with open(filepath, 'w') as f:
        f.write(markdown)

    return filepath

# ============================================================================
# MCP SERVER INITIALIZATION
# ============================================================================

mcp = FastMCP("readwise")

# ============================================================================
# MCP TOOLS (8 essential tools)
# ============================================================================

@mcp.tool()
async def readwise_daily_review() -> dict:
    """Fetch today's highlights and save to Daily Reviews directory"""
    try:
        # Get today's date
        today = datetime.now(timezone.utc).date()
        today_str = today.isoformat()

        # Fetch highlights API (using highlights endpoint for daily review)
        data = fetch_api("/highlights/", params={"limit": 50})
        highlights = data.get("results", [])

        if not highlights:
            return {"status": "no_highlights", "count": 0}

        # Create daily review file
        filename = f"{today_str}.md"
        filepath = DAILY_REVIEWS_DIR / filename
        DAILY_REVIEWS_DIR.mkdir(parents=True, exist_ok=True)

        # Format content
        content = f"# Daily Review - {today_str}\n\n"
        for highlight in highlights:
            content += f"## {highlight.get('text', '')}\n\n"
            if highlight.get('note'):
                content += f"**Note**: {highlight['note']}\n\n"
            content += f"**Source**: {highlight.get('source_url', 'Unknown')}\n\n---\n\n"

        with open(filepath, 'w') as f:
            f.write(content)

        return {
            "status": "success",
            "count": len(highlights),
            "file": str(filepath)
        }

    except Exception as e:
        logger.error(f"Error in daily review: {e}")
        return {"status": "error", "message": str(e)}

@mcp.tool()
async def readwise_import_recent(category: str = "tweet", limit: int = 20) -> dict:
    """Import recent documents since last import with deduplication"""
    try:
        # Load state
        state = load_state()
        last_import = state.get("last_import_timestamp")

        # Scan existing documents
        known_ids, known_filenames = scan_existing_documents()

        # Build API params
        params = {"category": category, "limit": limit}
        if last_import:
            params["updatedAfter"] = last_import

        # Fetch documents
        data = fetch_api("/list/", params=params)
        results = data.get("results", [])

        imported = 0
        skipped = 0

        for doc in results:
            # Check deduplication
            doc_id = extract_id_from_url(doc.get("readwise_url"))
            filename = sanitize_filename(doc.get("title", ""), doc)

            if doc_id in known_ids or filename in known_filenames:
                skipped += 1
                continue

            # Save document
            save_document(doc, DOCUMENTS_DIR)
            imported += 1

            # Track for session deduplication
            if doc_id:
                known_ids.add(doc_id)
            known_filenames.add(filename)

        # Update state
        if results:
            state["last_import_timestamp"] = datetime.now(timezone.utc).isoformat()
            write_state(state)

        return {
            "status": "success",
            "imported": imported,
            "skipped": skipped,
            "total_analyzed": len(results)
        }

    except Exception as e:
        logger.error(f"Error importing recent: {e}")
        return {"status": "error", "message": str(e)}

@mcp.tool()
async def readwise_backfill(target_date: str, category: str = "tweet") -> dict:
    """Paginate to target date with synced range optimization"""
    try:
        # Load state
        state = load_state()
        synced_ranges = state.get("synced_ranges", [])

        # Check optimization
        should_proceed, optimized_after = optimize_backfill(target_date, synced_ranges)

        if not should_proceed:
            return {
                "status": "already_synced",
                "message": f"Target date {target_date} already synced",
                "imported": 0,
                "skipped": 0
            }

        # Scan existing documents
        known_ids, known_filenames = scan_existing_documents()

        # Build initial params
        params = {"category": category, "limit": 50}
        if optimized_after:
            params["updatedAfter"] = optimized_after

        # Pagination loop
        cursor = None
        imported = 0
        skipped = 0
        page_num = 0
        target_dt = datetime.strptime(target_date, "%Y-%m-%d")
        reached_target = False

        while not reached_target and page_num < 100:  # Safety limit
            page_num += 1

            # Update params with cursor
            if cursor:
                params["pageCursor"] = cursor

            # Fetch page
            data = fetch_api("/list/", params=params)
            results = data.get("results", [])

            # Throttle between requests (except first page)
            if page_num > 1:
                time.sleep(PAGINATION_THROTTLE_DELAY)

            if not results:
                break

            # Process documents
            for doc in results:
                doc_date = datetime.fromisoformat(doc["saved_at"].replace('Z', '+00:00'))

                # Check if reached target
                if doc_date.date() < target_dt.date():
                    reached_target = True
                    break

                # Deduplicate
                doc_id = extract_id_from_url(doc.get("readwise_url"))
                filename = sanitize_filename(doc.get("title", ""), doc)

                if doc_id in known_ids or filename in known_filenames:
                    skipped += 1
                    continue

                # Save document
                save_document(doc, DOCUMENTS_DIR)
                imported += 1

                # Track for session deduplication
                if doc_id:
                    known_ids.add(doc_id)
                known_filenames.add(filename)

            if reached_target:
                break

            # Get next cursor
            cursor = data.get("nextPageCursor")
            if not cursor:
                break

        # Update state
        state["last_import_timestamp"] = datetime.now(timezone.utc).isoformat()
        write_state(state)

        return {
            "status": "success" if reached_target else "completed_all_pages",
            "imported": imported,
            "skipped": skipped,
            "pages": page_num,
            "reached_target": reached_target
        }

    except Exception as e:
        logger.error(f"Error in backfill: {e}")
        return {"status": "error", "message": str(e)}

@mcp.tool()
async def readwise_book_highlights(title: Optional[str] = None, book_id: Optional[str] = None) -> dict:
    """Get highlights for a specific book"""
    try:
        # Build params
        params = {}
        if book_id:
            params["book_id"] = book_id

        # Fetch highlights
        data = fetch_api("/highlights/", params=params)
        highlights = data.get("results", [])

        # Filter by title if provided
        if title:
            highlights = [h for h in highlights if title.lower() in h.get("book_title", "").lower()]

        return {
            "status": "success",
            "count": len(highlights),
            "highlights": [
                {
                    "text": h.get("text"),
                    "note": h.get("note"),
                    "book_title": h.get("book_title"),
                    "location": h.get("location")
                }
                for h in highlights[:50]  # Limit to 50 for token efficiency
            ]
        }

    except Exception as e:
        logger.error(f"Error fetching book highlights: {e}")
        return {"status": "error", "message": str(e)}

@mcp.tool()
async def readwise_search_highlights(query: str, limit: int = 50) -> dict:
    """Search highlights by text query"""
    try:
        # Fetch highlights (API doesn't support search directly, so we fetch and filter)
        data = fetch_api("/highlights/", params={"limit": 100})
        highlights = data.get("results", [])

        # Filter by query
        query_lower = query.lower()
        matching = [
            h for h in highlights
            if query_lower in h.get("text", "").lower() or
               query_lower in h.get("note", "").lower()
        ]

        return {
            "status": "success",
            "count": len(matching),
            "highlights": [
                {
                    "text": h.get("text"),
                    "note": h.get("note"),
                    "source": h.get("source_url"),
                    "created_at": h.get("created_at")
                }
                for h in matching[:limit]
            ]
        }

    except Exception as e:
        logger.error(f"Error searching highlights: {e}")
        return {"status": "error", "message": str(e)}

@mcp.tool()
async def readwise_state_info() -> dict:
    """Show current import state and synced ranges"""
    try:
        state = load_state()

        # Scan filesystem for current count
        known_ids, known_filenames = scan_existing_documents()

        return {
            "status": "success",
            "last_import": state.get("last_import_timestamp"),
            "oldest_imported": state.get("oldest_imported_date"),
            "synced_ranges": state.get("synced_ranges", []),
            "backfill_in_progress": state.get("backfill_in_progress", False),
            "documents_on_disk": len(known_filenames),
            "documents_with_ids": len(known_ids)
        }

    except Exception as e:
        logger.error(f"Error getting state info: {e}")
        return {"status": "error", "message": str(e)}

@mcp.tool()
async def readwise_init_ranges() -> dict:
    """Scan filesystem to build synced_ranges from existing documents"""
    try:
        # Scan all documents
        docs_with_dates = []

        for directory in [DOCUMENTS_DIR, ARCHIVES_DIR]:
            if not directory.exists():
                continue

            for filepath in directory.glob("*.md"):
                try:
                    with open(filepath, 'r') as f:
                        content = f.read()
                        # Extract saved_at from frontmatter
                        match = re.search(r'^saved_at:\s*"?([^"\n]+)"?', content, re.MULTILINE)
                        if match:
                            saved_at = match.group(1)
                            docs_with_dates.append(saved_at)
                except Exception:
                    pass

        if not docs_with_dates:
            return {"status": "no_documents", "message": "No documents with dates found"}

        # Sort dates
        dates = sorted([datetime.fromisoformat(d.replace('Z', '+00:00')) for d in docs_with_dates])

        # Build single range
        synced_range = {
            "start": dates[0].isoformat(),
            "end": dates[-1].isoformat(),
            "doc_count": len(docs_with_dates),
            "verified_at": datetime.now(timezone.utc).isoformat()
        }

        # Update state
        state = load_state()
        state["synced_ranges"] = [synced_range]
        state["oldest_imported_date"] = dates[0].strftime("%Y-%m-%d")
        write_state(state)

        return {
            "status": "success",
            "range": synced_range,
            "documents_analyzed": len(docs_with_dates)
        }

    except Exception as e:
        logger.error(f"Error initializing ranges: {e}")
        return {"status": "error", "message": str(e)}

@mcp.tool()
async def readwise_reset_state(clear_ranges: bool = False) -> dict:
    """Clear state file (optionally preserve synced_ranges)"""
    try:
        if clear_ranges:
            # Full reset
            new_state = {
                "last_import_timestamp": datetime.now(timezone.utc).isoformat(),
                "synced_ranges": [],
                "backfill_in_progress": False
            }
        else:
            # Preserve ranges
            state = load_state()
            new_state = {
                "last_import_timestamp": datetime.now(timezone.utc).isoformat(),
                "synced_ranges": state.get("synced_ranges", []),
                "backfill_in_progress": False
            }

        write_state(new_state)

        return {
            "status": "success",
            "message": "State reset",
            "cleared_ranges": clear_ranges
        }

    except Exception as e:
        logger.error(f"Error resetting state: {e}")
        return {"status": "error", "message": str(e)}

@mcp.tool()
async def readwise_import_recent_highlights(limit: int = 100) -> dict:
    """Import recent highlights across all sources since last import"""
    try:
        # Load state
        state = load_state()
        highlights_state = state.get("highlights", {})
        last_import = highlights_state.get("last_import_timestamp")

        # Scan existing highlights
        known_ids, known_filenames = scan_existing_highlights()

        # Build API params
        params = {"page_size": min(limit, 1000)}
        if last_import:
            params["updatedAfter"] = last_import

        # Fetch highlights using export API (includes book metadata)
        data = fetch_api("/export/", params=params, api_version="v2")
        books = data.get("results", [])

        imported = 0
        skipped = 0
        total_analyzed = 0

        # Process each book and its highlights
        for book in books:
            # Extract book metadata
            book_title = book.get("title", "Unknown Source")
            book_author = book.get("author")
            book_category = book.get("category")
            book_source_url = book.get("source_url")

            # Process highlights for this book
            for highlight in book.get("highlights", []):
                total_analyzed += 1

                # Enrich highlight with book metadata
                highlight["source_title"] = book_title
                highlight["book_title"] = book_title
                highlight["author"] = book_author
                highlight["category"] = book_category
                highlight["source_type"] = book_category
                highlight["source_url"] = book_source_url

                # Check deduplication
                highlight_id = str(highlight.get("id", ""))

                # Generate filename for filename-based dedup check
                updated_at = highlight.get("updated") or highlight.get("updated_at") or highlight.get("created_at")
                try:
                    dt = datetime.fromisoformat(updated_at.replace('Z', '+00:00'))
                    timestamp_prefix = dt.strftime("%Y%m%d-%H%M%S")
                except:
                    timestamp_prefix = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")

                sanitized_source = sanitize_source_title(book_title, max_length=100)
                filename = f"{timestamp_prefix} [{sanitized_source}] highlight.md"

                if highlight_id in known_ids or filename in known_filenames:
                    skipped += 1
                    continue

                # Save highlight
                save_highlight(highlight, HIGHLIGHTS_DIR)
                imported += 1

                # Track for session deduplication
                if highlight_id:
                    known_ids.add(highlight_id)
                known_filenames.add(filename)

        # Update state
        if total_analyzed > 0:
            highlights_state["last_import_timestamp"] = datetime.now(timezone.utc).isoformat()
            state["highlights"] = highlights_state
            write_state(state)

        return {
            "status": "success",
            "imported": imported,
            "skipped": skipped,
            "total_analyzed": total_analyzed
        }

    except Exception as e:
        logger.error(f"Error importing recent highlights: {e}")
        return {"status": "error", "message": str(e)}

@mcp.tool()
async def readwise_backfill_highlights(target_date: str) -> dict:
    """Paginate highlights back to target date with synced range optimization"""
    try:
        # Load state
        state = load_state()
        highlights_state = state.get("highlights", {})
        synced_ranges = highlights_state.get("synced_ranges", [])

        # Check optimization
        should_proceed, optimized_after = optimize_backfill(target_date, synced_ranges)

        if not should_proceed:
            return {
                "status": "already_synced",
                "message": f"Target date {target_date} already synced",
                "imported": 0,
                "skipped": 0
            }

        # Scan existing highlights
        known_ids, known_filenames = scan_existing_highlights()

        # Build base params (v2 export API uses page number)
        base_params = {"page_size": 50}
        if optimized_after:
            base_params["updatedAfter"] = optimized_after

        # Pagination loop
        page_num = 1
        imported = 0
        skipped = 0
        target_dt = datetime.strptime(target_date, "%Y-%m-%d")
        reached_target = False

        while not reached_target and page_num < 1000:  # Safety limit
            # Build params for this page
            params = {**base_params, "page": page_num}

            # Fetch page using export API (includes book metadata)
            data = fetch_api("/export/", params=params, api_version="v2")
            books = data.get("results", [])

            # Throttle between requests (except first page)
            if page_num > 1:
                time.sleep(PAGINATION_THROTTLE_DELAY)

            if not books:
                break

            # Process each book and its highlights
            for book in books:
                if reached_target:
                    break

                # Extract book metadata
                book_title = book.get("title", "Unknown Source")
                book_author = book.get("author")
                book_category = book.get("category")
                book_source_url = book.get("source_url")

                # Process highlights for this book
                for highlight in book.get("highlights", []):
                    # Enrich highlight with book metadata
                    highlight["source_title"] = book_title
                    highlight["book_title"] = book_title
                    highlight["author"] = book_author
                    highlight["category"] = book_category
                    highlight["source_type"] = book_category
                    highlight["source_url"] = book_source_url

                    # Get highlight date
                    updated_at = highlight.get("updated") or highlight.get("updated_at") or highlight.get("created_at")
                    try:
                        highlight_date = datetime.fromisoformat(updated_at.replace('Z', '+00:00'))
                    except:
                        # Skip highlights with invalid dates
                        continue

                    # Check if reached target
                    if highlight_date.date() < target_dt.date():
                        reached_target = True
                        break

                    # Deduplicate
                    highlight_id = str(highlight.get("id", ""))

                    # Generate filename for dedup check
                    timestamp_prefix = highlight_date.strftime("%Y%m%d-%H%M%S")
                    sanitized_source = sanitize_source_title(book_title, max_length=100)
                    filename = f"{timestamp_prefix} [{sanitized_source}] highlight.md"

                    if highlight_id in known_ids or filename in known_filenames:
                        skipped += 1
                        continue

                    # Save highlight
                    save_highlight(highlight, HIGHLIGHTS_DIR)
                    imported += 1

                    # Track for session deduplication
                    if highlight_id:
                        known_ids.add(highlight_id)
                    known_filenames.add(filename)

            if reached_target:
                break

            # v2 API pagination: check if there are more pages
            # The API returns "count", "next", "previous" fields
            if not data.get("next"):
                break

            page_num += 1

        # Update state with synced range
        if reached_target:
            # Create synced range entry
            synced_range = {
                "start": f"{target_date}T00:00:00+00:00",
                "end": datetime.now(timezone.utc).isoformat(),
                "doc_count": imported,
                "verified_at": datetime.now(timezone.utc).isoformat()
            }
            synced_ranges.append(synced_range)

        highlights_state["last_import_timestamp"] = datetime.now(timezone.utc).isoformat()
        highlights_state["synced_ranges"] = synced_ranges
        state["highlights"] = highlights_state
        write_state(state)

        return {
            "status": "success" if reached_target else "completed_all_pages",
            "imported": imported,
            "skipped": skipped,
            "pages": page_num,
            "reached_target": reached_target
        }

    except Exception as e:
        logger.error(f"Error in highlights backfill: {e}")
        return {"status": "error", "message": str(e)}

# ============================================================================
# SERVER STARTUP
# ============================================================================

if __name__ == "__main__":
    validate_config()
    logger.info("Starting Readwise MCP Server")
    logger.info(f"Vault path: {VAULT_PATH}")
    logger.info(f"Documents directory: {DOCUMENTS_DIR}")
    mcp.run()
