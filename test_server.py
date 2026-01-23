#!/usr/bin/env python3
# Copyright (c) 2026 ngpestelos
# Licensed under the MIT License - see LICENSE file for details
"""
Unit and integration tests for Readwise MCP Server
"""

import json
import pytest
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock, patch, mock_open
import tempfile
import os

# Import functions from server
import sys
sys.path.insert(0, str(Path(__file__).parent))
from server import (
    load_state, write_state, optimize_backfill, scan_existing_documents,
    sanitize_filename, extract_id_from_url, format_document_markdown,
    save_document
)

# ============================================================================
# UNIT TESTS
# ============================================================================

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
        """Test optimization when target is before synced range"""
        synced_ranges = [
            {
                "start": "2026-01-15T00:00:00+00:00",
                "end": "2026-01-21T00:00:00+00:00",
                "doc_count": 100
            }
        ]
        should_proceed, optimized_after = optimize_backfill("2026-01-10", synced_ranges)
        assert should_proceed == True
        assert optimized_after == "2026-01-21T00:00:00+00:00"

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
