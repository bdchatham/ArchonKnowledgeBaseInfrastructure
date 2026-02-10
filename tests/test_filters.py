"""Unit tests for Qdrant filter builders.

This module contains unit tests for the filter builder functions
used in vector store search operations.

Feature: vector-store-arn

Source:
- src/vector/filters.py
- .kiro/specs/vector-store-arn/design.md

Validates:
    Requirements 5.1, 5.2, 5.3, 5.4, 5.5, 6.1, 6.2, 6.3, 6.4, 6.5, 6.6,
    7.1, 7.2, 7.3, 7.4
"""

import sys
from pathlib import Path

import pytest
from qdrant_client.models import Filter, FieldCondition, MatchValue

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.vector.filters import (
    build_package_filter,
    build_symbol_kind_filter,
    build_combined_filter,
)


class TestBuildPackageFilter:
    """Unit tests for build_package_filter function.
    
    Validates:
        Requirements 5.1, 5.2, 5.3, 5.4, 5.5
    """

    def test_build_package_filter_returns_filter(self):
        """Test build_package_filter returns a Filter object."""
        result = build_package_filter("TestPackage")
        
        assert isinstance(result, Filter)

    def test_build_package_filter_has_must_condition(self):
        """Test build_package_filter creates a must condition."""
        result = build_package_filter("TestPackage")
        
        assert result.must is not None
        assert len(result.must) == 1

    def test_build_package_filter_correct_key(self):
        """Test build_package_filter uses 'package' key."""
        result = build_package_filter("TestPackage")
        
        condition = result.must[0]
        assert condition.key == "package"

    def test_build_package_filter_correct_value(self):
        """Test build_package_filter uses correct package value."""
        result = build_package_filter("MyPackage")
        
        condition = result.must[0]
        assert condition.match.value == "MyPackage"

    def test_build_package_filter_with_hyphenated_name(self):
        """Test build_package_filter with hyphenated package name."""
        result = build_package_filter("my-package-name")
        
        condition = result.must[0]
        assert condition.match.value == "my-package-name"

    def test_build_package_filter_with_underscored_name(self):
        """Test build_package_filter with underscored package name."""
        result = build_package_filter("my_package_name")
        
        condition = result.must[0]
        assert condition.match.value == "my_package_name"


class TestBuildSymbolKindFilter:
    """Unit tests for build_symbol_kind_filter function.
    
    Validates:
        Requirements 6.1, 6.2, 6.3, 6.4, 6.5, 6.6
    """

    def test_build_symbol_kind_filter_returns_filter(self):
        """Test build_symbol_kind_filter returns a Filter object."""
        result = build_symbol_kind_filter("function")
        
        assert isinstance(result, Filter)

    def test_build_symbol_kind_filter_has_must_condition(self):
        """Test build_symbol_kind_filter creates a must condition."""
        result = build_symbol_kind_filter("function")
        
        assert result.must is not None
        assert len(result.must) == 1

    def test_build_symbol_kind_filter_correct_key(self):
        """Test build_symbol_kind_filter uses 'symbol_kind' key."""
        result = build_symbol_kind_filter("function")
        
        condition = result.must[0]
        assert condition.key == "symbol_kind"

    def test_build_symbol_kind_filter_function(self):
        """Test build_symbol_kind_filter with 'function' kind."""
        result = build_symbol_kind_filter("function")
        
        condition = result.must[0]
        assert condition.match.value == "function"

    def test_build_symbol_kind_filter_class(self):
        """Test build_symbol_kind_filter with 'class' kind."""
        result = build_symbol_kind_filter("class")
        
        condition = result.must[0]
        assert condition.match.value == "class"

    def test_build_symbol_kind_filter_method(self):
        """Test build_symbol_kind_filter with 'method' kind."""
        result = build_symbol_kind_filter("method")
        
        condition = result.must[0]
        assert condition.match.value == "method"

    def test_build_symbol_kind_filter_variable(self):
        """Test build_symbol_kind_filter with 'variable' kind."""
        result = build_symbol_kind_filter("variable")
        
        condition = result.must[0]
        assert condition.match.value == "variable"

    def test_build_symbol_kind_filter_type(self):
        """Test build_symbol_kind_filter with 'type' kind."""
        result = build_symbol_kind_filter("type")
        
        condition = result.must[0]
        assert condition.match.value == "type"

    def test_build_symbol_kind_filter_module(self):
        """Test build_symbol_kind_filter with 'module' kind."""
        result = build_symbol_kind_filter("module")
        
        condition = result.must[0]
        assert condition.match.value == "module"

    def test_build_symbol_kind_filter_invalid_raises(self):
        """Test build_symbol_kind_filter raises for invalid kind."""
        with pytest.raises(ValueError) as exc_info:
            build_symbol_kind_filter("invalid")
        
        assert "Invalid symbol_kind" in str(exc_info.value)

    def test_build_symbol_kind_filter_uppercase_raises(self):
        """Test build_symbol_kind_filter raises for uppercase kind."""
        with pytest.raises(ValueError):
            build_symbol_kind_filter("FUNCTION")

    def test_build_symbol_kind_filter_empty_raises(self):
        """Test build_symbol_kind_filter raises for empty kind."""
        with pytest.raises(ValueError):
            build_symbol_kind_filter("")


class TestBuildCombinedFilter:
    """Unit tests for build_combined_filter function.
    
    Validates:
        Requirements 7.1, 7.2, 7.3, 7.4
    """

    def test_build_combined_filter_both_params(self):
        """Test build_combined_filter with both package and symbol_kind."""
        result = build_combined_filter(
            package="TestPackage",
            symbol_kind="function",
        )
        
        assert isinstance(result, Filter)
        assert result.must is not None
        assert len(result.must) == 2

    def test_build_combined_filter_package_only(self):
        """Test build_combined_filter with package only."""
        result = build_combined_filter(package="TestPackage")
        
        assert isinstance(result, Filter)
        assert result.must is not None
        assert len(result.must) == 1
        assert result.must[0].key == "package"

    def test_build_combined_filter_symbol_kind_only(self):
        """Test build_combined_filter with symbol_kind only."""
        result = build_combined_filter(symbol_kind="function")
        
        assert isinstance(result, Filter)
        assert result.must is not None
        assert len(result.must) == 1
        assert result.must[0].key == "symbol_kind"

    def test_build_combined_filter_neither_returns_none(self):
        """Test build_combined_filter with neither param returns None."""
        result = build_combined_filter()
        
        assert result is None

    def test_build_combined_filter_none_values_returns_none(self):
        """Test build_combined_filter with None values returns None."""
        result = build_combined_filter(package=None, symbol_kind=None)
        
        assert result is None

    def test_build_combined_filter_correct_values(self):
        """Test build_combined_filter uses correct values."""
        result = build_combined_filter(
            package="MyPackage",
            symbol_kind="class",
        )
        
        package_condition = None
        symbol_condition = None
        
        for condition in result.must:
            if condition.key == "package":
                package_condition = condition
            elif condition.key == "symbol_kind":
                symbol_condition = condition
        
        assert package_condition is not None
        assert package_condition.match.value == "MyPackage"
        
        assert symbol_condition is not None
        assert symbol_condition.match.value == "class"

    def test_build_combined_filter_invalid_symbol_kind_raises(self):
        """Test build_combined_filter raises for invalid symbol_kind."""
        with pytest.raises(ValueError):
            build_combined_filter(
                package="TestPackage",
                symbol_kind="invalid",
            )

    def test_build_combined_filter_package_none_symbol_valid(self):
        """Test build_combined_filter with None package and valid symbol_kind."""
        result = build_combined_filter(
            package=None,
            symbol_kind="method",
        )
        
        assert isinstance(result, Filter)
        assert len(result.must) == 1
        assert result.must[0].key == "symbol_kind"
        assert result.must[0].match.value == "method"

    def test_build_combined_filter_package_valid_symbol_none(self):
        """Test build_combined_filter with valid package and None symbol_kind."""
        result = build_combined_filter(
            package="TestPackage",
            symbol_kind=None,
        )
        
        assert isinstance(result, Filter)
        assert len(result.must) == 1
        assert result.must[0].key == "package"
        assert result.must[0].match.value == "TestPackage"


class TestFilterStructure:
    """Tests for filter structure and Qdrant compatibility.
    
    Validates:
        Requirements 5.1, 6.1, 7.1
    """

    def test_package_filter_uses_field_condition(self):
        """Test package filter uses FieldCondition."""
        result = build_package_filter("TestPackage")
        
        assert isinstance(result.must[0], FieldCondition)

    def test_symbol_kind_filter_uses_field_condition(self):
        """Test symbol_kind filter uses FieldCondition."""
        result = build_symbol_kind_filter("function")
        
        assert isinstance(result.must[0], FieldCondition)

    def test_combined_filter_uses_field_conditions(self):
        """Test combined filter uses FieldConditions."""
        result = build_combined_filter(
            package="TestPackage",
            symbol_kind="function",
        )
        
        for condition in result.must:
            assert isinstance(condition, FieldCondition)

    def test_package_filter_uses_match_value(self):
        """Test package filter uses MatchValue."""
        result = build_package_filter("TestPackage")
        
        assert isinstance(result.must[0].match, MatchValue)

    def test_symbol_kind_filter_uses_match_value(self):
        """Test symbol_kind filter uses MatchValue."""
        result = build_symbol_kind_filter("function")
        
        assert isinstance(result.must[0].match, MatchValue)
