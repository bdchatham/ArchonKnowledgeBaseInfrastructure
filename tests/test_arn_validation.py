"""Unit tests for ARN validation utilities.

This module contains unit tests for ARN format validation, parsing,
and building functions.

Feature: vector-store-arn

Source:
- src/vector/arn.py
- .kiro/specs/vector-store-arn/design.md

Validates:
    Requirements 4.1, 4.2
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.vector.arn import (
    validate_arn,
    parse_arn,
    validate_arn_type,
    build_arn,
    ARN_PATTERN,
    VALID_ARN_TYPES,
    ARNComponents,
)


class TestValidateArn:
    """Unit tests for validate_arn function.
    
    Validates:
        Requirements 4.1, 4.2
    """

    def test_valid_arn_code_type(self):
        """Test valid ARN with code type is accepted."""
        arn = "arn:archon:code:workspace/package/src/file.py#Symbol"
        assert validate_arn(arn) is True

    def test_valid_arn_doc_type(self):
        """Test valid ARN with doc type is accepted."""
        arn = "arn:archon:doc:workspace/package/docs/readme.md"
        assert validate_arn(arn) is True

    def test_valid_arn_k8s_type(self):
        """Test valid ARN with k8s type is accepted."""
        arn = "arn:archon:k8s:workspace/package/manifests/deployment.yaml"
        assert validate_arn(arn) is True

    def test_valid_arn_infra_type(self):
        """Test valid ARN with infra type is accepted."""
        arn = "arn:archon:infra:workspace/package/terraform/main.tf"
        assert validate_arn(arn) is True

    def test_valid_arn_without_symbol(self):
        """Test valid ARN without symbol fragment is accepted."""
        arn = "arn:archon:doc:workspace/package/path/file.md"
        assert validate_arn(arn) is True

    def test_valid_arn_with_symbol(self):
        """Test valid ARN with symbol fragment is accepted."""
        arn = "arn:archon:code:workspace/package/path/file.py#MyClass"
        assert validate_arn(arn) is True

    def test_valid_arn_with_nested_path(self):
        """Test valid ARN with deeply nested path is accepted."""
        arn = "arn:archon:code:workspace/package/src/deep/nested/path/file.py#Symbol"
        assert validate_arn(arn) is True

    def test_valid_arn_with_underscores(self):
        """Test valid ARN with underscores in components is accepted."""
        arn = "arn:archon:code:my_workspace/my_package/src/my_file.py#my_symbol"
        assert validate_arn(arn) is True

    def test_valid_arn_with_numbers(self):
        """Test valid ARN with numbers in components is accepted."""
        arn = "arn:archon:code:workspace123/package456/src/file2.py#Symbol3"
        assert validate_arn(arn) is True

    def test_valid_arn_with_hyphens(self):
        """Test valid ARN with hyphens in components is accepted."""
        arn = "arn:archon:code:my-workspace/my-package/src/my-file.py#my-symbol"
        assert validate_arn(arn) is True

    def test_invalid_arn_empty_string(self):
        """Test empty ARN string is rejected."""
        assert validate_arn("") is False

    def test_invalid_arn_none(self):
        """Test None ARN is rejected."""
        assert validate_arn(None) is False

    def test_invalid_arn_missing_prefix(self):
        """Test ARN without 'arn:archon:' prefix is rejected."""
        assert validate_arn("code:workspace/package/path") is False
        assert validate_arn("archon:code:workspace/package/path") is False

    def test_invalid_arn_wrong_prefix(self):
        """Test ARN with wrong prefix is rejected."""
        assert validate_arn("arn:aws:code:workspace/package/path") is False
        assert validate_arn("urn:archon:code:workspace/package/path") is False

    def test_invalid_arn_invalid_type(self):
        """Test ARN with invalid type is rejected."""
        assert validate_arn("arn:archon:invalid:workspace/package/path") is False
        assert validate_arn("arn:archon:CODE:workspace/package/path") is False
        assert validate_arn("arn:archon:Doc:workspace/package/path") is False

    def test_invalid_arn_missing_workspace(self):
        """Test ARN without workspace is rejected."""
        assert validate_arn("arn:archon:code:/package/path") is False

    def test_invalid_arn_missing_package(self):
        """Test ARN without package is rejected."""
        assert validate_arn("arn:archon:code:workspace//path") is False

    def test_invalid_arn_missing_path(self):
        """Test ARN without path is rejected."""
        assert validate_arn("arn:archon:code:workspace/package/") is False
        assert validate_arn("arn:archon:code:workspace/package") is False

    def test_invalid_arn_only_prefix(self):
        """Test ARN with only prefix is rejected."""
        assert validate_arn("arn:archon:code:") is False

    def test_invalid_arn_whitespace(self):
        """Test ARN with leading whitespace is rejected."""
        assert validate_arn(" arn:archon:code:ws/pkg/path") is False


class TestParseArn:
    """Unit tests for parse_arn function.
    
    Validates:
        Requirements 4.1, 4.2
    """

    def test_parse_valid_arn_with_symbol(self):
        """Test parsing valid ARN with symbol."""
        arn = "arn:archon:code:workspace/package/src/file.py#Symbol"
        result = parse_arn(arn)
        
        assert result is not None
        assert result.type == "code"
        assert result.workspace == "workspace"
        assert result.package == "package"
        assert result.path == "src/file.py"
        assert result.symbol == "Symbol"

    def test_parse_valid_arn_without_symbol(self):
        """Test parsing valid ARN without symbol."""
        arn = "arn:archon:doc:workspace/package/docs/readme.md"
        result = parse_arn(arn)
        
        assert result is not None
        assert result.type == "doc"
        assert result.workspace == "workspace"
        assert result.package == "package"
        assert result.path == "docs/readme.md"
        assert result.symbol is None

    def test_parse_valid_arn_with_nested_path(self):
        """Test parsing valid ARN with nested path."""
        arn = "arn:archon:code:ws/pkg/src/deep/nested/file.py#Sym"
        result = parse_arn(arn)
        
        assert result is not None
        assert result.path == "src/deep/nested/file.py"

    def test_parse_invalid_arn_returns_none(self):
        """Test parsing invalid ARN returns None."""
        assert parse_arn("invalid") is None
        assert parse_arn("") is None
        assert parse_arn(None) is None

    def test_parse_arn_returns_frozen_dataclass(self):
        """Test parse_arn returns a frozen ARNComponents instance."""
        arn = "arn:archon:code:ws/pkg/path/file.py"
        result = parse_arn(arn)
        
        assert isinstance(result, ARNComponents)
        
        with pytest.raises(Exception):
            result.type = "doc"


class TestValidateArnType:
    """Unit tests for validate_arn_type function.
    
    Validates:
        Requirement 4.2
    """

    def test_valid_type_code(self):
        """Test 'code' is a valid ARN type."""
        assert validate_arn_type("code") is True

    def test_valid_type_doc(self):
        """Test 'doc' is a valid ARN type."""
        assert validate_arn_type("doc") is True

    def test_valid_type_k8s(self):
        """Test 'k8s' is a valid ARN type."""
        assert validate_arn_type("k8s") is True

    def test_valid_type_infra(self):
        """Test 'infra' is a valid ARN type."""
        assert validate_arn_type("infra") is True

    def test_invalid_type_uppercase(self):
        """Test uppercase types are invalid."""
        assert validate_arn_type("CODE") is False
        assert validate_arn_type("Doc") is False

    def test_invalid_type_unknown(self):
        """Test unknown types are invalid."""
        assert validate_arn_type("unknown") is False
        assert validate_arn_type("api") is False
        assert validate_arn_type("") is False


class TestBuildArn:
    """Unit tests for build_arn function.
    
    Validates:
        Requirement 4.2
    """

    def test_build_arn_with_symbol(self):
        """Test building ARN with symbol."""
        arn = build_arn(
            arn_type="code",
            workspace="workspace",
            package="package",
            path="src/file.py",
            symbol="Symbol",
        )
        
        assert arn == "arn:archon:code:workspace/package/src/file.py#Symbol"

    def test_build_arn_without_symbol(self):
        """Test building ARN without symbol."""
        arn = build_arn(
            arn_type="doc",
            workspace="workspace",
            package="package",
            path="docs/readme.md",
        )
        
        assert arn == "arn:archon:doc:workspace/package/docs/readme.md"

    def test_build_arn_with_none_symbol(self):
        """Test building ARN with None symbol."""
        arn = build_arn(
            arn_type="code",
            workspace="ws",
            package="pkg",
            path="file.py",
            symbol=None,
        )
        
        assert arn == "arn:archon:code:ws/pkg/file.py"
        assert "#" not in arn

    def test_build_arn_produces_valid_arn(self):
        """Test that build_arn produces a valid ARN."""
        arn = build_arn(
            arn_type="code",
            workspace="workspace",
            package="package",
            path="src/file.py",
            symbol="Symbol",
        )
        
        assert validate_arn(arn) is True

    def test_build_arn_roundtrip(self):
        """Test that build_arn and parse_arn are inverses."""
        original = build_arn(
            arn_type="code",
            workspace="my-workspace",
            package="my-package",
            path="src/deep/file.py",
            symbol="MySymbol",
        )
        
        parsed = parse_arn(original)
        rebuilt = build_arn(
            arn_type=parsed.type,
            workspace=parsed.workspace,
            package=parsed.package,
            path=parsed.path,
            symbol=parsed.symbol,
        )
        
        assert original == rebuilt


class TestArnConstants:
    """Unit tests for ARN constants.
    
    Validates:
        Requirement 4.2
    """

    def test_valid_arn_types_contains_all_types(self):
        """Test VALID_ARN_TYPES contains all expected types."""
        expected = {"code", "doc", "k8s", "infra"}
        assert VALID_ARN_TYPES == expected

    def test_valid_arn_types_is_frozenset(self):
        """Test VALID_ARN_TYPES is immutable."""
        assert isinstance(VALID_ARN_TYPES, frozenset)

    def test_arn_pattern_matches_valid_arns(self):
        """Test ARN_PATTERN regex matches valid ARNs."""
        valid_arns = [
            "arn:archon:code:ws/pkg/path/file.py",
            "arn:archon:doc:ws/pkg/path/file.md",
            "arn:archon:k8s:ws/pkg/path/file.yaml",
            "arn:archon:infra:ws/pkg/path/file.tf",
            "arn:archon:code:ws/pkg/path/file.py#Symbol",
        ]
        
        for arn in valid_arns:
            assert ARN_PATTERN.match(arn) is not None, f"Expected {arn} to match"

    def test_arn_pattern_rejects_invalid_arns(self):
        """Test ARN_PATTERN regex rejects invalid ARNs."""
        invalid_arns = [
            "arn:archon:invalid:ws/pkg/path",
            "arn:aws:code:ws/pkg/path",
            "code:ws/pkg/path",
            "",
        ]
        
        for arn in invalid_arns:
            assert ARN_PATTERN.match(arn) is None, f"Expected {arn} to not match"
