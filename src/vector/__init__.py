"""Vector store module for ARN-enriched semantic search.

This module provides the vector store functionality for the Archon Knowledge Base,
including chunk storage with ARN metadata for graph traversal and semantic search
with filtering capabilities.

Components:
- models: Data models for chunks and search results (ArchonChunk, SearchResult)
- arn: ARN validation utilities (validate_arn, parse_arn, ARNComponents)
- store: Vector store service for upsert and search operations
- filters: Qdrant filter builders for package and symbol kind filtering

Source:
- .kiro/specs/vector-store-arn/design.md
"""

from src.vector.arn import (
    ARN_PATTERN,
    ARNComponents,
    VALID_ARN_TYPES,
    build_arn,
    parse_arn,
    validate_arn,
    validate_arn_type,
)
from src.vector.filters import (
    build_combined_filter,
    build_package_filter,
    build_symbol_kind_filter,
)
from src.vector.models import ArchonChunk, SearchResult, SymbolKind
from src.vector.store import (
    VectorStoreService,
    VectorStoreError,
    VectorStoreConnectionError,
    VectorStoreConfigurationError,
)

__all__ = [
    "ArchonChunk",
    "SearchResult",
    "SymbolKind",
    "ARN_PATTERN",
    "ARNComponents",
    "VALID_ARN_TYPES",
    "build_arn",
    "parse_arn",
    "validate_arn",
    "validate_arn_type",
    "build_combined_filter",
    "build_package_filter",
    "build_symbol_kind_filter",
    "VectorStoreService",
    "VectorStoreError",
    "VectorStoreConnectionError",
    "VectorStoreConfigurationError",
]
