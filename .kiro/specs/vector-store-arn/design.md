# Design Document: Vector Store with ARN Metadata

## Overview

The Vector Store with ARN Metadata component extends the existing Qdrant vector store to include ARN (Archon Resource Name) metadata for graph traversal. This enables semantic search results to be linked to the Code Graph, allowing agents to navigate from documentation chunks to related code symbols and vice versa.

**This is a child spec** implementing Requirement 11 (Vector Store with ARN Metadata) from the root Archon Agent Pipeline specification.

### Parent Specification Reference

- **Root Spec Location:** `.kiro/specs/archon-agent-pipeline/`
- **Root Spec Repository:** Workspace root (personal-work)
- **Related Requirements:** Requirement 11 (Vector Store with ARN Metadata)
- **Related Correctness Properties:** Properties 25-26

### Design Principles

1. **ARN-Enriched Chunks**: Every chunk includes ARN metadata linking to the Code Graph
2. **Graph Traversal Ready**: Search results include `related_arns` for navigation to referenced symbols
3. **Efficient Filtering**: Qdrant payload filters enable package and symbol kind filtering
4. **Embedding Service Integration**: Chunks are embedded via the existing embedding service
5. **Sync-Friendly**: Bulk upsert operations for efficient synchronization from Archon docs


## Architecture

### System Context

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                     ArchonKnowledgeBaseInfrastructure                        │
│                                                                              │
│  ┌──────────────────────────────────────────────────────────────────────┐  │
│  │                    Vector Store Module                                │  │
│  │                                                                       │  │
│  │  ┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐  │  │
│  │  │  Search API     │───▶│   Vector Store  │───▶│   Qdrant        │  │  │
│  │  │  (Python)       │    │   Service       │    │   (Storage)     │  │  │
│  │  └─────────────────┘    └─────────────────┘    └─────────────────┘  │  │
│  │                                │                                     │  │
│  │                                ▼                                     │  │
│  │                         ┌─────────────────┐                         │  │
│  │                         │  Embedding      │                         │  │
│  │                         │  Service        │                         │  │
│  │                         └─────────────────┘                         │  │
│  └──────────────────────────────────────────────────────────────────────┘  │
│                                    ▲                                        │
│                                    │                                        │
│  ┌─────────────────────────────────┼────────────────────────────────────┐  │
│  │                    Sync Service │                                     │  │
│  │  ┌─────────────────┐           │                                     │  │
│  │  │ Vector Sync     │───────────┘                                     │  │
│  │  │ Adapter         │                                                 │  │
│  │  │ - chunk docs    │                                                 │  │
│  │  │ - embed         │                                                 │  │
│  │  │ - upsert        │                                                 │  │
│  │  └─────────────────┘                                                 │  │
│  └──────────────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────────┘
                                    ▲
                                    │
┌───────────────────────────────────┼─────────────────────────────────────────┐
│                    External Consumers                                        │
│                                                                              │
│  ┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐         │
│  │ ArchonMCPServer │    │ ArchonDocTools  │    │ Kiro Agent      │         │
│  │ (archon.search) │    │ (sync trigger)  │    │ (queries)       │         │
│  └─────────────────┘    └─────────────────┘    └─────────────────┘         │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Package Structure

```
ArchonKnowledgeBaseInfrastructure/
├── src/
│   ├── vector/                     # Enhanced vector store module
│   │   ├── __init__.py
│   │   ├── models.py               # ArchonChunk, SearchResult dataclasses
│   │   ├── store.py                # Vector store service with ARN support
│   │   └── filters.py              # Qdrant filter builders
│   ├── sync/
│   │   └── vector_adapter.py       # Sync adapter for vector operations
│   └── common/
│       └── embedding.py            # Embedding service client
└── .kiro/
    └── specs/
        └── vector-store-arn/       # This spec
```


## Component Interfaces

### ArchonChunk Dataclass

The enhanced chunk model with ARN metadata for graph integration.

```python
from dataclasses import dataclass


@dataclass
class ArchonChunk:
    """Document chunk with ARN metadata for graph traversal."""
    
    content: str              # Chunk text content
    source: str               # File path (e.g., "ArchonAgent/src/orchestrator/main.py")
    chunk_index: int          # Index of chunk within the source file
    
    # ARN metadata for graph integration
    arn: str                  # ARN of the documented symbol/file
    related_arns: list[str]   # ARNs referenced in this chunk
    
    # Additional context
    symbol_name: str | None   # Name of the symbol being documented
    symbol_kind: str | None   # Kind of symbol (function, class, etc.)
    package: str              # Package name
```

### SearchResult Dataclass

The enhanced search result with ARN metadata for graph traversal.

```python
@dataclass
class SearchResult:
    """Search result with ARN metadata for graph traversal."""
    
    content: str              # Chunk text content
    source: str               # File path
    chunk_index: int          # Index of chunk within the source file
    score: float              # Similarity score (0.0 to 1.0)
    
    # ARN metadata
    arn: str                  # ARN of the documented symbol/file
    related_arns: list[str]   # ARNs referenced in this chunk
    symbol_name: str | None   # Name of the symbol
    symbol_kind: str | None   # Kind of symbol
    package: str              # Package name
```


### Vector Store Service

The main service for vector store operations with ARN-enriched chunks.

```python
from typing import Optional


class VectorStoreService:
    """Service for vector store operations with ARN metadata."""
    
    def __init__(
        self,
        qdrant_url: str,
        collection_name: str = "archon-docs",
        embedding_service_url: str | None = None,
    ) -> None:
        """Initialize the vector store service.
        
        Args:
            qdrant_url: URL of the Qdrant server
            collection_name: Name of the Qdrant collection
            embedding_service_url: URL of the embedding service (optional)
        """
        ...
    
    async def upsert(self, chunks: list[ArchonChunk]) -> int:
        """Upsert chunks to the vector store.
        
        Generates embeddings for each chunk and stores them with
        the full ARN metadata payload.
        
        Args:
            chunks: List of ArchonChunk instances to upsert
            
        Returns:
            Number of chunks upserted
        """
        ...
    
    async def search(
        self,
        query: str,
        limit: int = 10,
        package: str | None = None,
        symbol_kind: str | None = None,
    ) -> list[SearchResult]:
        """Search for chunks matching the query.
        
        Args:
            query: Search query text
            limit: Maximum number of results to return
            package: Optional package filter
            symbol_kind: Optional symbol kind filter
            
        Returns:
            List of SearchResult instances ordered by score (highest first)
        """
        ...
    
    async def delete_by_package(self, package: str) -> int:
        """Delete all chunks for a package.
        
        Args:
            package: Package name to delete chunks for
            
        Returns:
            Number of chunks deleted
        """
        ...
    
    async def delete_by_arn(self, arn: str) -> int:
        """Delete all chunks with a specific ARN.
        
        Args:
            arn: ARN to delete chunks for
            
        Returns:
            Number of chunks deleted
        """
        ...
```

### Embedding Service Client

Client for generating embeddings via the embedding service.

```python
class EmbeddingServiceClient:
    """Client for the embedding service."""
    
    def __init__(self, service_url: str) -> None:
        """Initialize the embedding service client.
        
        Args:
            service_url: URL of the embedding service
        """
        ...
    
    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Generate embeddings for a list of texts.
        
        Args:
            texts: List of text strings to embed
            
        Returns:
            List of embedding vectors (one per input text)
        """
        ...
    
    async def embed_single(self, text: str) -> list[float]:
        """Generate embedding for a single text.
        
        Args:
            text: Text string to embed
            
        Returns:
            Embedding vector
        """
        ...
```


### Vector Sync Adapter

Adapter for synchronizing Archon documentation to the vector store.

```python
@dataclass
class VectorSyncResult:
    """Result of a vector sync operation."""
    chunks_upserted: int
    chunks_deleted: int
    errors: list[str]


class VectorSyncAdapter:
    """Adapter for syncing Archon docs to the vector store."""
    
    def __init__(self, vector_store: VectorStoreService) -> None:
        """Initialize the sync adapter.
        
        Args:
            vector_store: Vector store service instance
        """
        ...
    
    async def sync_package(
        self,
        package: str,
        archon_docs: list[ArchonChunk],
    ) -> VectorSyncResult:
        """Sync Archon documentation for a package.
        
        1. Upsert new/updated chunks
        2. Delete stale chunks not in the new set
        
        Args:
            package: Package name
            archon_docs: List of ArchonChunk instances for the package
            
        Returns:
            VectorSyncResult with counts and any errors
        """
        ...
    
    async def delete_package(self, package: str) -> int:
        """Delete all chunks for a package.
        
        Args:
            package: Package name to delete
            
        Returns:
            Number of chunks deleted
        """
        ...
```


## Data Models

### Qdrant Payload Schema

The Qdrant payload stores all metadata alongside the embedding vector.

```json
{
  "content": "This function generates SCIP indexes for a package...",
  "source": "ArchonDocumentationMCPTools/src/tools/scip_indexing.archon.md",
  "chunk_index": 0,
  "arn": "arn:archon:doc:workspace/ArchonDocumentationMCPTools/src/tools/scip_indexing.ts",
  "related_arns": [
    "arn:archon:code:workspace/ArchonDocumentationMCPTools/src/lib/language_detector.ts#detect",
    "arn:archon:code:workspace/ArchonDocumentationMCPTools/src/lib/scip_parser.ts#parse"
  ],
  "symbol_name": "generateScipIndex",
  "symbol_kind": "function",
  "package": "ArchonDocumentationMCPTools"
}
```

**Payload Fields:**

| Field | Type | Description | Required |
|-------|------|-------------|----------|
| `content` | string | Chunk text content | Yes |
| `source` | string | File path relative to workspace | Yes |
| `chunk_index` | integer | Index of chunk within source file | Yes |
| `arn` | string | ARN of the documented symbol/file | Yes |
| `related_arns` | array[string] | ARNs referenced in this chunk | Yes (can be empty) |
| `symbol_name` | string | Name of the symbol being documented | No |
| `symbol_kind` | string | Kind of symbol (function, class, etc.) | No |
| `package` | string | Package name for filtering | Yes |


### ARN Format Reference

ARNs (Archon Resource Names) follow this format:

```
arn:archon:<type>:<workspace>/<package>/<path>#<symbol>
```

| Component | Description | Example |
|-----------|-------------|---------|
| `type` | Resource type | `code`, `doc`, `k8s`, `infra` |
| `workspace` | Workspace identifier | `personal-work` |
| `package` | Package/repository name | `ArchonDocumentationMCPTools` |
| `path` | File path relative to package | `src/tools/scip_indexing.ts` |
| `symbol` | Symbol name (optional) | `generateScipIndex` |

**Examples:**
```
arn:archon:doc:personal-work/ArchonDocumentationMCPTools/src/tools/scip_indexing.archon.md
arn:archon:code:personal-work/ArchonAgent/src/orchestrator/main.py#Orchestrator
arn:archon:code:personal-work/AphexCLI/cmd/root.go#Execute
```

### Symbol Kind Values

Valid values for the `symbol_kind` field:

| Value | Description |
|-------|-------------|
| `function` | Standalone function |
| `class` | Class definition |
| `method` | Method within a class |
| `variable` | Variable or constant |
| `type` | Type definition or interface |
| `module` | Module or namespace |

### Qdrant Collection Configuration

The `archon-docs` collection should be configured with:

```python
from qdrant_client.models import Distance, VectorParams

collection_config = {
    "vectors_config": VectorParams(
        size=768,  # BGE-base embedding dimension
        distance=Distance.COSINE,
    ),
    "on_disk_payload": True,  # Store payloads on disk for large collections
}
```

### Qdrant Filter Examples

**Package Filter:**
```python
from qdrant_client.models import Filter, FieldCondition, MatchValue

package_filter = Filter(
    must=[
        FieldCondition(
            key="package",
            match=MatchValue(value="ArchonDocumentationMCPTools"),
        )
    ]
)
```

**Symbol Kind Filter:**
```python
symbol_kind_filter = Filter(
    must=[
        FieldCondition(
            key="symbol_kind",
            match=MatchValue(value="function"),
        )
    ]
)
```

**Combined Filter:**
```python
combined_filter = Filter(
    must=[
        FieldCondition(
            key="package",
            match=MatchValue(value="ArchonDocumentationMCPTools"),
        ),
        FieldCondition(
            key="symbol_kind",
            match=MatchValue(value="function"),
        ),
    ]
)
```


## Correctness Properties

*Properties define characteristics that should hold true across all valid executions. These bridge human-readable specifications and machine-verifiable correctness guarantees.*

### Property 25: Vector Chunk ARN Metadata

*For any* chunk stored in the Vector Store, the chunk SHALL have a non-empty `arn` field conforming to the ARN format, and the `related_arns` field SHALL be a list (possibly empty) of valid ARN strings.

**Validates:** Requirements 1.1, 1.2, 4.1, 4.2

**Test Strategy:**
- Generate random ArchonChunk instances with various ARN formats
- Upsert chunks to the vector store
- Retrieve chunks and verify:
  - `arn` field is non-empty and matches ARN format regex
  - `related_arns` is a list (may be empty)
  - Each item in `related_arns` matches ARN format regex
- Test edge cases: empty `related_arns`, many `related_arns`, special characters in ARNs

### Property 26: ARN-Enriched Search Results

*For any* search query returning results, each result SHALL include the `arn`, `related_arns`, `symbol_name`, `symbol_kind`, and `package` metadata fields alongside `content`, `source`, and `score`.

**Validates:** Requirements 3.1, 3.2

**Test Strategy:**
- Insert chunks with various metadata combinations
- Execute search queries
- Verify each SearchResult contains:
  - `content`: non-empty string
  - `source`: non-empty string
  - `score`: float between 0.0 and 1.0
  - `arn`: non-empty string matching ARN format
  - `related_arns`: list of strings (may be empty)
  - `symbol_name`: string or None
  - `symbol_kind`: string or None (if present, must be valid kind)
  - `package`: non-empty string
- Test with filters (package, symbol_kind) to ensure metadata is preserved


## Error Handling

### Embedding Errors

| Error Condition | Response | Recovery |
|-----------------|----------|----------|
| Embedding service unavailable | Return service unavailable error (503) | Retry with exponential backoff |
| Invalid chunk content (empty) | Return validation error (400) | Provide non-empty content |
| Embedding dimension mismatch | Return configuration error (500) | Verify embedding model configuration |
| Batch embedding timeout | Return timeout error (504) | Reduce batch size and retry |

### Search Errors

| Error Condition | Response | Recovery |
|-----------------|----------|----------|
| Invalid symbol_kind value | Return validation error (400) | Use valid symbol kind from enum |
| Collection not found | Return configuration error (500) | Initialize collection first |
| Query embedding fails | Return service unavailable (503) | Retry with backoff |
| Empty query string | Return validation error (400) | Provide non-empty query |

### ARN Validation Errors

| Error Condition | Response | Recovery |
|-----------------|----------|----------|
| Invalid ARN format | Return validation error (400) | Fix ARN format |
| Empty ARN field | Return validation error (400) | Provide valid ARN |
| Invalid related_arns (not a list) | Return validation error (400) | Provide list of ARN strings |

### Qdrant Errors

| Error Condition | Response | Recovery |
|-----------------|----------|----------|
| Connection refused | Return service unavailable (503) | Check Qdrant server status |
| Collection does not exist | Return configuration error (500) | Create collection first |
| Payload too large | Return validation error (400) | Reduce chunk size |
| Rate limit exceeded | Return rate limit error (429) | Implement backoff and retry |


## Testing Strategy

### Property-Based Testing

Use `hypothesis` (Python) for property-based tests:

- **Library**: hypothesis
- **Minimum iterations**: 100 per property test
- **Tag format**: `Feature: vector-store-arn, Property N: <property_text>`

### Test Categories

#### Data Model Tests

**Property Tests:**
- Property 25: ARN metadata validation on chunks

**Unit Tests:**
- ArchonChunk dataclass instantiation
- SearchResult dataclass instantiation
- ARN format validation
- Symbol kind validation

#### Vector Store Service Tests

**Property Tests:**
- Property 26: Search result completeness

**Unit Tests:**
- Upsert single chunk
- Upsert batch of chunks
- Search with no filters
- Search with package filter
- Search with symbol_kind filter
- Search with combined filters
- Delete by package
- Delete by ARN

#### Integration Tests

- End-to-end upsert and search flow
- Embedding service integration
- Qdrant connection handling
- Concurrent access handling

### Test Data Generation

For property-based tests, generate:

**ARN Components:**
```python
from hypothesis import strategies as st

arn_type = st.sampled_from(["code", "doc", "k8s", "infra"])
workspace = st.text(min_size=1, max_size=50, alphabet=st.characters(whitelist_categories=("L", "N"), whitelist_characters="-_"))
package = st.text(min_size=1, max_size=100, alphabet=st.characters(whitelist_categories=("L", "N"), whitelist_characters="-_"))
path = st.text(min_size=1, max_size=200, alphabet=st.characters(whitelist_categories=("L", "N"), whitelist_characters="-_./"))
symbol = st.text(min_size=0, max_size=100, alphabet=st.characters(whitelist_categories=("L", "N"), whitelist_characters="-_."))
```

**ArchonChunk Generation:**
```python
@st.composite
def archon_chunk(draw):
    return ArchonChunk(
        content=draw(st.text(min_size=1, max_size=1000)),
        source=draw(st.text(min_size=1, max_size=200)),
        chunk_index=draw(st.integers(min_value=0, max_value=100)),
        arn=draw(valid_arn()),
        related_arns=draw(st.lists(valid_arn(), max_size=10)),
        symbol_name=draw(st.one_of(st.none(), st.text(min_size=1, max_size=100))),
        symbol_kind=draw(st.one_of(st.none(), st.sampled_from(["function", "class", "method", "variable", "type", "module"]))),
        package=draw(st.text(min_size=1, max_size=100)),
    )
```

### Mock Strategy

For unit tests that don't require real services:

- **Embedding Service**: Mock with deterministic embeddings (e.g., hash-based vectors)
- **Qdrant Client**: Use `qdrant-client` in-memory mode for fast tests
- **ARN Validation**: Use regex-based validation without Code Graph lookup

For integration tests:

- Use real Qdrant instance (Docker container)
- Use real embedding service or mock with consistent behavior
- Validate ARN format only (Code Graph validation is sync service responsibility)


## Implementation Notes

### Qdrant Client Usage

Use the official `qdrant-client` Python library:

```python
from qdrant_client import QdrantClient
from qdrant_client.models import PointStruct, Filter, FieldCondition, MatchValue

client = QdrantClient(url="http://localhost:6333")

# Upsert with payload
points = [
    PointStruct(
        id=str(uuid.uuid4()),
        vector=embedding,
        payload={
            "content": chunk.content,
            "source": chunk.source,
            "chunk_index": chunk.chunk_index,
            "arn": chunk.arn,
            "related_arns": chunk.related_arns,
            "symbol_name": chunk.symbol_name,
            "symbol_kind": chunk.symbol_kind,
            "package": chunk.package,
        },
    )
    for chunk, embedding in zip(chunks, embeddings)
]
client.upsert(collection_name="archon-docs", points=points)
```

### Embedding Service Integration

The embedding service should be called in batches for efficiency:

```python
async def embed_chunks(chunks: list[ArchonChunk]) -> list[list[float]]:
    texts = [chunk.content for chunk in chunks]
    # Batch in groups of 32 for optimal throughput
    batch_size = 32
    embeddings = []
    for i in range(0, len(texts), batch_size):
        batch = texts[i:i + batch_size]
        batch_embeddings = await embedding_client.embed(batch)
        embeddings.extend(batch_embeddings)
    return embeddings
```

### ARN Validation

Validate ARN format at chunk creation time:

```python
import re

ARN_PATTERN = re.compile(
    r"^arn:archon:(code|doc|k8s|infra):[^/]+/[^/]+/[^#]+(#.+)?$"
)

def validate_arn(arn: str) -> bool:
    """Validate ARN format."""
    return bool(ARN_PATTERN.match(arn))
```

### Performance Considerations

- Use batch upsert operations (up to 100 points per batch)
- Use async operations for embedding and Qdrant calls
- Consider connection pooling for high-throughput scenarios
- Use Qdrant's `on_disk_payload` for large collections
- Index `package` and `symbol_kind` fields for efficient filtering
