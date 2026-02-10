"""Qdrant filter builders for the Vector Store.

This module provides utility functions for building Qdrant filters
used in vector store search operations. Filters enable efficient
filtering by package and symbol kind before similarity ranking.

The filter builders create Qdrant Filter objects with FieldCondition
and MatchValue for exact matching on payload fields.

Functions:
    build_package_filter: Create a filter for package matching
    build_symbol_kind_filter: Create a filter for symbol kind matching
    build_combined_filter: Create a combined filter with AND logic

Source:
- .kiro/specs/vector-store-arn/design.md
- .kiro/specs/vector-store-arn/requirements.md

Validates:
    Requirements 5.1, 5.2, 5.3, 5.4, 5.5, 6.1, 6.2, 6.3, 6.4, 6.5, 6.6,
    7.1, 7.2, 7.3, 7.4
"""

from typing import List, Optional

from qdrant_client.models import FieldCondition, Filter, MatchValue

from src.vector.models import SymbolKind


def build_package_filter(package: str) -> Filter:
    """Build a Qdrant filter for package filtering.

    Creates a filter that matches chunks with the specified package name.
    The filter uses exact matching on the 'package' payload field.

    Args:
        package: Package name to filter by (e.g., "ArchonDocumentationMCPTools")

    Returns:
        Qdrant Filter configured to match the specified package

    Example:
        >>> filter = build_package_filter("ArchonDocumentationMCPTools")
        >>> # Use in Qdrant search:
        >>> # client.search(collection_name="archon-docs", query_filter=filter, ...)

    Note:
        The filter is applied before similarity ranking, ensuring only
        chunks from the specified package are considered in the search.

    Validates:
        Requirements 5.1, 5.2, 5.3, 5.4, 5.5
    """
    return Filter(
        must=[
            FieldCondition(
                key="package",
                match=MatchValue(value=package),
            )
        ]
    )


def build_symbol_kind_filter(symbol_kind: str) -> Filter:
    """Build a Qdrant filter for symbol kind filtering.

    Creates a filter that matches chunks with the specified symbol kind.
    The filter uses exact matching on the 'symbol_kind' payload field.

    Args:
        symbol_kind: Symbol kind to filter by. Must be one of the valid
            SymbolKind values: 'function', 'class', 'method', 'variable',
            'type', 'module'.

    Returns:
        Qdrant Filter configured to match the specified symbol kind

    Raises:
        ValueError: If symbol_kind is not a valid SymbolKind value

    Example:
        >>> filter = build_symbol_kind_filter("function")
        >>> # Use in Qdrant search:
        >>> # client.search(collection_name="archon-docs", query_filter=filter, ...)

    Note:
        The filter is applied before similarity ranking, ensuring only
        chunks with the specified symbol kind are considered in the search.

    Validates:
        Requirements 6.1, 6.2, 6.3, 6.4, 6.5, 6.6
    """
    valid_kinds = {kind.value for kind in SymbolKind}
    if symbol_kind not in valid_kinds:
        raise ValueError(
            f"Invalid symbol_kind '{symbol_kind}'. "
            f"Must be one of: {', '.join(sorted(valid_kinds))}"
        )

    return Filter(
        must=[
            FieldCondition(
                key="symbol_kind",
                match=MatchValue(value=symbol_kind),
            )
        ]
    )


def build_combined_filter(
    package: Optional[str] = None,
    symbol_kind: Optional[str] = None,
) -> Optional[Filter]:
    """Build a combined Qdrant filter for package and symbol kind.

    Creates a filter that combines package and symbol_kind conditions with
    AND logic using Qdrant's Filter.must. This allows filtering chunks that
    match both conditions simultaneously.

    Args:
        package: Optional package name to filter by
            (e.g., "ArchonDocumentationMCPTools")
        symbol_kind: Optional symbol kind to filter by. Must be one of the
            valid SymbolKind values: 'function', 'class', 'method',
            'variable', 'type', 'module'.

    Returns:
        Qdrant Filter with AND logic if any filters are specified,
        None if both parameters are None

    Raises:
        ValueError: If symbol_kind is provided but not a valid SymbolKind value

    Example:
        >>> # Combined filter for functions in a specific package
        >>> filter = build_combined_filter(
        ...     package="ArchonDocumentationMCPTools",
        ...     symbol_kind="function",
        ... )
        >>> # Use in Qdrant search:
        >>> # client.search(collection_name="archon-docs", query_filter=filter, ...)

        >>> # Package filter only
        >>> filter = build_combined_filter(package="ArchonAgent")

        >>> # Symbol kind filter only
        >>> filter = build_combined_filter(symbol_kind="class")

        >>> # No filters - returns None
        >>> filter = build_combined_filter()  # Returns None

    Note:
        The filter is applied before similarity ranking, ensuring only
        chunks matching ALL specified conditions are considered in the search.
        When both filters are specified, chunks must match both the package
        AND the symbol_kind to be included in results.

    Validates:
        Requirements 7.1, 7.2, 7.3, 7.4
    """
    conditions: List[FieldCondition] = []

    if package is not None:
        conditions.append(
            FieldCondition(
                key="package",
                match=MatchValue(value=package),
            )
        )

    if symbol_kind is not None:
        valid_kinds = {kind.value for kind in SymbolKind}
        if symbol_kind not in valid_kinds:
            raise ValueError(
                f"Invalid symbol_kind '{symbol_kind}'. "
                f"Must be one of: {', '.join(sorted(valid_kinds))}"
            )
        conditions.append(
            FieldCondition(
                key="symbol_kind",
                match=MatchValue(value=symbol_kind),
            )
        )

    if not conditions:
        return None

    return Filter(must=conditions)
