"""Unit tests for Vector Store data models.

This module contains unit tests for the ArchonChunk, SearchResult,
and SymbolKind data models.

Feature: vector-store-arn

Source:
- src/vector/models.py
- .kiro/specs/vector-store-arn/design.md

Validates:
    Requirements 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 3.1, 3.2
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.vector.models import ArchonChunk, SearchResult, SymbolKind


class TestArchonChunkDataclass:
    """Unit tests for ArchonChunk dataclass.
    
    Validates:
        Requirements 1.1, 1.2, 1.3, 1.4, 1.5, 1.6
    """

    def test_archon_chunk_instantiation_with_all_fields(self):
        """Test ArchonChunk can be instantiated with all fields."""
        chunk = ArchonChunk(
            content="Test content",
            source="test/file.py",
            chunk_index=0,
            arn="arn:archon:code:ws/pkg/test/file.py#Symbol",
            related_arns=["arn:archon:code:ws/pkg/other.py#Other"],
            symbol_name="Symbol",
            symbol_kind="function",
            package="TestPackage",
        )
        
        assert chunk.content == "Test content"
        assert chunk.source == "test/file.py"
        assert chunk.chunk_index == 0
        assert chunk.arn == "arn:archon:code:ws/pkg/test/file.py#Symbol"
        assert chunk.related_arns == ["arn:archon:code:ws/pkg/other.py#Other"]
        assert chunk.symbol_name == "Symbol"
        assert chunk.symbol_kind == "function"
        assert chunk.package == "TestPackage"

    def test_archon_chunk_with_default_values(self):
        """Test ArchonChunk uses correct default values."""
        chunk = ArchonChunk(
            content="Test content",
            source="test/file.py",
            chunk_index=0,
            arn="arn:archon:code:ws/pkg/test/file.py",
        )
        
        assert chunk.related_arns == []
        assert chunk.symbol_name is None
        assert chunk.symbol_kind is None
        assert chunk.package == ""

    def test_archon_chunk_with_empty_related_arns(self):
        """Test ArchonChunk with empty related_arns list."""
        chunk = ArchonChunk(
            content="Test content",
            source="test/file.py",
            chunk_index=0,
            arn="arn:archon:code:ws/pkg/test/file.py",
            related_arns=[],
        )
        
        assert chunk.related_arns == []
        assert isinstance(chunk.related_arns, list)

    def test_archon_chunk_with_multiple_related_arns(self):
        """Test ArchonChunk with multiple related_arns."""
        related = [
            "arn:archon:code:ws/pkg/a.py#A",
            "arn:archon:code:ws/pkg/b.py#B",
            "arn:archon:code:ws/pkg/c.py#C",
        ]
        chunk = ArchonChunk(
            content="Test content",
            source="test/file.py",
            chunk_index=0,
            arn="arn:archon:code:ws/pkg/test/file.py",
            related_arns=related,
        )
        
        assert chunk.related_arns == related
        assert len(chunk.related_arns) == 3

    def test_archon_chunk_with_none_optional_fields(self):
        """Test ArchonChunk with None for optional fields."""
        chunk = ArchonChunk(
            content="Test content",
            source="test/file.py",
            chunk_index=0,
            arn="arn:archon:code:ws/pkg/test/file.py",
            symbol_name=None,
            symbol_kind=None,
        )
        
        assert chunk.symbol_name is None
        assert chunk.symbol_kind is None

    def test_archon_chunk_chunk_index_zero(self):
        """Test ArchonChunk with chunk_index of 0."""
        chunk = ArchonChunk(
            content="First chunk",
            source="test/file.py",
            chunk_index=0,
            arn="arn:archon:code:ws/pkg/test/file.py",
        )
        
        assert chunk.chunk_index == 0

    def test_archon_chunk_chunk_index_positive(self):
        """Test ArchonChunk with positive chunk_index."""
        chunk = ArchonChunk(
            content="Later chunk",
            source="test/file.py",
            chunk_index=42,
            arn="arn:archon:code:ws/pkg/test/file.py",
        )
        
        assert chunk.chunk_index == 42


class TestSearchResultDataclass:
    """Unit tests for SearchResult dataclass.
    
    Validates:
        Requirements 3.1, 3.2
    """

    def test_search_result_instantiation_with_all_fields(self):
        """Test SearchResult can be instantiated with all fields."""
        result = SearchResult(
            content="Test content",
            source="test/file.py",
            chunk_index=0,
            score=0.95,
            arn="arn:archon:code:ws/pkg/test/file.py#Symbol",
            related_arns=["arn:archon:code:ws/pkg/other.py#Other"],
            symbol_name="Symbol",
            symbol_kind="function",
            package="TestPackage",
        )
        
        assert result.content == "Test content"
        assert result.source == "test/file.py"
        assert result.chunk_index == 0
        assert result.score == 0.95
        assert result.arn == "arn:archon:code:ws/pkg/test/file.py#Symbol"
        assert result.related_arns == ["arn:archon:code:ws/pkg/other.py#Other"]
        assert result.symbol_name == "Symbol"
        assert result.symbol_kind == "function"
        assert result.package == "TestPackage"

    def test_search_result_score_at_boundaries(self):
        """Test SearchResult with score at boundary values."""
        result_zero = SearchResult(
            content="Test",
            source="test.py",
            chunk_index=0,
            score=0.0,
            arn="arn:archon:code:ws/pkg/test.py",
        )
        assert result_zero.score == 0.0
        
        result_one = SearchResult(
            content="Test",
            source="test.py",
            chunk_index=0,
            score=1.0,
            arn="arn:archon:code:ws/pkg/test.py",
        )
        assert result_one.score == 1.0

    def test_search_result_with_default_values(self):
        """Test SearchResult uses correct default values."""
        result = SearchResult(
            content="Test content",
            source="test/file.py",
            chunk_index=0,
            score=0.5,
            arn="arn:archon:code:ws/pkg/test/file.py",
        )
        
        assert result.related_arns == []
        assert result.symbol_name is None
        assert result.symbol_kind is None
        assert result.package == ""

    def test_search_result_score_precision(self):
        """Test SearchResult preserves score precision."""
        result = SearchResult(
            content="Test",
            source="test.py",
            chunk_index=0,
            score=0.123456789,
            arn="arn:archon:code:ws/pkg/test.py",
        )
        
        assert result.score == 0.123456789


class TestSymbolKindEnum:
    """Unit tests for SymbolKind enum.
    
    Validates:
        Requirement 6.6
    """

    def test_symbol_kind_function_value(self):
        """Test SymbolKind.FUNCTION has correct value."""
        assert SymbolKind.FUNCTION.value == "function"

    def test_symbol_kind_class_value(self):
        """Test SymbolKind.CLASS has correct value."""
        assert SymbolKind.CLASS.value == "class"

    def test_symbol_kind_method_value(self):
        """Test SymbolKind.METHOD has correct value."""
        assert SymbolKind.METHOD.value == "method"

    def test_symbol_kind_variable_value(self):
        """Test SymbolKind.VARIABLE has correct value."""
        assert SymbolKind.VARIABLE.value == "variable"

    def test_symbol_kind_type_value(self):
        """Test SymbolKind.TYPE has correct value."""
        assert SymbolKind.TYPE.value == "type"

    def test_symbol_kind_module_value(self):
        """Test SymbolKind.MODULE has correct value."""
        assert SymbolKind.MODULE.value == "module"

    def test_symbol_kind_all_values(self):
        """Test all SymbolKind values are present."""
        expected_values = {"function", "class", "method", "variable", "type", "module"}
        actual_values = {kind.value for kind in SymbolKind}
        
        assert actual_values == expected_values

    def test_symbol_kind_from_string(self):
        """Test SymbolKind can be created from string value."""
        assert SymbolKind("function") == SymbolKind.FUNCTION
        assert SymbolKind("class") == SymbolKind.CLASS
        assert SymbolKind("method") == SymbolKind.METHOD

    def test_symbol_kind_string_comparison(self):
        """Test SymbolKind can be compared to strings."""
        assert SymbolKind.FUNCTION == "function"
        assert SymbolKind.CLASS == "class"

    def test_symbol_kind_invalid_value_raises(self):
        """Test invalid SymbolKind value raises ValueError."""
        with pytest.raises(ValueError):
            SymbolKind("invalid")
