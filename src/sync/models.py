"""Data models for the Knowledge Base Sync Service.

This module defines the data models for the sync service, including both
input data models (SCIP parse results and Archon documentation) and result
type models for reporting sync operation outcomes.

Input Data Models:
- SymbolLocation: Location of a symbol in source code
- ScipSymbol: Symbol extracted from a SCIP index
- ScipRelationship: Relationship between symbols from a SCIP index
- ScipParseResult: Result of parsing a SCIP index
- GeneratedDoc: Generated Archon documentation file

Result Type Models:
- SyncResult: Result of a package sync operation
- PackageSyncResult: Result of syncing a single package within a workspace sync
- WorkspaceSyncResult: Result of a workspace sync operation
- GraphSyncResult: Result of a graph sync operation
- VectorSyncResult: Result of a vector sync operation

These models are used by the KnowledgeBaseSyncService for transforming
code intelligence data into Code Graph nodes/edges and Vector Store chunks,
and for reporting the outcomes of sync operations.

Source:
- .kiro/specs/sync-service/design.md (Data Models section)
- .kiro/specs/sync-service/requirements.md (Requirements 1.1, 1.2, 1.3, 9.1, 9.2, 9.3, 9.4)
"""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class SymbolLocation:
    """Location of a symbol in source code.

    Represents the precise location where a symbol is defined in a source
    file, including file path, line number, and column position.

    Attributes:
        file: File path relative to the package root
        line: Line number in the source file (1-indexed)
        column: Column position in the line (0-indexed)

    Example:
        >>> location = SymbolLocation(
        ...     file="src/main.py",
        ...     line=42,
        ...     column=4,
        ... )

    Validates:
        Requirement 1.1 (SCIP parse result input handling)
    """

    file: str
    line: int
    column: int


@dataclass
class ScipSymbol:
    """Symbol extracted from a SCIP index.

    Represents a code symbol (function, class, method, etc.) extracted from
    a SCIP index. Contains all metadata needed to create a Code Graph node.

    The ARN (Archon Resource Name) uniquely identifies the symbol across
    the entire knowledge base.

    ARN Format: arn:archon:<type>:<workspace>/<package>/<path>#<symbol>
    Example: arn:archon:code:personal-work/MyPackage/src/main.py#my_function

    Attributes:
        arn: Archon Resource Name uniquely identifying the symbol
        type: Resource type classification (code, doc, k8s, infra)
        workspace: Workspace identifier (e.g., 'personal-work')
        package: Package/repository name
        path: File path relative to package root
        symbol: Symbol identifier within the file (optional for file-level symbols)
        kind: Symbol kind (function, class, method, variable, type, module)
        name: Human-readable name of the symbol
        signature: Function/method signature (optional)
        documentation: Documentation string (optional)
        location: Location of the symbol in source code

    Example:
        >>> symbol = ScipSymbol(
        ...     arn="arn:archon:code:personal-work/MyPackage/src/main.py#my_function",
        ...     type="code",
        ...     workspace="personal-work",
        ...     package="MyPackage",
        ...     path="src/main.py",
        ...     symbol="my_function",
        ...     kind="function",
        ...     name="my_function",
        ...     signature="def my_function(x: int) -> str",
        ...     documentation="Converts an integer to a string.",
        ...     location=SymbolLocation(file="src/main.py", line=42, column=0),
        ... )

    Validates:
        Requirement 1.1 (SCIP parse result input handling)
    """

    arn: str
    type: str  # code, doc, k8s, infra
    workspace: str
    package: str
    path: str
    symbol: Optional[str]
    kind: str  # function, class, method, variable, type, module
    name: str
    signature: Optional[str]
    documentation: Optional[str]
    location: SymbolLocation


@dataclass
class ScipRelationship:
    """Relationship between symbols from a SCIP index.

    Represents a directed relationship between two symbols in the code graph.
    The relationship connects a source symbol (from_arn) to a target symbol
    (to_arn) with a specific relationship type.

    Relationship types:
        - contains: Parent contains child (e.g., class contains method)
        - references: Source references target (e.g., function calls another)
        - implements: Source implements target interface/type
        - extends: Source extends target class/type
        - imports: Source imports target module

    Attributes:
        from_arn: ARN of the source symbol
        to_arn: ARN of the target symbol
        type: Relationship type (contains, references, implements, extends, imports)

    Example:
        >>> relationship = ScipRelationship(
        ...     from_arn="arn:archon:code:personal-work/MyPackage/src/main.py#MyClass",
        ...     to_arn="arn:archon:code:personal-work/MyPackage/src/main.py#my_method",
        ...     type="contains",
        ... )

    Validates:
        Requirement 1.1 (SCIP parse result input handling)
    """

    from_arn: str
    to_arn: str
    type: str  # contains, references, implements, extends, imports


@dataclass
class ScipParseResult:
    """Result of parsing a SCIP index.

    Contains all symbols and relationships extracted from a SCIP index for
    a package, along with a hash for change detection.

    The hash is a SHA-256 hash of the SCIP index content, used to detect
    whether the index has changed since the last sync. If the hash matches
    the stored hash, synchronization can be skipped.

    Attributes:
        symbols: List of symbols discovered in the package
        relationships: List of relationships between symbols
        hash: SHA-256 hash of the SCIP index content for change detection

    Example:
        >>> result = ScipParseResult(
        ...     symbols=[symbol1, symbol2],
        ...     relationships=[rel1, rel2],
        ...     hash="abc123def456...",
        ... )

    Validates:
        Requirement 1.1 (SCIP parse result input handling)
    """

    symbols: list[ScipSymbol]
    relationships: list[ScipRelationship]
    hash: str  # SHA-256 hash of the SCIP index content


@dataclass
class GeneratedDoc:
    """Generated Archon documentation file.

    Represents a documentation file generated by the Archon documentation
    pipeline. Contains the documentation content along with metadata for
    linking to the Code Graph.

    The ARN identifies the symbol or file being documented, and referenced_arns
    lists all symbols referenced in the documentation content.

    Attributes:
        source_path: Path to the source file being documented
        doc_path: Path to the generated .archon.md file
        content: Documentation content (markdown text)
        arn: ARN of the documented symbol/file
        referenced_arns: List of ARNs referenced in the documentation

    Example:
        >>> doc = GeneratedDoc(
        ...     source_path="src/main.py",
        ...     doc_path="src/main.archon.md",
        ...     content="# my_function\\n\\nConverts an integer to a string...",
        ...     arn="arn:archon:doc:personal-work/MyPackage/src/main.py#my_function",
        ...     referenced_arns=[
        ...         "arn:archon:code:personal-work/MyPackage/src/utils.py#helper",
        ...     ],
        ... )

    Validates:
        Requirement 1.2 (Archon documentation input handling)
    """

    source_path: str  # Path to the source file being documented
    doc_path: str  # Path to the generated .archon.md file
    content: str  # Documentation content
    arn: str  # ARN of the documented symbol/file
    referenced_arns: list[str] = field(default_factory=list)  # ARNs referenced in the documentation


@dataclass
class HashState:
    """Stored hash state for a package.

    Represents the persisted state of a package's SCIP index hash, used for
    change detection during synchronization. When the current SCIP index hash
    matches the stored hash, synchronization can be skipped to save resources.

    The state is stored in a JSON file (default: `.archon/sync-state.json`)
    and loaded by the ChangeDetector service.

    State File Schema:
        {
          "packages": {
            "ArchonDocumentationMCPTools": {
              "index_hash": "abc123...",
              "last_synced": "2024-01-15T10:30:00Z"
            }
          }
        }

    Attributes:
        package: Package name/path that was synchronized
        index_hash: SHA-256 hash of the SCIP index content
        last_synced: ISO 8601 timestamp of the last successful sync

    Example:
        >>> state = HashState(
        ...     package="ArchonDocumentationMCPTools",
        ...     index_hash="abc123def456789...",
        ...     last_synced="2024-01-15T10:30:00Z",
        ... )

    Validates:
        Requirement 7.1 (Skip synchronization when hash unchanged)
        Requirement 7.2 (Store last synchronized index hash per package)
    """

    package: str
    index_hash: str
    last_synced: str  # ISO 8601 timestamp


# =============================================================================
# Result Type Models
# =============================================================================
# These models represent the results of sync operations, providing detailed
# information about what was created, updated, pruned, and any errors.
# =============================================================================


@dataclass
class GraphSyncResult:
    """Result of a graph sync operation.

    Contains counts of nodes and edges created, updated, and pruned during
    synchronization of SCIP parse results to the Code Graph.

    This result type is used by the GraphSyncAdapter to report the outcome
    of sync_symbols, sync_relationships, and prune_stale_nodes operations.

    Attributes:
        nodes_created: Count of new nodes inserted into the Code Graph
        nodes_updated: Count of existing nodes updated in the Code Graph
        edges_created: Count of new edges inserted into the Code Graph
        nodes_pruned: Count of stale nodes removed from the Code Graph

    Example:
        >>> result = GraphSyncResult(
        ...     nodes_created=10,
        ...     nodes_updated=5,
        ...     edges_created=25,
        ...     nodes_pruned=2,
        ... )

    Validates:
        Requirement 9.2 (SyncResult type fields)
    """

    nodes_created: int
    nodes_updated: int
    edges_created: int
    nodes_pruned: int


@dataclass
class VectorSyncResult:
    """Result of a vector sync operation.

    Contains counts of chunks upserted and pruned during synchronization
    of Archon documentation to the Vector Store, along with any errors.

    This result type is used by the VectorSyncAdapter to report the outcome
    of sync_docs and prune_stale_chunks operations.

    Attributes:
        chunks_upserted: Count of chunks upserted to the Vector Store
        chunks_pruned: Count of stale chunks removed from the Vector Store
        errors: List of error messages encountered during sync

    Example:
        >>> result = VectorSyncResult(
        ...     chunks_upserted=50,
        ...     chunks_pruned=3,
        ...     errors=[],
        ... )

    Validates:
        Requirement 9.2 (SyncResult type fields)
    """

    chunks_upserted: int
    chunks_pruned: int
    errors: list[str] = field(default_factory=list)


@dataclass
class SyncResult:
    """Result of a package sync operation.

    Comprehensive result type containing all metrics from synchronizing
    a single package's code intelligence to the knowledge base. Includes
    counts for both Code Graph (nodes, edges) and Vector Store (chunks)
    operations.

    When synchronization is skipped due to unchanged hash, the skipped
    field is True and skip_reason provides the explanation.

    Attributes:
        success: Boolean indicating overall success of the sync operation
        nodes_created: Count of new nodes inserted into the Code Graph
        nodes_updated: Count of existing nodes updated in the Code Graph
        edges_created: Count of new edges inserted into the Code Graph
        chunks_upserted: Count of chunks upserted to the Vector Store
        nodes_pruned: Count of stale nodes removed from the Code Graph
        chunks_pruned: Count of stale chunks removed from the Vector Store
        skipped: Boolean indicating if sync was skipped due to unchanged hash
        skip_reason: Explanation for why sync was skipped (if skipped=True)
        errors: List of error messages encountered during sync

    Example:
        >>> result = SyncResult(
        ...     success=True,
        ...     nodes_created=10,
        ...     nodes_updated=5,
        ...     edges_created=25,
        ...     chunks_upserted=50,
        ...     nodes_pruned=2,
        ...     chunks_pruned=3,
        ...     skipped=False,
        ...     skip_reason=None,
        ...     errors=[],
        ... )

    Example (skipped sync):
        >>> result = SyncResult(
        ...     success=True,
        ...     nodes_created=0,
        ...     nodes_updated=0,
        ...     edges_created=0,
        ...     chunks_upserted=0,
        ...     nodes_pruned=0,
        ...     chunks_pruned=0,
        ...     skipped=True,
        ...     skip_reason="SCIP index hash unchanged",
        ...     errors=[],
        ... )

    Validates:
        Requirement 9.1 (sync_package method signature)
        Requirement 9.2 (SyncResult type definition)
    """

    success: bool
    nodes_created: int
    nodes_updated: int
    edges_created: int
    chunks_upserted: int
    nodes_pruned: int
    chunks_pruned: int
    skipped: bool
    skip_reason: Optional[str]
    errors: list[str] = field(default_factory=list)


@dataclass
class PackageSyncResult:
    """Result of syncing a single package within a workspace sync.

    Extended version of SyncResult that includes the package identifier,
    used when reporting results for individual packages within a workspace
    sync operation.

    Attributes:
        package: Package name/path that was synchronized
        success: Boolean indicating overall success of the sync operation
        nodes_created: Count of new nodes inserted into the Code Graph
        nodes_updated: Count of existing nodes updated in the Code Graph
        edges_created: Count of new edges inserted into the Code Graph
        chunks_upserted: Count of chunks upserted to the Vector Store
        nodes_pruned: Count of stale nodes removed from the Code Graph
        chunks_pruned: Count of stale chunks removed from the Vector Store
        skipped: Boolean indicating if sync was skipped due to unchanged hash
        skip_reason: Explanation for why sync was skipped (if skipped=True)
        errors: List of error messages encountered during sync

    Example:
        >>> result = PackageSyncResult(
        ...     package="ArchonDocumentationMCPTools",
        ...     success=True,
        ...     nodes_created=10,
        ...     nodes_updated=5,
        ...     edges_created=25,
        ...     chunks_upserted=50,
        ...     nodes_pruned=2,
        ...     chunks_pruned=3,
        ...     skipped=False,
        ...     skip_reason=None,
        ...     errors=[],
        ... )

    Validates:
        Requirement 9.3 (sync_workspace method signature)
        Requirement 9.4 (WorkspaceSyncResult type definition)
    """

    package: str
    success: bool
    nodes_created: int
    nodes_updated: int
    edges_created: int
    chunks_upserted: int
    nodes_pruned: int
    chunks_pruned: int
    skipped: bool
    skip_reason: Optional[str]
    errors: list[str] = field(default_factory=list)


@dataclass
class WorkspaceSyncResult:
    """Result of a workspace sync operation.

    Aggregated result type for synchronizing all packages in a workspace.
    Contains per-package results, list of skipped packages, and errors
    organized by package.

    The success field is True only if all packages synced successfully
    (or were intentionally skipped due to unchanged hash).

    Attributes:
        success: Boolean indicating overall success (all packages succeeded)
        packages_synced: List of PackageSyncResult for each synced package
        packages_skipped: List of package names skipped due to unchanged hash
        errors: Dictionary mapping package names to their error lists

    Example:
        >>> result = WorkspaceSyncResult(
        ...     success=True,
        ...     packages_synced=[pkg1_result, pkg2_result],
        ...     packages_skipped=["UnchangedPackage"],
        ...     errors={},
        ... )

    Example (partial failure):
        >>> result = WorkspaceSyncResult(
        ...     success=False,
        ...     packages_synced=[pkg1_result],
        ...     packages_skipped=[],
        ...     errors={"FailedPackage": ["Database connection failed"]},
        ... )

    Validates:
        Requirement 9.3 (sync_workspace method signature)
        Requirement 9.4 (WorkspaceSyncResult type definition)
    """

    success: bool
    packages_synced: list[PackageSyncResult] = field(default_factory=list)
    packages_skipped: list[str] = field(default_factory=list)
    errors: dict[str, list[str]] = field(default_factory=dict)  # package -> errors
