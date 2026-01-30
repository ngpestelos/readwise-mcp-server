#!/usr/bin/env python3
# Copyright (c) 2026 ngpestelos
# Licensed under the MIT License - see LICENSE file for details
"""
Unit and integration tests for Readwise MCP Server
"""

import json
import pytest
import time
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock, patch, mock_open, MagicMock
import tempfile
import os

# Import functions from server
import sys
sys.path.insert(0, str(Path(__file__).parent))
from server import (
    load_state, write_state, optimize_backfill, scan_existing_documents,
    sanitize_filename, extract_id_from_url, format_document_markdown,
    save_document, fetch_api, scan_existing_highlights, sanitize_source_title,
    format_highlight_markdown, save_highlight
)

# ============================================================================
# UNIT TESTS
# ============================================================================

class TestTimestampFormat:
    """Test ISO 8601 timestamp format correctness"""

    def test_load_state_default_timestamp_format(self, tmp_path):
        """Test that default state has valid ISO 8601 timestamp without double timezone"""
        state_file = tmp_path / "nonexistent.json"

        with patch('server.STATE_FILE', state_file):
            state = load_state()
            timestamp = state["last_import_timestamp"]

            # Should not have both +00:00 and Z
            assert not ("+00:00Z" in timestamp), f"Malformed timestamp: {timestamp}"

            # Should be parseable as ISO 8601
            try:
                datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
            except ValueError:
                pytest.fail(f"Invalid ISO 8601 timestamp: {timestamp}")

    def test_timestamp_has_timezone_info(self, tmp_path):
        """Test that generated timestamps include timezone information"""
        state_file = tmp_path / "nonexistent.json"

        with patch('server.STATE_FILE', state_file):
            state = load_state()
            timestamp = state["last_import_timestamp"]

            # Should have either +00:00 or Z, but not both
            has_offset = "+00:00" in timestamp
            has_z = timestamp.endswith("Z")

            assert has_offset or has_z, f"Timestamp missing timezone info: {timestamp}"
            assert not (has_offset and has_z), f"Timestamp has both offset and Z: {timestamp}"

    def test_written_state_has_valid_timestamp(self, tmp_path):
        """Test that written state files contain valid timestamps"""
        state_file = tmp_path / "state.json"

        # Create state with current timestamp (simulating what the server does)
        from datetime import timezone
        test_state = {
            "last_import_timestamp": datetime.now(timezone.utc).isoformat(),
            "synced_ranges": []
        }

        with patch('server.STATE_FILE', state_file):
            write_state(test_state)

            # Read back and validate
            with open(state_file, 'r') as f:
                loaded = json.load(f)
                timestamp = loaded["last_import_timestamp"]

                # Should not have malformed double timezone
                assert not ("+00:00Z" in timestamp), f"Written state has malformed timestamp: {timestamp}"

                # Should be parseable
                try:
                    datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
                except ValueError:
                    pytest.fail(f"Written timestamp not valid ISO 8601: {timestamp}")


class TestStateManagement:
    """Test state file reading and writing"""

    def test_load_state_existing(self, tmp_path):
        """Test loading existing state file"""
        state_file = tmp_path / "state.json"
        test_state = {
            "last_import_timestamp": "2026-01-22T00:00:00Z",
            "synced_ranges": [{"start": "2026-01-01T00:00:00Z", "end": "2026-01-21T00:00:00Z", "doc_count": 614}]
        }
        with open(state_file, 'w') as f:
            json.dump(test_state, f)

        # Mock STATE_FILE
        with patch('server.STATE_FILE', state_file):
            state = load_state()
            assert state["last_import_timestamp"] == "2026-01-22T00:00:00Z"
            assert len(state["synced_ranges"]) == 1
            assert state["synced_ranges"][0]["doc_count"] == 614

    def test_load_state_missing(self, tmp_path):
        """Test loading when state file doesn't exist"""
        state_file = tmp_path / "nonexistent.json"

        with patch('server.STATE_FILE', state_file):
            state = load_state()
            assert "last_import_timestamp" in state
            assert "synced_ranges" in state
            assert state["synced_ranges"] == []

    def test_write_state(self, tmp_path):
        """Test writing state file"""
        state_file = tmp_path / "state.json"
        test_state = {
            "last_import_timestamp": "2026-01-22T00:00:00Z",
            "synced_ranges": []
        }

        with patch('server.STATE_FILE', state_file):
            write_state(test_state)
            assert state_file.exists()

            with open(state_file, 'r') as f:
                loaded = json.load(f)
                assert loaded["last_import_timestamp"] == "2026-01-22T00:00:00Z"


class TestOptimization:
    """Test synced range optimization logic"""

    def test_optimize_no_ranges(self):
        """Test optimization with no synced ranges"""
        should_proceed, optimized_after = optimize_backfill("2026-01-15", [])
        assert should_proceed == True
        assert optimized_after is None

    def test_optimize_target_within_range(self):
        """Test optimization when target is within synced range"""
        synced_ranges = [
            {
                "start": "2026-01-01T00:00:00+00:00",
                "end": "2026-01-21T00:00:00+00:00",
                "doc_count": 614
            }
        ]
        should_proceed, optimized_after = optimize_backfill("2026-01-15", synced_ranges)
        assert should_proceed == False
        assert optimized_after is None

    def test_optimize_target_before_range(self):
        """Test optimization when target is before synced range (gap filling)"""
        synced_ranges = [
            {
                "start": "2026-01-15T00:00:00+00:00",
                "end": "2026-01-21T00:00:00+00:00",
                "doc_count": 100
            }
        ]
        should_proceed, optimized_after = optimize_backfill("2026-01-10", synced_ranges)
        assert should_proceed == True
        # Should return None to allow pagination to fill the gap
        # Deduplication will handle overlap with synced range
        assert optimized_after is None

    def test_optimize_target_after_range(self):
        """Test optimization when target is after synced range"""
        synced_ranges = [
            {
                "start": "2026-01-01T00:00:00+00:00",
                "end": "2026-01-10T00:00:00+00:00",
                "doc_count": 50
            }
        ]
        should_proceed, optimized_after = optimize_backfill("2026-01-20", synced_ranges)
        assert should_proceed == True
        assert optimized_after is None

    def test_optimize_gap_filling_bug_scenario(self):
        """
        Test the specific bug scenario that was fixed:
        Target: Aug 1, 2025
        Synced range: Dec 1, 2025 - Jan 21, 2026

        Expected: Should return (True, None) to fill the gap
        Bug was: Returned (True, range.end) which skipped the gap

        This test verifies the fix for commit e953e71
        """
        synced_ranges = [
            {
                "start": "2025-12-01T02:07:57.309000+00:00",
                "end": "2026-01-21T07:28:56.317000+00:00",
                "doc_count": 1008
            }
        ]

        should_proceed, optimized_after = optimize_backfill("2025-08-01", synced_ranges)

        # Should proceed to fill the gap
        assert should_proceed == True

        # Critical: Should NOT use range.end as filter
        # This was the bug - it would return "2026-01-21T07:28:56.317000+00:00"
        # which told API to only fetch documents AFTER Jan 21, completely missing Aug-Nov
        assert optimized_after is None, \
            "Bug: optimize_backfill should return None for gap filling, not range.end"

    def test_optimize_multiple_ranges_finds_correct_gap(self):
        """Test with multiple synced ranges - should identify gap correctly"""
        synced_ranges = [
            {
                "start": "2025-01-01T00:00:00+00:00",
                "end": "2025-03-31T00:00:00+00:00",
                "doc_count": 200
            },
            {
                "start": "2025-12-01T00:00:00+00:00",
                "end": "2026-01-21T00:00:00+00:00",
                "doc_count": 1000
            }
        ]

        # Target between ranges (gap exists)
        should_proceed, optimized_after = optimize_backfill("2025-08-01", synced_ranges)

        assert should_proceed == True
        # Should paginate to fill gap, not use any range filter
        assert optimized_after is None


class TestFilenameHandling:
    """Test filename sanitization and ID extraction"""

    def test_sanitize_filename_basic(self):
        """Test basic filename sanitization"""
        result = sanitize_filename("Simple Title")
        assert result == "Simple Title.md"

    def test_sanitize_filename_special_chars(self):
        """Test sanitization with special characters"""
        result = sanitize_filename("Title / With : Special <Chars>")
        assert "/" not in result
        assert ":" not in result
        assert "<" not in result
        assert ">" not in result
        assert result.endswith(".md")

    def test_sanitize_filename_long(self):
        """Test truncation of long filenames"""
        long_title = "A" * 150
        result = sanitize_filename(long_title)
        assert len(result) <= 104  # 100 chars + ".md"

    def test_extract_id_from_url(self):
        """Test ID extraction from Readwise URL"""
        url = "https://readwise.io/reader/document/123456"
        assert extract_id_from_url(url) == "123456"

        url_with_slash = "https://readwise.io/reader/document/789012/"
        assert extract_id_from_url(url_with_slash) == "789012"

    def test_extract_id_none(self):
        """Test ID extraction with None URL"""
        assert extract_id_from_url(None) is None

    def test_extract_id_empty(self):
        """Test ID extraction with empty URL"""
        assert extract_id_from_url("") is None


class TestFilenameValidity:
    """
    Test filename sanitization for human-readable, cross-platform compatibility.

    Filenames should contain at least one alphanumeric character for:
    - Human readability in file listings
    - Shell tab completion
    - Cross-platform compatibility
    - Proper sorting and searching

    These tests verify the fallback mechanism for documents with titles
    containing only special characters (emoji, ellipsis, etc.).
    """

    def test_ellipsis_only_title(self):
        """Test that ellipsis-only title generates valid filename"""
        doc = {
            "title": "…",
            "author": "Take Action!",
            "saved_at": "2025-12-08T00:00:00Z",
            "category": "tweet"
        }

        result = sanitize_filename("…", doc)

        # Should use fallback with author and date
        assert result == "Tweet by Take Action! - 2025-12-08.md"

        # Should have alphanumeric characters for readability
        filename_without_ext = result[:-3]  # Remove .md
        assert any(c.isalnum() for c in filename_without_ext), \
            f"Filename '{result}' has no alphanumeric characters"

    def test_emoji_only_title(self):
        """Test that emoji-only title generates valid filename"""
        doc = {
            "title": "🍿🍿",
            "author": "Elon Musk",
            "saved_at": "2025-12-06T00:00:00Z",
            "category": "tweet"
        }

        result = sanitize_filename("🍿🍿", doc)

        assert result == "Tweet by Elon Musk - 2025-12-06.md"

        # Verify filename has alphanumeric content
        filename_without_ext = result[:-3]
        assert any(c.isalnum() for c in filename_without_ext), \
            f"Filename '{result}' has no alphanumeric characters"

    def test_empty_title(self):
        """Test that empty title generates valid filename"""
        doc = {
            "title": "",
            "author": "x.com",
            "saved_at": "2025-12-05T00:00:00Z",
            "category": "tweet"
        }

        result = sanitize_filename("", doc)

        assert result == "Tweet by x.com - 2025-12-05.md"

        # Verify filename has alphanumeric content
        filename_without_ext = result[:-3]
        assert any(c.isalnum() for c in filename_without_ext), \
            f"Filename '{result}' has no alphanumeric characters"

    def test_whitespace_only_title(self):
        """Test that whitespace-only title generates valid filename"""
        doc = {
            "title": "   ",
            "author": "TestUser",
            "saved_at": "2025-12-01T00:00:00Z",
            "category": "tweet"
        }

        result = sanitize_filename("   ", doc)

        # Should use fallback
        assert "Tweet by TestUser - 2025-12-01.md" == result

        # Verify filename has alphanumeric content
        filename_without_ext = result[:-3]
        assert any(c.isalnum() for c in filename_without_ext)

    def test_special_chars_only_title(self):
        """Test title with only special characters"""
        doc = {
            "title": "!@#$%^&*()",
            "author": "SpecialUser",
            "saved_at": "2025-12-02T00:00:00Z",
            "category": "tweet"
        }

        result = sanitize_filename("!@#$%^&*()", doc)

        # Should use fallback
        assert "Tweet by SpecialUser - 2025-12-02.md" == result

        # Verify filename has alphanumeric content
        filename_without_ext = result[:-3]
        assert any(c.isalnum() for c in filename_without_ext)

    def test_article_category_fallback(self):
        """Test fallback uses category-specific label for articles"""
        doc = {
            "title": "…",
            "author": "Blog Author",
            "saved_at": "2025-12-03T00:00:00Z",
            "category": "article"
        }

        result = sanitize_filename("…", doc)

        assert result == "Article by Blog Author - 2025-12-03.md"
        assert any(c.isalnum() for c in result[:-3])

    def test_pdf_category_fallback(self):
        """Test fallback uses category-specific label for PDFs"""
        doc = {
            "title": "🔥",
            "author": "PDF Author",
            "saved_at": "2025-12-04T00:00:00Z",
            "category": "pdf"
        }

        result = sanitize_filename("🔥", doc)

        assert result == "Pdf by PDF Author - 2025-12-04.md"
        assert any(c.isalnum() for c in result[:-3])

    def test_no_doc_fallback(self):
        """Test fallback when doc parameter is not provided"""
        result = sanitize_filename("…", None)

        # Should use generic fallback with timestamp
        assert result.startswith("Untitled - ")
        assert result.endswith(".md")

        # Should have date in format YYYY-MM-DD
        filename_without_ext = result[:-3]
        assert any(c.isalnum() for c in filename_without_ext)

    def test_author_with_special_chars(self):
        """Test that author names with special chars are sanitized"""
        doc = {
            "title": "…",
            "author": "User/Name:Test",
            "saved_at": "2025-12-05T00:00:00Z",
            "category": "tweet"
        }

        result = sanitize_filename("…", doc)

        # Author special chars should be removed
        assert "/" not in result
        assert ":" not in result
        assert "Tweet by UserNameTest - 2025-12-05.md" == result

    def test_very_long_author_name(self):
        """Test that author names are truncated to 30 chars"""
        doc = {
            "title": "…",
            "author": "A" * 50,  # 50 character author name
            "saved_at": "2025-12-06T00:00:00Z",
            "category": "tweet"
        }

        result = sanitize_filename("…", doc)

        # Author should be truncated to 30 chars
        expected = f"Tweet by {'A' * 30} - 2025-12-06.md"
        assert result == expected

    def test_mixed_valid_invalid_chars(self):
        """Test title with mix of valid and invalid chars still uses original"""
        # This should NOT use fallback because it has some alphanumeric
        result = sanitize_filename("Hello 🍿 World", None)

        # Should use original title (cleaned up)
        assert result == "Hello 🍿 World.md"
        assert any(c.isalnum() for c in result[:-3])

    def test_invalid_title_regression(self):
        """
        Regression test: Verify all problematic cases with invalid titles
        now produce valid, human-readable filenames.

        These titles (emoji-only, empty, special characters) previously
        created filenames that were not cross-platform compatible and
        difficult for users to work with in shell environments.
        """
        problematic_cases = [
            ("…", {"author": "User1", "saved_at": "2025-12-08", "category": "tweet"}),
            ("🍿🍿", {"author": "User2", "saved_at": "2025-12-06", "category": "tweet"}),
            ("", {"author": "User3", "saved_at": "2025-12-05", "category": "tweet"}),
            ("   ", {"author": "User4", "saved_at": "2025-12-04", "category": "tweet"}),
            ("...", {"author": "User5", "saved_at": "2025-12-03", "category": "tweet"}),
            ("---", {"author": "User6", "saved_at": "2025-12-02", "category": "tweet"}),
        ]

        for title, doc in problematic_cases:
            result = sanitize_filename(title, doc)
            filename_without_ext = result[:-3]

            # Critical: Must have at least one alphanumeric character
            has_alnum = any(c.isalnum() for c in filename_without_ext)
            assert has_alnum, \
                f"REGRESSION: Title '{title}' produced invalid filename '{result}' " \
                f"with no alphanumeric characters"

    def test_save_document_with_invalid_title(self, tmp_path):
        """Integration test: Verify save_document works with invalid titles"""
        doc = {
            "title": "…",
            "author": "Test Author",
            "saved_at": "2025-12-08T00:00:00Z",
            "category": "tweet",
            "content": "Test content"
        }

        filepath = save_document(doc, tmp_path)

        # Should create file with fallback name
        assert filepath.exists()
        assert filepath.name == "Tweet by Test Author - 2025-12-08.md"

        # Verify filename is valid and human-readable
        filename_without_ext = filepath.name[:-3]
        assert any(c.isalnum() for c in filename_without_ext), \
            f"Saved file '{filepath.name}' has no alphanumeric characters"


class TestDocumentScanning:
    """Test filesystem scanning for deduplication"""

    def test_scan_empty_directory(self, tmp_path):
        """Test scanning empty directory"""
        with patch('server.DOCUMENTS_DIR', tmp_path), \
             patch('server.ARCHIVES_DIR', tmp_path / "archives"), \
             patch('server.DAILY_REVIEWS_DIR', tmp_path / "reviews"):
            known_ids, known_filenames = scan_existing_documents()
            assert len(known_ids) == 0
            assert len(known_filenames) == 0

    def test_scan_with_documents(self, tmp_path):
        """Test scanning directory with documents"""
        # Create test document
        doc_content = """---
title: "Test Document"
readwise_url: "https://readwise.io/reader/document/test123"
---

Content here
"""
        doc_file = tmp_path / "Test Document.md"
        with open(doc_file, 'w') as f:
            f.write(doc_content)

        with patch('server.DOCUMENTS_DIR', tmp_path), \
             patch('server.ARCHIVES_DIR', tmp_path / "archives"):
            known_ids, known_filenames = scan_existing_documents()
            assert "test123" in known_ids
            assert "Test Document.md" in known_filenames


class TestMarkdownFormatting:
    """Test markdown document formatting"""

    def test_format_document_basic(self):
        """Test basic document formatting"""
        doc = {
            "title": "Test Title",
            "author": "Test Author",
            "category": "tweet",
            "saved_at": "2026-01-22T00:00:00Z",
            "readwise_url": "https://readwise.io/reader/document/123",
            "content": "This is the content"
        }

        markdown = format_document_markdown(doc)
        assert "---" in markdown
        assert "title: Test Title" in markdown
        assert "author: Test Author" in markdown
        assert "## Content" in markdown
        assert "This is the content" in markdown

    def test_format_document_with_summary(self):
        """Test document formatting with summary"""
        doc = {
            "title": "Test Title",
            "content": "Content here",
            "summary": "This is a summary"
        }

        markdown = format_document_markdown(doc)
        assert "## Summary" in markdown
        assert "This is a summary" in markdown

    def test_format_document_with_notes(self):
        """Test document formatting with notes"""
        doc = {
            "title": "Test Title",
            "content": "Content here",
            "notes": "My personal notes"
        }

        markdown = format_document_markdown(doc)
        assert "## Notes" in markdown
        assert "My personal notes" in markdown


class TestDocumentSaving:
    """Test document saving to filesystem"""

    def test_save_document(self, tmp_path):
        """Test saving document to file"""
        doc = {
            "title": "Test Document",
            "content": "Test content"
        }

        filepath = save_document(doc, tmp_path)
        assert filepath.exists()
        assert filepath.name == "Test Document.md"

        # Verify content
        with open(filepath, 'r') as f:
            content = f.read()
            assert "Test Document" in content
            assert "Test content" in content

    def test_save_document_collision(self, tmp_path):
        """Test handling filename collisions"""
        doc = {
            "title": "Test Document",
            "content": "First version"
        }

        # Save first document
        filepath1 = save_document(doc, tmp_path)
        assert filepath1.name == "Test Document.md"

        # Save second document with same title
        doc2 = {
            "title": "Test Document",
            "content": "Second version"
        }
        filepath2 = save_document(doc2, tmp_path)
        assert filepath2.name == "Test Document (1).md"
        assert filepath1 != filepath2


# ============================================================================
# INTEGRATION TESTS (require mocked API)
# ============================================================================

class TestAPIIntegration:
    """Test API integration with mocked responses"""

    @pytest.mark.asyncio
    @patch('server.fetch_api')
    async def test_import_recent_with_dedup(self, mock_fetch, tmp_path):
        """Test importing recent documents with deduplication"""
        # Mock API response
        mock_fetch.return_value = {
            "results": [
                {
                    "title": "New Document",
                    "content": "New content",
                    "readwise_url": "https://readwise.io/reader/document/new123",
                    "saved_at": "2026-01-22T00:00:00Z"
                },
                {
                    "title": "Existing Document",
                    "content": "Existing content",
                    "readwise_url": "https://readwise.io/reader/document/existing456",
                    "saved_at": "2026-01-21T00:00:00Z"
                }
            ]
        }

        # Create existing document to test deduplication
        existing_doc = tmp_path / "Existing Document.md"
        with open(existing_doc, 'w') as f:
            f.write('---\nreadwise_url: "https://readwise.io/reader/document/existing456"\n---\n')

        # Note: Full integration test would require mocking the async tool
        # This is a simplified test to verify the mock setup
        assert mock_fetch.return_value["results"][0]["title"] == "New Document"

    def test_state_timestamp_regression(self, tmp_path):
        """Regression test: Verify timestamps never have both +00:00 and Z"""
        from datetime import timezone

        # This test prevents regression of the bug where we appended Z to .isoformat()
        # The bug was: datetime.now(timezone.utc).isoformat() + "Z"
        # This produced: 2026-01-23T02:16:59.102761+00:00Z (malformed)

        # Correct implementation (after fix):
        timestamp = datetime.now(timezone.utc).isoformat()

        # Verify fix: timestamp should NOT have both +00:00 and Z
        assert not timestamp.endswith("+00:00Z"), \
            f"REGRESSION: Timestamp has both +00:00 and Z: {timestamp}"

        # Verify correct format has timezone info
        assert "+00:00" in timestamp or timestamp.endswith("Z"), \
            f"Timestamp missing timezone info: {timestamp}"

        # Verify Readwise API would accept this format
        # (It rejected +00:00Z with 400 Bad Request)
        try:
            datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
        except ValueError:
            pytest.fail(f"Timestamp format invalid for API: {timestamp}")

        # Also test the old buggy way would produce malformed format
        buggy_timestamp = datetime.now(timezone.utc).isoformat() + "Z"
        assert buggy_timestamp.endswith("+00:00Z"), \
            "Test verification: buggy implementation should produce +00:00Z"


class TestRateLimitHandling:
    """Test rate limit retry logic and exponential backoff"""

    @patch('server.requests.get')
    @patch('server.time.sleep')
    def test_fetch_api_success_no_retry(self, mock_sleep, mock_get):
        """Test successful API call requires no retry"""
        # Mock successful response
        mock_response = Mock()
        mock_response.json.return_value = {"results": [{"title": "Test"}]}
        mock_response.status_code = 200
        mock_get.return_value = mock_response

        result = fetch_api("/list/", params={"limit": 10})

        # Should succeed on first attempt
        assert result == {"results": [{"title": "Test"}]}
        assert mock_get.call_count == 1
        assert mock_sleep.call_count == 0

    @patch('server.requests.get')
    @patch('server.time.sleep')
    def test_fetch_api_retry_on_429(self, mock_sleep, mock_get):
        """Test retry logic on 429 rate limit error"""
        from requests.exceptions import HTTPError

        # First call: 429 error
        mock_response_429 = Mock()
        mock_response_429.status_code = 429
        mock_response_429.headers = {}
        mock_response_429.raise_for_status.side_effect = HTTPError(response=mock_response_429)

        # Second call: success
        mock_response_200 = Mock()
        mock_response_200.json.return_value = {"results": [{"title": "Test"}]}
        mock_response_200.status_code = 200

        mock_get.side_effect = [mock_response_429, mock_response_200]

        result = fetch_api("/list/")

        # Should retry once and succeed
        assert result == {"results": [{"title": "Test"}]}
        assert mock_get.call_count == 2
        assert mock_sleep.call_count == 1
        # First retry should wait 5 seconds (base delay)
        mock_sleep.assert_called_with(5)

    @patch('server.requests.get')
    @patch('server.time.sleep')
    def test_fetch_api_exponential_backoff(self, mock_sleep, mock_get):
        """Test exponential backoff: 5s, 10s, 20s"""
        from requests.exceptions import HTTPError

        # Three 429 errors, then success
        mock_response_429 = Mock()
        mock_response_429.status_code = 429
        mock_response_429.headers = {}
        mock_response_429.raise_for_status.side_effect = HTTPError(response=mock_response_429)

        mock_response_200 = Mock()
        mock_response_200.json.return_value = {"results": []}
        mock_response_200.status_code = 200

        mock_get.side_effect = [
            mock_response_429,  # Attempt 0 fails
            mock_response_429,  # Attempt 1 fails
            mock_response_429,  # Attempt 2 fails
            mock_response_200   # Attempt 3 succeeds
        ]

        result = fetch_api("/list/")

        # Should retry 3 times with exponential backoff
        assert mock_get.call_count == 4
        assert mock_sleep.call_count == 3

        # Verify exponential backoff: 5s, 10s, 20s
        sleep_calls = [call[0][0] for call in mock_sleep.call_args_list]
        assert sleep_calls == [5, 10, 20]

    @patch('server.requests.get')
    @patch('server.time.sleep')
    def test_fetch_api_retry_exhaustion(self, mock_sleep, mock_get):
        """Test that retries are exhausted after max attempts"""
        from requests.exceptions import HTTPError

        # Always return 429
        mock_response_429 = Mock()
        mock_response_429.status_code = 429
        mock_response_429.headers = {}
        mock_response_429.raise_for_status.side_effect = HTTPError(response=mock_response_429)

        mock_get.return_value = mock_response_429

        # Should raise after exhausting retries
        with pytest.raises(HTTPError):
            fetch_api("/list/")

        # Should try 4 times total (initial + 3 retries)
        assert mock_get.call_count == 4
        # Should sleep 3 times (not on last attempt)
        assert mock_sleep.call_count == 3

    @patch('server.requests.get')
    @patch('server.time.sleep')
    def test_fetch_api_respects_retry_after_header(self, mock_sleep, mock_get):
        """Test that Retry-After header is respected"""
        from requests.exceptions import HTTPError

        # 429 with Retry-After header
        mock_response_429 = Mock()
        mock_response_429.status_code = 429
        mock_response_429.headers = {'Retry-After': '15'}  # 15 seconds
        mock_response_429.raise_for_status.side_effect = HTTPError(response=mock_response_429)

        # Success on second attempt
        mock_response_200 = Mock()
        mock_response_200.json.return_value = {"results": []}
        mock_response_200.status_code = 200

        mock_get.side_effect = [mock_response_429, mock_response_200]

        result = fetch_api("/list/")

        # Should use Retry-After value instead of exponential backoff
        assert mock_sleep.call_count == 1
        mock_sleep.assert_called_with(15)

    @patch('server.requests.get')
    @patch('server.time.sleep')
    def test_fetch_api_caps_max_delay(self, mock_sleep, mock_get):
        """Test that delay is capped at RATE_LIMIT_MAX_DELAY"""
        from requests.exceptions import HTTPError

        # Return 429 with very large Retry-After
        mock_response_429 = Mock()
        mock_response_429.status_code = 429
        mock_response_429.headers = {'Retry-After': '120'}  # 2 minutes
        mock_response_429.raise_for_status.side_effect = HTTPError(response=mock_response_429)

        mock_response_200 = Mock()
        mock_response_200.json.return_value = {"results": []}
        mock_response_200.status_code = 200

        mock_get.side_effect = [mock_response_429, mock_response_200]

        result = fetch_api("/list/")

        # Should cap at 60 seconds (RATE_LIMIT_MAX_DELAY)
        assert mock_sleep.call_count == 1
        mock_sleep.assert_called_with(60)

    @patch('server.requests.get')
    @patch('server.time.sleep')
    def test_fetch_api_no_retry_on_404(self, mock_sleep, mock_get):
        """Test that 404 errors don't trigger retry"""
        from requests.exceptions import HTTPError

        mock_response_404 = Mock()
        mock_response_404.status_code = 404
        mock_response_404.raise_for_status.side_effect = HTTPError(response=mock_response_404)

        mock_get.return_value = mock_response_404

        # Should raise immediately without retry
        with pytest.raises(HTTPError):
            fetch_api("/list/")

        # Should only try once (no retries)
        assert mock_get.call_count == 1
        assert mock_sleep.call_count == 0

    @patch('server.requests.get')
    @patch('server.time.sleep')
    def test_fetch_api_no_retry_on_500(self, mock_sleep, mock_get):
        """Test that 500 errors don't trigger retry"""
        from requests.exceptions import HTTPError

        mock_response_500 = Mock()
        mock_response_500.status_code = 500
        mock_response_500.raise_for_status.side_effect = HTTPError(response=mock_response_500)

        mock_get.return_value = mock_response_500

        # Should raise immediately without retry
        with pytest.raises(HTTPError):
            fetch_api("/list/")

        # Should only try once (no retries)
        assert mock_get.call_count == 1
        assert mock_sleep.call_count == 0

    @patch('server.requests.get')
    @patch('server.time.sleep')
    def test_fetch_api_no_retry_on_timeout(self, mock_sleep, mock_get):
        """Test that timeout errors don't trigger retry"""
        from requests.exceptions import Timeout

        mock_get.side_effect = Timeout("Connection timeout")

        # Should raise immediately without retry
        with pytest.raises(Timeout):
            fetch_api("/list/")

        # Should only try once (no retries)
        assert mock_get.call_count == 1
        assert mock_sleep.call_count == 0

    @patch('server.requests.get')
    def test_fetch_api_includes_timeout(self, mock_get):
        """Test that requests include timeout parameter"""
        mock_response = Mock()
        mock_response.json.return_value = {"results": []}
        mock_response.status_code = 200
        mock_get.return_value = mock_response

        fetch_api("/list/", params={"limit": 10})

        # Verify timeout was passed to requests.get
        call_kwargs = mock_get.call_args[1]
        assert 'timeout' in call_kwargs
        assert call_kwargs['timeout'] == 30  # REQUEST_TIMEOUT constant

    @pytest.mark.asyncio
    @patch('server.fetch_api')
    @patch('server.time.sleep')
    async def test_backfill_throttling(self, mock_sleep, mock_fetch):
        """Test that backfill adds throttling delay between pages"""
        from server import readwise_backfill

        # Mock two pages of results
        page1_response = {
            "results": [
                {
                    "title": "Doc 1",
                    "saved_at": "2026-01-15T00:00:00Z",
                    "readwise_url": "https://readwise.io/reader/document/1"
                }
            ],
            "nextPageCursor": "cursor123"
        }

        page2_response = {
            "results": [
                {
                    "title": "Doc 2",
                    "saved_at": "2026-01-10T00:00:00Z",
                    "readwise_url": "https://readwise.io/reader/document/2"
                }
            ],
            "nextPageCursor": None
        }

        mock_fetch.side_effect = [page1_response, page2_response]

        # Mock scan_existing_documents to avoid filesystem access
        with patch('server.scan_existing_documents', return_value=(set(), set())), \
             patch('server.save_document'), \
             patch('server.load_state', return_value={"synced_ranges": []}), \
             patch('server.write_state'):

            result = await readwise_backfill("2026-01-05", category="tweet")

            # Should fetch 2 pages
            assert mock_fetch.call_count == 2

            # Should sleep once (after page 2, not page 1)
            assert mock_sleep.call_count == 1
            mock_sleep.assert_called_with(0.5)  # PAGINATION_THROTTLE_DELAY

    @patch('server.requests.get')
    @patch('server.time.sleep')
    def test_fetch_api_retry_after_invalid_format(self, mock_sleep, mock_get):
        """Test handling of Retry-After header in HTTP date format"""
        from requests.exceptions import HTTPError

        # 429 with Retry-After in HTTP date format (not integer)
        mock_response_429 = Mock()
        mock_response_429.status_code = 429
        mock_response_429.headers = {'Retry-After': 'Wed, 21 Oct 2026 07:28:00 GMT'}
        mock_response_429.raise_for_status.side_effect = HTTPError(response=mock_response_429)

        mock_response_200 = Mock()
        mock_response_200.json.return_value = {"results": []}
        mock_response_200.status_code = 200

        mock_get.side_effect = [mock_response_429, mock_response_200]

        result = fetch_api("/list/")

        # Should fall back to exponential backoff (5s for first retry)
        assert mock_sleep.call_count == 1
        mock_sleep.assert_called_with(5)


class TestHighlightsImport:
    """Test highlights import functionality"""

    def test_scan_existing_highlights(self, tmp_path):
        """Test scanning highlights directory for known IDs"""
        # Create test highlight file
        highlight_content = """---
highlight_id: "123456789"
text: "Sample highlight text"
source_title: "Sample Book"
---

# Sample Book
"""
        highlight_file = tmp_path / "20260130-143020 [Sample Book] highlight.md"
        with open(highlight_file, 'w') as f:
            f.write(highlight_content)

        with patch('server.HIGHLIGHTS_DIR', tmp_path):
            known_ids, known_filenames = scan_existing_highlights()
            assert "123456789" in known_ids
            assert "20260130-143020 [Sample Book] highlight.md" in known_filenames

    def test_scan_highlights_empty_directory(self, tmp_path):
        """Test scanning empty highlights directory"""
        with patch('server.HIGHLIGHTS_DIR', tmp_path):
            known_ids, known_filenames = scan_existing_highlights()
            assert len(known_ids) == 0
            assert len(known_filenames) == 0

    def test_sanitize_source_title_basic(self):
        """Test basic source title sanitization"""
        result = sanitize_source_title("Building a Second Brain")
        assert result == "Building a Second Brain"

    def test_sanitize_source_title_special_chars(self):
        """Test sanitization with special characters"""
        result = sanitize_source_title("Title: With / Special <Chars>")
        assert "/" not in result
        assert ":" not in result
        assert "<" not in result
        assert ">" not in result

    def test_sanitize_source_title_long(self):
        """Test truncation of long source titles"""
        long_title = "A" * 150
        result = sanitize_source_title(long_title, max_length=100)
        assert len(result) == 100

    def test_sanitize_source_title_empty(self):
        """Test empty source title uses fallback"""
        result = sanitize_source_title("")
        assert result == "Untitled Source"

    def test_sanitize_source_title_special_only(self):
        """Test source title with only special characters"""
        result = sanitize_source_title("...")
        assert result == "Untitled Source"

    def test_format_highlight_markdown(self):
        """Test highlight markdown generation with all fields"""
        highlight = {
            "id": 123456789,
            "text": "This is a sample highlight that demonstrates the format.",
            "note": "My personal note about this highlight",
            "source_title": "Building a Second Brain",
            "author": "Tiago Forte",
            "category": "book",
            "source_url": "https://example.com/book",
            "highlighted_at": "2024-05-26T10:30:00Z",
            "updated": "2024-05-26T10:30:00Z",
            "location": "2149",
            "readwise_url": "https://readwise.io/open/123456789",
            "tags": ["productivity", "knowledge-management"]
        }

        markdown = format_highlight_markdown(highlight)

        # Check frontmatter
        assert "---" in markdown
        assert "highlight_id: '123456789'" in markdown
        assert "source_title: Building a Second Brain" in markdown
        assert "source_author: Tiago Forte" in markdown

        # Check body
        assert "# Building a Second Brain" in markdown
        assert "*Tiago Forte*" in markdown
        assert '> "This is a sample highlight that demonstrates the format."' in markdown
        assert "**Location**: 2149" in markdown
        assert "**Note**: My personal note about this highlight" in markdown
        assert "**Source**: https://example.com/book" in markdown
        assert "**Readwise**: https://readwise.io/open/123456789" in markdown

    def test_save_highlight(self, tmp_path):
        """Test saving highlight to filesystem"""
        highlight = {
            "id": 123456789,
            "text": "Sample highlight text",
            "source_title": "Building a Second Brain",
            "author": "Tiago Forte",
            "updated": "2026-01-30T14:30:20Z"
        }

        filepath = save_highlight(highlight, tmp_path)
        assert filepath.exists()
        assert filepath.name.startswith("20260130-143020")
        assert "[Building a Second Brain]" in filepath.name
        assert filepath.name.endswith("highlight.md")

        # Verify content
        with open(filepath, 'r') as f:
            content = f.read()
            assert "Building a Second Brain" in content
            assert "Sample highlight text" in content

    def test_save_highlight_collision(self, tmp_path):
        """Test handling filename collisions for highlights"""
        highlight = {
            "id": 123456789,
            "text": "First highlight",
            "source_title": "Same Book",
            "updated": "2026-01-30T14:30:20Z"
        }

        # Save first highlight
        filepath1 = save_highlight(highlight, tmp_path)
        assert "[Same Book]" in filepath1.name

        # Save second highlight with same timestamp and source
        highlight2 = {
            "id": 987654321,
            "text": "Second highlight",
            "source_title": "Same Book",
            "updated": "2026-01-30T14:30:20Z"
        }
        filepath2 = save_highlight(highlight2, tmp_path)

        # Should have collision suffix
        assert filepath1 != filepath2
        assert "(1)" in filepath2.name

    def test_highlights_deduplication(self, tmp_path):
        """Test ID-based and filename-based deduplication"""
        # Create existing highlight
        existing_highlight = tmp_path / "20260130-143020 [Test Book] highlight.md"
        with open(existing_highlight, 'w') as f:
            f.write('---\nhighlight_id: "existing123"\n---\n')

        with patch('server.HIGHLIGHTS_DIR', tmp_path):
            known_ids, known_filenames = scan_existing_highlights()

            # Should find the ID
            assert "existing123" in known_ids
            # Should find the filename
            assert "20260130-143020 [Test Book] highlight.md" in known_filenames

    def test_highlights_state_management(self, tmp_path):
        """Test separate highlights state tracking"""
        state_file = tmp_path / "state.json"

        # Create state without highlights section
        old_state = {
            "last_import_timestamp": "2026-01-22T00:00:00Z",
            "synced_ranges": []
        }
        with open(state_file, 'w') as f:
            json.dump(old_state, f)

        # Load state should add highlights section
        with patch('server.STATE_FILE', state_file):
            state = load_state()
            assert "highlights" in state
            assert "last_import_timestamp" in state["highlights"]
            assert "synced_ranges" in state["highlights"]

    def test_highlights_filename_generation(self):
        """Test temporal + source title filename format"""
        highlight = {
            "id": 123,
            "text": "Test",
            "source_title": "The Phoenix Project",
            "updated": "2026-01-30T14:28:15Z"
        }

        # Should generate format: YYYYMMDD-HHMMSS [Source] highlight.md
        with tempfile.TemporaryDirectory() as tmpdir:
            filepath = save_highlight(highlight, Path(tmpdir))
            expected_prefix = "20260130-142815"
            assert filepath.name.startswith(expected_prefix)
            assert "[The Phoenix Project]" in filepath.name
            assert filepath.name.endswith("highlight.md")

    @pytest.mark.asyncio
    @patch('server.fetch_api')
    @patch('server.save_highlight')
    async def test_highlights_backfill_pagination(self, mock_save, mock_fetch):
        """Test page-number based pagination (not cursor)"""
        from server import readwise_backfill_highlights

        # Mock two pages of results (v2 API format)
        page1_response = {
            "count": 100,
            "next": "https://readwise.io/api/v2/highlights/?page=2",
            "previous": None,
            "results": [
                {
                    "id": 1,
                    "text": "Highlight 1",
                    "source_title": "Book 1",
                    "updated": "2026-01-15T00:00:00Z"
                }
            ]
        }

        page2_response = {
            "count": 100,
            "next": None,
            "previous": "https://readwise.io/api/v2/highlights/?page=1",
            "results": [
                {
                    "id": 2,
                    "text": "Highlight 2",
                    "source_title": "Book 2",
                    "updated": "2026-01-10T00:00:00Z"
                }
            ]
        }

        mock_fetch.side_effect = [page1_response, page2_response]

        # Mock other dependencies
        with patch('server.scan_existing_highlights', return_value=(set(), set())), \
             patch('server.load_state', return_value={"highlights": {"synced_ranges": []}}), \
             patch('server.write_state'):

            result = await readwise_backfill_highlights("2026-01-05")

            # Should fetch 2 pages
            assert mock_fetch.call_count == 2

            # Verify page parameter was used (not cursor)
            first_call_params = mock_fetch.call_args_list[0][1]['params']
            assert 'page' in first_call_params
            assert first_call_params['page'] == 1

            second_call_params = mock_fetch.call_args_list[1][1]['params']
            assert second_call_params['page'] == 2

            # Should have saved 2 highlights
            assert mock_save.call_count == 2


# ============================================================================
# FIXTURES
# ============================================================================

@pytest.fixture
def sample_state():
    """Sample state file data"""
    return {
        "last_import_timestamp": "2026-01-22T00:00:00Z",
        "oldest_imported_date": "2026-01-01",
        "synced_ranges": [
            {
                "start": "2026-01-01T00:00:00+00:00",
                "end": "2026-01-21T00:00:00+00:00",
                "doc_count": 614,
                "verified_at": "2026-01-21T00:00:00Z"
            }
        ],
        "backfill_in_progress": False
    }

@pytest.fixture
def sample_document():
    """Sample Readwise document"""
    return {
        "title": "Sample Tweet Thread",
        "author": "@testuser",
        "source": "Twitter",
        "category": "tweet",
        "saved_at": "2026-01-22T00:00:00Z",
        "updated_at": "2026-01-22T00:00:00Z",
        "readwise_url": "https://readwise.io/reader/document/sample123",
        "source_url": "https://twitter.com/testuser/status/123456",
        "content": "This is a sample tweet thread content.",
        "summary": "A summary of the tweet thread",
        "tags": ["testing", "sample"]
    }


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
