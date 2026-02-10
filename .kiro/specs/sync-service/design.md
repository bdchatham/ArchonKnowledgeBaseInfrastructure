# Design Document: Knowledge Base Sync Service

## Overview

The Knowledge Base Sync Service orchestrates the synchronization of code intelligence data (SCIP parse results and Archon documentation) to the knowledge base. It transforms SCIP symbols and relationships into Code Graph nodes and edges, chunks and embeds Archon documentation for the Vector Store, and ensures the knowledge base reflects the current state of the codebase.

**This is a child spec** implementing Requirements 12-13 (Knowledge Base Sync Service, Documentation Pipeline Triggers) from the root Archon Agent Pipeline specification.

### Parent Specification Reference

- **Root Spec Location:** `.kiro/specs/archon-agent-pipeline/`
- **Root Spec Repository:** Workspace root (personal-work)
- **Related Requirements:** Requirements 12 (Knowledge Base Sync Service), 13 (Documentation Pipeline Triggers)
- **Related Correctness Properties:** Properties 27-28

### Design Principles

1. **Idempotent Operations**: Multiple syncs with the same input produce identical state
2. **Change Detection**: Skip synchronization when SCIP index hash hasn't changed
3. **Atomic Transactions**: All-or-nothing sync per package using database transactions
4. **Deterministic Identifiers**: ARN-based keys for nodes, edges, and chunks
5. **Efficient Batching**: Bulk operations for nodes, edges, and embeddings
6. **Graceful Degradation**: Partial failures don't block entire workspace sync


## Architecture

### System Context

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                     ArchonKnowledgeBaseInfrastructure                        │
│                                                                              │
│  ┌──────────────────────────────────────────────────────────────────────┐  │
│  │                    Knowledge Base Sync Service                        │  │
│  │                                                                       │  │
│  │  ┌─────────────────────────────────────────────────────────────────┐ │  │
│  │  │                    Sync Orchestrator                             │ │  │
│  │  │  - sync_package()                                               │ │  │
│  │  │  - sync_workspace()                                             │ │  │
│  │  │  - change detection                                             │ │  │
│  │  └─────────────────────────────────────────────────────────────────┘ │  │
│  │           │                                │                          │  │
│  │           ▼                                ▼                          │  │
│  │  ┌─────────────────┐              ┌─────────────────┐                │  │
│  │  │ Graph Sync      │              │ Vector Sync     │                │  │
│  │  │ Adapter         │              │ Adapter         │                │  │
│  │  │ - transform     │              │ - chunk         │                │  │
│  │  │ - upsert nodes  │              │ - embed         │                │  │
│  │  │ - upsert edges  │              │ - upsert        │                │  │
│  │  │ - prune stale   │              │ - prune stale   │                │  │
│  │  └────────┬────────┘              └────────┬────────┘                │  │
│  │           │                                │                          │  │
│  └───────────┼────────────────────────────────┼──────────────────────────┘  │
│              │                                │                              │
│              ▼                                ▼                              │
│  ┌─────────────────────────┐    ┌─────────────────────────┐                │
│  │    PostgreSQL           │    │    Qdrant               │                │
│  │    Code Graph           │    │    Vector Store         │                │
│  │    - nodes              │    │    - embeddings         │                │
│  │    - edges              │    │    - ARN metadata       │                │
│  └─────────────────────────┘    └─────────────────────────┘                │
└─────────────────────────────────────────────────────────────────────────────┘
                                    ▲
                                    │
┌───────────────────────────────────┼─────────────────────────────────────────┐
│                    External Inputs                                           │
│                                                                              │
│  ┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐         │
│  │ SCIP Parse      │    │ Archon Docs     │    │ Kiro Hooks      │         │
│  │ Results         │    │ (Generated)     │    │ (Triggers)      │         │
│  └─────────────────┘    └─────────────────┘    └─────────────────┘         │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Sync Flow Diagram

```
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│ SCIP Parse      │────▶│ Graph Sync      │────▶│ PostgreSQL      │
│ Result          │     │ Adapter         │     │ Code Graph      │
│ - symbols       │     │ - transform     │     │ - nodes         │
│ - relationships │     │ - upsert        │     │ - edges         │
│ - hash          │     │ - prune         │     │                 │
└─────────────────┘     └─────────────────┘     └─────────────────┘

┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│ Archon Docs     │────▶│ Vector Sync     │────▶│ Qdrant          │
│ - content       │     │ Adapter         │     │ Vector Store    │
│ - arn           │     │ - chunk         │     │ - embeddings    │
│ - references    │     │ - embed         │     │ - ARN metadata  │
└─────────────────┘     │ - upsert        │     │                 │
                        └─────────────────┘     └─────────────────┘
```


### Package Structure

```
ArchonKnowledgeBaseInfrastructure/
├── src/
│   ├── sync/                       # Sync service module
│   │   ├── __init__.py
│   │   ├── service.py              # KnowledgeBaseSyncService
│   │   ├── graph_adapter.py        # Graph sync adapter
│   │   ├── vector_adapter.py       # Vector sync adapter
│   │   ├── change_detector.py      # Hash-based change detection
│   │   └── models.py               # SyncResult, WorkspaceSyncResult
│   ├── graph/                      # Code Graph module (from code-graph-storage spec)
│   │   └── ...
│   ├── vector/                     # Vector Store module (from vector-store-arn spec)
│   │   └── ...
│   └── common/
│       ├── database.py             # Database connection utilities
│       └── embedding.py            # Embedding service client
└── .kiro/
    ├── hooks/
    │   └── archon-knowledge-base-sync.json  # Kiro hook configuration
    └── specs/
        └── sync-service/           # This spec
```


## Component Interfaces

### Knowledge Base Sync Service

The main orchestrator for synchronizing code intelligence to the knowledge base.

```python
from dataclasses import dataclass
from typing import Optional


@dataclass
class SyncResult:
    """Result of a package sync operation."""
    success: bool
    nodes_created: int
    nodes_updated: int
    edges_created: int
    chunks_upserted: int
    nodes_pruned: int
    chunks_pruned: int
    skipped: bool
    skip_reason: Optional[str]
    errors: list[str]


@dataclass
class PackageSyncResult:
    """Result of syncing a single package within a workspace sync."""
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
    errors: list[str]


@dataclass
class WorkspaceSyncResult:
    """Result of a workspace sync operation."""
    success: bool
    packages_synced: list[PackageSyncResult]
    packages_skipped: list[str]
    errors: dict[str, list[str]]  # package -> errors


class KnowledgeBaseSyncService:
    """Synchronizes code intelligence data to the knowledge base."""
    
    def __init__(
        self,
        graph_adapter: "GraphSyncAdapter",
        vector_adapter: "VectorSyncAdapter",
        change_detector: "ChangeDetector",
    ) -> None:
        """Initialize the sync service.
        
        Args:
            graph_adapter: Adapter for Code Graph operations
            vector_adapter: Adapter for Vector Store operations
            change_detector: Service for hash-based change detection
        """
        ...
    
    async def sync_package(
        self,
        package_path: str,
        scip_result: "ScipParseResult",
        archon_docs: list["GeneratedDoc"],
    ) -> SyncResult:
        """Sync a package's code intelligence to the knowledge base.
        
        Performs the following steps:
        1. Check if SCIP index hash has changed (skip if unchanged)
        2. Upsert symbols to Code Graph as nodes
        3. Upsert relationships to Code Graph as edges
        4. Chunk and embed Archon docs
        5. Upsert chunks to Vector Store with ARN metadata
        6. Prune stale nodes and chunks
        7. Update stored hash
        
        Args:
            package_path: Path to the package being synchronized
            scip_result: Parsed SCIP index with symbols, relationships, and hash
            archon_docs: List of generated Archon documentation files
            
        Returns:
            SyncResult with counts and any errors
        """
        ...
    
    async def sync_workspace(
        self,
        workspace_path: str,
        force: bool = False,
    ) -> WorkspaceSyncResult:
        """Sync all packages in a workspace to the knowledge base.
        
        Discovers packages in the workspace, loads their SCIP indexes
        and Archon docs, and syncs each package.
        
        Args:
            workspace_path: Path to the workspace root
            force: If True, bypass change detection and sync all packages
            
        Returns:
            WorkspaceSyncResult with per-package results
        """
        ...
```


### Graph Sync Adapter

Transforms SCIP parse results to Code Graph operations.

```python
from dataclasses import dataclass


@dataclass
class GraphSyncResult:
    """Result of a graph sync operation."""
    nodes_created: int
    nodes_updated: int
    edges_created: int
    nodes_pruned: int


class GraphSyncAdapter:
    """Adapter for syncing SCIP data to the Code Graph."""
    
    def __init__(self, graph_repository: "GraphRepository") -> None:
        """Initialize the graph sync adapter.
        
        Args:
            graph_repository: Repository for Code Graph operations
        """
        ...
    
    async def sync_symbols(
        self,
        package: str,
        symbols: list["ScipSymbol"],
        index_hash: str,
    ) -> tuple[int, int]:
        """Sync SCIP symbols to Code Graph nodes.
        
        Transforms each symbol to a GraphNode and performs upsert.
        
        Args:
            package: Package name
            symbols: List of SCIP symbols to sync
            index_hash: Hash of the SCIP index for change tracking
            
        Returns:
            Tuple of (nodes_created, nodes_updated)
        """
        ...
    
    async def sync_relationships(
        self,
        relationships: list["ScipRelationship"],
    ) -> int:
        """Sync SCIP relationships to Code Graph edges.
        
        Transforms each relationship to a GraphEdge and performs upsert.
        
        Args:
            relationships: List of SCIP relationships to sync
            
        Returns:
            Number of edges created
        """
        ...
    
    async def prune_stale_nodes(
        self,
        package: str,
        current_arns: list[str],
    ) -> int:
        """Remove nodes not in the current SCIP parse result.
        
        Args:
            package: Package name
            current_arns: List of ARNs from the current SCIP parse
            
        Returns:
            Number of nodes pruned
        """
        ...
    
    def transform_symbol_to_node(
        self,
        symbol: "ScipSymbol",
        index_hash: str,
    ) -> "GraphNode":
        """Transform a SCIP symbol to a Code Graph node.
        
        Mapping:
        - arn → arn (primary key)
        - type → type (code, doc, k8s, infra)
        - workspace → workspace
        - package → package
        - path → path
        - symbol → symbol
        - kind → kind (function, class, method, variable, type, module)
        - name → name
        - signature → signature
        - documentation → documentation
        - location.file → file_path
        - location.line → line_number
        - hash → index_hash
        
        Args:
            symbol: SCIP symbol to transform
            index_hash: Hash of the SCIP index
            
        Returns:
            GraphNode instance
        """
        ...
    
    def transform_relationship_to_edge(
        self,
        relationship: "ScipRelationship",
    ) -> "GraphEdge":
        """Transform a SCIP relationship to a Code Graph edge.
        
        Mapping:
        - from_arn → from_arn (foreign key)
        - to_arn → to_arn (foreign key)
        - type → type (contains, references, implements, extends, imports, documents)
        
        Args:
            relationship: SCIP relationship to transform
            
        Returns:
            GraphEdge instance
        """
        ...
```


### Vector Sync Adapter

Chunks, embeds, and syncs Archon documentation to the Vector Store.

```python
from dataclasses import dataclass


@dataclass
class VectorSyncResult:
    """Result of a vector sync operation."""
    chunks_upserted: int
    chunks_pruned: int
    errors: list[str]


class VectorSyncAdapter:
    """Adapter for syncing Archon docs to the Vector Store."""
    
    def __init__(
        self,
        vector_store: "VectorStoreService",
        embedding_client: "EmbeddingServiceClient",
        chunk_size: int = 600,  # Target tokens per chunk (400-800 range)
        chunk_overlap: int = 100,  # Overlap tokens between chunks
    ) -> None:
        """Initialize the vector sync adapter.
        
        Args:
            vector_store: Vector store service instance
            embedding_client: Embedding service client
            chunk_size: Target size for chunks in tokens
            chunk_overlap: Overlap between consecutive chunks
        """
        ...
    
    async def sync_docs(
        self,
        package: str,
        archon_docs: list["GeneratedDoc"],
    ) -> VectorSyncResult:
        """Sync Archon documentation to the Vector Store.
        
        1. Chunk each document into segments
        2. Generate embeddings for chunks
        3. Upsert chunks with ARN metadata
        4. Prune stale chunks
        
        Args:
            package: Package name
            archon_docs: List of generated Archon documentation
            
        Returns:
            VectorSyncResult with counts and errors
        """
        ...
    
    def chunk_document(
        self,
        doc: "GeneratedDoc",
    ) -> list["ArchonChunk"]:
        """Chunk a document into segments suitable for embedding.
        
        Preserves ARN metadata in each chunk:
        - arn: ARN of the documented symbol/file
        - related_arns: ARNs referenced in the chunk content
        - symbol_name: Name of the symbol being documented
        - symbol_kind: Kind of symbol
        - package: Package name
        
        Args:
            doc: Generated Archon documentation
            
        Returns:
            List of ArchonChunk instances
        """
        ...
    
    async def prune_stale_chunks(
        self,
        package: str,
        current_arns: list[str],
    ) -> int:
        """Remove chunks not in the current Archon docs.
        
        Args:
            package: Package name
            current_arns: List of ARNs from the current Archon docs
            
        Returns:
            Number of chunks pruned
        """
        ...
    
    def generate_chunk_id(
        self,
        arn: str,
        chunk_index: int,
    ) -> str:
        """Generate a deterministic point ID for a chunk.
        
        Uses hash of (ARN + chunk_index) for idempotent upserts.
        
        Args:
            arn: ARN of the documented symbol/file
            chunk_index: Index of the chunk within the document
            
        Returns:
            Deterministic point ID string
        """
        ...
```


### Change Detector

Hash-based change detection for efficient synchronization.

```python
from dataclasses import dataclass
from typing import Optional


@dataclass
class HashState:
    """Stored hash state for a package."""
    package: str
    index_hash: str
    last_synced: str  # ISO timestamp


class ChangeDetector:
    """Service for hash-based change detection."""
    
    def __init__(self, state_file: str = ".archon/sync-state.json") -> None:
        """Initialize the change detector.
        
        Args:
            state_file: Path to the state file for storing hashes
        """
        ...
    
    def has_changed(
        self,
        package: str,
        current_hash: str,
    ) -> bool:
        """Check if a package's SCIP index has changed.
        
        Args:
            package: Package name
            current_hash: Hash of the current SCIP index
            
        Returns:
            True if hash differs from stored hash, False otherwise
        """
        ...
    
    def get_stored_hash(self, package: str) -> Optional[str]:
        """Get the stored hash for a package.
        
        Args:
            package: Package name
            
        Returns:
            Stored hash or None if not found
        """
        ...
    
    def update_hash(
        self,
        package: str,
        new_hash: str,
    ) -> None:
        """Update the stored hash for a package.
        
        Args:
            package: Package name
            new_hash: New hash to store
        """
        ...
    
    def load_state(self) -> dict[str, HashState]:
        """Load hash state from the state file.
        
        Returns:
            Dictionary of package name to HashState
        """
        ...
    
    def save_state(self, state: dict[str, HashState]) -> None:
        """Save hash state to the state file.
        
        Args:
            state: Dictionary of package name to HashState
        """
        ...
```


## Data Models

### Input Data Models

These models represent the input data from ArchonDocumentationMCPTools.

```python
from dataclasses import dataclass
from typing import Optional


@dataclass
class ScipSymbol:
    """Symbol extracted from a SCIP index."""
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
    location: "SymbolLocation"


@dataclass
class SymbolLocation:
    """Location of a symbol in source code."""
    file: str
    line: int
    column: int


@dataclass
class ScipRelationship:
    """Relationship between symbols from a SCIP index."""
    from_arn: str
    to_arn: str
    type: str  # contains, references, implements, extends, imports


@dataclass
class ScipParseResult:
    """Result of parsing a SCIP index."""
    symbols: list[ScipSymbol]
    relationships: list[ScipRelationship]
    hash: str  # SHA-256 hash of the SCIP index content


@dataclass
class GeneratedDoc:
    """Generated Archon documentation file."""
    source_path: str  # Path to the source file being documented
    doc_path: str     # Path to the generated .archon.md file
    content: str      # Documentation content
    arn: str          # ARN of the documented symbol/file
    referenced_arns: list[str]  # ARNs referenced in the documentation
```


### Sync State Storage

The sync service stores hash state for change detection.

**State File Location:** `<workspace>/.archon/sync-state.json`

**State File Schema:**
```json
{
  "packages": {
    "ArchonDocumentationMCPTools": {
      "index_hash": "abc123...",
      "last_synced": "2024-01-15T10:30:00Z"
    },
    "ArchonKnowledgeBaseInfrastructure": {
      "index_hash": "def456...",
      "last_synced": "2024-01-15T10:35:00Z"
    }
  }
}
```

### MCP Tool Interface

The sync service is exposed via an MCP tool in ArchonDocumentationMCPTools.

```typescript
interface SyncToKnowledgeBaseInput {
  workspacePath: string;
  packages?: string[];      // Optional: specific packages to sync
  force?: boolean;          // Force full sync (bypass change detection)
}

interface SyncToKnowledgeBaseOutput {
  success: boolean;
  packagesSynced: {
    package: string;
    nodesCreated: number;
    nodesUpdated: number;
    chunksUpserted: number;
  }[];
  packagesSkipped: string[];
  errors: {
    package: string;
    error: string;
  }[];
}
```

### Kiro Hook Configuration

The sync service is triggered automatically via a Kiro hook.

**Hook File:** `.kiro/hooks/archon-knowledge-base-sync.json`

```json
{
  "name": "Archon Knowledge Base Sync",
  "version": "1.0.0",
  "description": "Sync code intelligence to knowledge base after documentation generation",
  "when": {
    "type": "agentStop"
  },
  "then": {
    "type": "askAgent",
    "prompt": "If documentation was generated, sync the updated packages to the knowledge base using the sync_to_knowledge_base tool."
  }
}
```


## Correctness Properties

*Properties define characteristics that should hold true across all valid executions. These bridge human-readable specifications and machine-verifiable correctness guarantees.*

### Property 27: Sync Idempotency

*For any* package, running the sync service multiple times with the same SCIP result and Archon docs SHALL produce identical Code Graph and Vector Store state. Specifically:
- The set of nodes after N syncs SHALL equal the set after 1 sync
- The set of edges after N syncs SHALL equal the set after 1 sync
- The set of chunks after N syncs SHALL equal the set after 1 sync
- No duplicate entries SHALL be created

**Validates:** Requirement 8 (Sync Idempotency)

**Test Strategy:**
- Generate random SCIP parse results and Archon docs
- Run sync_package multiple times (N=1, 2, 5)
- After each sync, capture the state of:
  - All nodes in the Code Graph for the package
  - All edges in the Code Graph for the package
  - All chunks in the Vector Store for the package
- Verify that state after sync N equals state after sync 1
- Verify no duplicate ARNs in nodes
- Verify no duplicate (from_arn, to_arn, type) in edges
- Verify no duplicate (arn, chunk_index) in chunks

**Implementation Requirements:**
- Use ARN as primary key for nodes (upsert on conflict)
- Use (from_arn, to_arn, type) as unique constraint for edges (upsert on conflict)
- Use deterministic point ID (hash of ARN + chunk_index) for chunks

### Property 28: Sync Change Detection

*For any* package where the SCIP index hash has not changed since the last sync, the sync service SHALL skip synchronization unless `force=true`. Specifically:
- WHEN hash matches stored hash AND force=false THEN sync SHALL be skipped
- WHEN hash matches stored hash AND force=true THEN sync SHALL proceed
- WHEN hash differs from stored hash THEN sync SHALL proceed regardless of force parameter

**Validates:** Requirement 7 (Change Detection)

**Test Strategy:**
- Generate a SCIP parse result with a known hash
- Run sync_package to establish baseline state
- Run sync_package again with same hash and force=false
  - Verify sync was skipped (skipped=true in result)
  - Verify no database operations occurred
- Run sync_package again with same hash and force=true
  - Verify sync proceeded (skipped=false in result)
  - Verify database operations occurred
- Modify the SCIP parse result (different hash)
- Run sync_package with force=false
  - Verify sync proceeded (skipped=false in result)
  - Verify database operations occurred

**Implementation Requirements:**
- Store index hash per package in state file
- Compare current hash with stored hash before sync
- Respect force parameter to bypass change detection
- Update stored hash after successful sync


## Error Handling

### Input Validation Errors

| Error Condition | Response | Recovery |
|-----------------|----------|----------|
| Invalid SCIP parse result | Return validation error with details | Fix SCIP parsing |
| Invalid Archon doc format | Return validation error with details | Fix doc generation |
| Empty package path | Return validation error | Provide valid path |
| Invalid ARN format in input | Log warning, skip invalid entry | Fix ARN generation |

### Code Graph Errors

| Error Condition | Response | Recovery |
|-----------------|----------|----------|
| Database connection failure | Return service unavailable | Retry with backoff |
| Foreign key violation | Log error, skip edge | Ensure nodes exist first |
| Unique constraint violation | Use upsert behavior | No action needed |
| Transaction rollback | Return error, no changes persisted | Retry entire sync |

### Vector Store Errors

| Error Condition | Response | Recovery |
|-----------------|----------|----------|
| Embedding service unavailable | Return service unavailable | Retry with backoff |
| Qdrant connection failure | Return service unavailable | Retry with backoff |
| Invalid chunk content | Log warning, skip chunk | Fix chunking logic |
| Batch embedding timeout | Reduce batch size, retry | Adjust batch configuration |

### Sync Orchestration Errors

| Error Condition | Response | Recovery |
|-----------------|----------|----------|
| Partial sync failure | Return partial success with errors | Retry failed packages |
| Hash storage failure | Log warning, continue sync | Manual hash reset |
| Pruning failure | Log error, continue | Manual cleanup |
| Workspace discovery failure | Return error with path | Verify workspace structure |

### Retry Strategy

For transient failures, implement exponential backoff:

```python
async def retry_with_backoff(
    operation: Callable,
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
) -> Any:
    """Retry an operation with exponential backoff.
    
    Args:
        operation: Async callable to retry
        max_retries: Maximum number of retry attempts
        base_delay: Initial delay in seconds
        max_delay: Maximum delay in seconds
        
    Returns:
        Result of the operation
        
    Raises:
        Last exception if all retries fail
    """
    for attempt in range(max_retries + 1):
        try:
            return await operation()
        except (ConnectionError, TimeoutError) as e:
            if attempt == max_retries:
                raise
            delay = min(base_delay * (2 ** attempt), max_delay)
            await asyncio.sleep(delay)
```


## Testing Strategy

### Property-Based Testing

Use `hypothesis` (Python) for property-based tests:

- **Library**: hypothesis
- **Minimum iterations**: 100 per property test
- **Tag format**: `Feature: sync-service, Property N: <property_text>`

### Test Categories

#### Sync Service Tests

**Property Tests:**
- Property 27: Sync idempotency
- Property 28: Sync change detection

**Unit Tests:**
- sync_package with valid inputs
- sync_package with invalid inputs
- sync_workspace with multiple packages
- sync_workspace with force=true
- Partial failure handling

#### Graph Sync Adapter Tests

**Unit Tests:**
- Symbol to node transformation
- Relationship to edge transformation
- Batch upsert operations
- Stale node pruning

#### Vector Sync Adapter Tests

**Unit Tests:**
- Document chunking
- Chunk ID generation (determinism)
- Batch embedding and upsert
- Stale chunk pruning

#### Change Detector Tests

**Unit Tests:**
- Hash comparison
- State file loading/saving
- Hash update after sync
- Missing state file handling

### Test Data Generation

For property-based tests, generate:

**SCIP Parse Results:**
```python
from hypothesis import strategies as st

@st.composite
def scip_symbol(draw):
    return ScipSymbol(
        arn=draw(valid_arn()),
        type=draw(st.sampled_from(["code", "doc", "k8s", "infra"])),
        workspace=draw(st.text(min_size=1, max_size=50)),
        package=draw(st.text(min_size=1, max_size=100)),
        path=draw(st.text(min_size=1, max_size=200)),
        symbol=draw(st.one_of(st.none(), st.text(min_size=1, max_size=100))),
        kind=draw(st.sampled_from(["function", "class", "method", "variable", "type", "module"])),
        name=draw(st.text(min_size=1, max_size=100)),
        signature=draw(st.one_of(st.none(), st.text(min_size=1, max_size=500))),
        documentation=draw(st.one_of(st.none(), st.text(min_size=1, max_size=1000))),
        location=draw(symbol_location()),
    )

@st.composite
def scip_parse_result(draw):
    symbols = draw(st.lists(scip_symbol(), min_size=1, max_size=50))
    relationships = draw(st.lists(scip_relationship(symbols), max_size=100))
    return ScipParseResult(
        symbols=symbols,
        relationships=relationships,
        hash=draw(st.text(min_size=64, max_size=64, alphabet="0123456789abcdef")),
    )
```

**Generated Docs:**
```python
@st.composite
def generated_doc(draw):
    return GeneratedDoc(
        source_path=draw(st.text(min_size=1, max_size=200)),
        doc_path=draw(st.text(min_size=1, max_size=200)),
        content=draw(st.text(min_size=100, max_size=5000)),
        arn=draw(valid_arn()),
        referenced_arns=draw(st.lists(valid_arn(), max_size=20)),
    )
```

### Integration Tests

- End-to-end sync flow with real PostgreSQL and Qdrant
- Concurrent sync operations
- Large-scale sync (many symbols, many docs)
- Recovery from partial failures


## Implementation Notes

### Transaction Management

Use database transactions for atomic sync operations:

```python
async def sync_package_atomic(
    self,
    package_path: str,
    scip_result: ScipParseResult,
    archon_docs: list[GeneratedDoc],
) -> SyncResult:
    """Sync a package atomically using a database transaction."""
    async with self.db.transaction():
        # All graph operations within transaction
        nodes_created, nodes_updated = await self.graph_adapter.sync_symbols(
            package=package_path,
            symbols=scip_result.symbols,
            index_hash=scip_result.hash,
        )
        edges_created = await self.graph_adapter.sync_relationships(
            relationships=scip_result.relationships,
        )
        nodes_pruned = await self.graph_adapter.prune_stale_nodes(
            package=package_path,
            current_arns=[s.arn for s in scip_result.symbols],
        )
    
    # Vector operations (separate, as Qdrant doesn't support transactions)
    vector_result = await self.vector_adapter.sync_docs(
        package=package_path,
        archon_docs=archon_docs,
    )
    
    return SyncResult(
        success=True,
        nodes_created=nodes_created,
        nodes_updated=nodes_updated,
        edges_created=edges_created,
        chunks_upserted=vector_result.chunks_upserted,
        nodes_pruned=nodes_pruned,
        chunks_pruned=vector_result.chunks_pruned,
        skipped=False,
        skip_reason=None,
        errors=vector_result.errors,
    )
```

### Batch Operations

Use batch operations for efficiency:

```python
# Batch upsert nodes (up to 100 per batch)
BATCH_SIZE = 100

async def batch_upsert_nodes(
    self,
    nodes: list[GraphNode],
) -> tuple[int, int]:
    """Batch upsert nodes for efficiency."""
    created = 0
    updated = 0
    for i in range(0, len(nodes), BATCH_SIZE):
        batch = nodes[i:i + BATCH_SIZE]
        batch_created, batch_updated = await self.repository.upsert_nodes(batch)
        created += batch_created
        updated += batch_updated
    return created, updated
```

### Parallel Processing

For workspace sync, process packages in parallel:

```python
async def sync_workspace(
    self,
    workspace_path: str,
    force: bool = False,
) -> WorkspaceSyncResult:
    """Sync all packages in parallel."""
    packages = await self.discover_packages(workspace_path)
    
    # Sync packages concurrently (with semaphore to limit parallelism)
    semaphore = asyncio.Semaphore(4)  # Max 4 concurrent syncs
    
    async def sync_with_semaphore(package: str) -> PackageSyncResult:
        async with semaphore:
            return await self.sync_single_package(package, force)
    
    results = await asyncio.gather(
        *[sync_with_semaphore(p) for p in packages],
        return_exceptions=True,
    )
    
    return self.aggregate_results(results)
```

### Logging

Implement structured logging for debugging:

```python
import structlog

logger = structlog.get_logger()

async def sync_package(self, ...):
    logger.info(
        "sync_package_started",
        package=package_path,
        symbol_count=len(scip_result.symbols),
        doc_count=len(archon_docs),
    )
    
    # ... sync operations ...
    
    logger.info(
        "sync_package_completed",
        package=package_path,
        nodes_created=result.nodes_created,
        nodes_updated=result.nodes_updated,
        chunks_upserted=result.chunks_upserted,
        skipped=result.skipped,
    )
```

### Performance Considerations

- Use connection pooling for PostgreSQL (e.g., `asyncpg` pool)
- Batch embedding requests (32 texts per batch)
- Use Qdrant batch upsert (up to 100 points per batch)
- Consider caching frequently accessed nodes
- Use database indexes for efficient pruning queries
- Implement progress reporting for long-running workspace syncs
