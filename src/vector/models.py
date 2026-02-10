"""Data models for the Vector Store with ARN metadata.

This module defines the data models for document chunks and search results
in the Archon Knowledge Base vector store. Chunks include ARN metadata
that enables graph traversal and cross-referencing with the Code Graph.

The models support:
- ARN-enriched chunks for graph integration
- Search results with similarity scores and metadata
- Filtering by package and symbol kind
- SymbolKind enum for type-safe symbol classification

Source:
- .kiro/specs/vector-store-arn/design.md
- .kiro/specs/vector-store-arn/requirements.md
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional


class SymbolKind(str, Enum):
    """Classification of code symbols for filtering and categorization.

    Defines the valid kinds of code symbols that can be documented and
    stored in the vector store. Used for filtering search results by
    symbol type.

    Values:
        FUNCTION: Standalone function
        CLASS: Class definition
        METHOD: Method within a class
        VARIABLE: Variable or constant
        TYPE: Type definition or interface
        MODULE: Module or namespace

    Example:
        >>> kind = SymbolKind.FUNCTION
        >>> kind.value
        'function'
        >>> SymbolKind('class')
        <SymbolKind.CLASS: 'class'>

    Note:
        Inherits from str to allow direct string comparison and JSON
        serialization without explicit conversion.

    Validates:
        Requirement 6.6
    """

    FUNCTION = "function"
    CLASS = "class"
    METHOD = "method"
    VARIABLE = "variable"
    TYPE = "type"
    MODULE = "module"


@dataclass
class ArchonChunk:
    """Document chunk with ARN metadata for graph traversal.

    Represents a segment of documentation text with associated metadata
    and ARN (Archon Resource Name) information for linking to the Code Graph.

    The chunk includes:
    - Base content fields (content, source, chunk_index)
    - ARN metadata for graph integration (arn, related_arns)
    - Context fields for filtering (symbol_name, symbol_kind, package)

    ARN Format: arn:archon:<type>:<workspace>/<package>/<path>#<symbol>
    Example: arn:archon:doc:personal-work/ArchonDocumentationMCPTools/src/tools/scip_indexing.archon.md

    Attributes:
        content: Chunk text content (the actual documentation text)
        source: File path relative to workspace
            (e.g., "ArchonAgent/src/orchestrator/main.py")
        chunk_index: Index of chunk within the source file (0-based)
        arn: ARN of the documented symbol or file
        related_arns: List of ARNs referenced in this chunk's content
        symbol_name: Name of the symbol being documented (optional)
        symbol_kind: Kind of symbol - function, class, method, variable,
            type, or module (optional)
        package: Package/repository name for filtering

    Example:
        >>> chunk = ArchonChunk(
        ...     content="This function generates SCIP indexes...",
        ...     source="ArchonDocumentationMCPTools/src/tools/scip_indexing.archon.md",
        ...     chunk_index=0,
        ...     arn="arn:archon:doc:workspace/ArchonDocumentationMCPTools/src/tools/scip_indexing.ts",
        ...     related_arns=[
        ...         "arn:archon:code:workspace/ArchonDocumentationMCPTools/src/lib/language_detector.ts#detect"
        ...     ],
        ...     symbol_name="generateScipIndex",
        ...     symbol_kind="function",
        ...     package="ArchonDocumentationMCPTools",
        ... )

    Validates:
        Requirements 1.1, 1.2, 1.3, 1.4, 1.5, 1.6
    """

    content: str
    source: str
    chunk_index: int

    arn: str
    related_arns: List[str] = field(default_factory=list)

    symbol_name: Optional[str] = None
    symbol_kind: Optional[str] = None
    package: str = ""


@dataclass
class SearchResult:
    """Search result with ARN metadata for graph traversal.

    Represents a search result from the vector store, including the chunk
    content, similarity score, and ARN metadata for linking to the Code Graph.

    The search result includes all ArchonChunk fields plus a similarity score
    that indicates how well the result matches the query.

    Attributes:
        content: Chunk text content (the actual documentation text)
        source: File path relative to workspace
            (e.g., "ArchonAgent/src/orchestrator/main.py")
        chunk_index: Index of chunk within the source file (0-based)
        score: Similarity score between 0.0 and 1.0 (higher is more similar)
        arn: ARN of the documented symbol or file
        related_arns: List of ARNs referenced in this chunk's content
        symbol_name: Name of the symbol being documented (optional)
        symbol_kind: Kind of symbol - function, class, method, variable,
            type, or module (optional)
        package: Package/repository name for filtering

    Example:
        >>> result = SearchResult(
        ...     content="This function generates SCIP indexes...",
        ...     source="ArchonDocumentationMCPTools/src/tools/scip_indexing.archon.md",
        ...     chunk_index=0,
        ...     score=0.92,
        ...     arn="arn:archon:doc:workspace/ArchonDocumentationMCPTools/src/tools/scip_indexing.ts",
        ...     related_arns=[
        ...         "arn:archon:code:workspace/ArchonDocumentationMCPTools/src/lib/language_detector.ts#detect"
        ...     ],
        ...     symbol_name="generateScipIndex",
        ...     symbol_kind="function",
        ...     package="ArchonDocumentationMCPTools",
        ... )

    Validates:
        Requirements 3.1, 3.2
    """

    content: str
    source: str
    chunk_index: int
    score: float

    arn: str
    related_arns: List[str] = field(default_factory=list)

    symbol_name: Optional[str] = None
    symbol_kind: Optional[str] = None
    package: str = ""
