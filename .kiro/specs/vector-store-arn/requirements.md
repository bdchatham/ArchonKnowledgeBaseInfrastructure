# Requirements Document: Vector Store with ARN Metadata

## Introduction

This specification defines the Vector Store with ARN Metadata component for the Archon Knowledge Base Infrastructure. The Vector Store provides semantic search over code and documentation, with search results enriched with ARN metadata that enables graph traversal and cross-referencing.

**This is a child spec** created by the root Archon Agent Pipeline specification. It implements Requirement 11 (Vector Store with ARN Metadata) from the root spec, focusing on:
- Enhanced chunk metadata schema with ARN fields
- ARN-enriched search results for graph integration
- Filtering capabilities by package and symbol kind

### Parent Specification Reference

- **Root Spec Location:** `.kiro/specs/archon-agent-pipeline/`
- **Root Spec Repository:** Workspace root (personal-work)
- **Related Requirements:** Requirement 11 (Vector Store with ARN Metadata)
- **Related Correctness Properties:** Properties 25-26

### Integration Context

The Vector Store with ARN Metadata integrates with:
- **Code Graph**: Chunks reference ARNs that resolve to Code Graph nodes
- **ArchonDocumentationMCPTools**: Provides Archon documentation for chunking and embedding
- **ArchonMCPServer**: Exposes `archon.search` tool that returns ARN-enriched results
- **Sync Service**: Orchestrates chunking, embedding, and upserting to the vector store

## Glossary

- **Vector_Store**: Embedding-based storage for semantic search (Qdrant)
- **Chunk**: A segment of documentation text with associated metadata and embedding
- **Embedding**: Dense vector representation of text for similarity search
- **ARN**: Archon Resource Name - deterministic identifier linking chunks to Code Graph nodes (format: `arn:archon:<type>:<workspace>/<package>/<path>#<symbol>`)
- **Related_ARNs**: List of ARNs referenced within a chunk's content
- **Symbol_Name**: Name of the code symbol being documented
- **Symbol_Kind**: Classification of a code symbol (function, class, method, variable, type, module)
- **Package**: Package/repository name for filtering
- **Payload**: Metadata stored alongside the embedding vector in Qdrant
- **Similarity_Search**: Finding chunks with embeddings closest to a query embedding
- **Archon_Doc**: Documentation file co-located with source code (`<name>.archon.md`)

## Requirements

### Requirement 1: Enhanced Chunk Metadata Schema

**User Story:** As a developer, I want documentation chunks to include ARN metadata, so that search results can be linked to the Code Graph for traversal.

#### Acceptance Criteria

1.1 THE Vector_Store chunks SHALL include an `arn` field (string) containing the ARN of the documented symbol or file.

1.2 THE Vector_Store chunks SHALL include a `related_arns` field (list of strings) containing ARNs referenced in the chunk content.

1.3 THE Vector_Store chunks SHALL include a `symbol_name` field (optional string) containing the name of the symbol being documented.

1.4 THE Vector_Store chunks SHALL include a `symbol_kind` field (optional string) containing the kind of symbol (function, class, method, variable, type, module).

1.5 THE Vector_Store chunks SHALL include a `package` field (string) containing the package name for filtering.

1.6 THE chunk metadata schema SHALL be implemented as a dataclass or equivalent structured type:
```python
@dataclass
class ArchonChunk:
    content: str           # Chunk text content
    source: str            # File path (e.g., "ArchonAgent/src/orchestrator/main.py")
    chunk_index: int       # Index of chunk within the source file
    arn: str               # ARN of the documented symbol/file
    related_arns: list[str]  # ARNs referenced in this chunk
    symbol_name: str | None  # Name of the symbol being documented
    symbol_kind: str | None  # Kind of symbol (function, class, etc.)
    package: str           # Package name
```

**Validates Root Spec:** Requirement 11.1

### Requirement 2: Vector Store Embedding Storage

**User Story:** As a knowledge base operator, I want Archon documentation chunks stored with embeddings and ARN metadata, so that semantic search returns graph-integrated results.

#### Acceptance Criteria

2.1 THE Vector_Store SHALL store embeddings for Archon documentation chunks with the enhanced metadata schema defined in Requirement 1.

2.2 THE Vector_Store payload (Qdrant) SHALL include all metadata fields:
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

2.3 THE Vector_Store upsert operation SHALL accept ArchonChunk instances and generate embeddings via the embedding service.

2.4 THE Vector_Store upsert operation SHALL store the embedding vector alongside the full metadata payload.

**Validates Root Spec:** Requirement 11.2

### Requirement 3: ARN-Enriched Search Results

**User Story:** As a developer, I want search results to include ARN metadata, so that I can traverse to related code and documentation.

#### Acceptance Criteria

3.1 THE Vector_Store search results SHALL return ARN metadata alongside content, source, and score.

3.2 THE search result type SHALL include all metadata fields:
```python
@dataclass
class SearchResult:
    content: str           # Chunk text content
    source: str            # File path
    score: float           # Similarity score
    arn: str               # ARN of the documented symbol/file
    related_arns: list[str]  # ARNs referenced in this chunk
    symbol_name: str | None  # Name of the symbol
    symbol_kind: str | None  # Kind of symbol
    package: str           # Package name
```

3.3 THE search results SHALL be ordered by similarity score (highest first).

3.4 THE search operation SHALL support a `limit` parameter to control the number of results returned.

**Validates Root Spec:** Requirement 11.3

### Requirement 4: ARN Validity Constraints

**User Story:** As a developer, I want chunk ARNs to be valid and resolvable, so that I can reliably traverse from search results to the Code Graph.

#### Acceptance Criteria

4.1 WHEN a chunk is retrieved THEN the `arn` field SHALL be a valid ARN that resolves to a Code_Graph node.

4.2 THE `arn` field SHALL conform to the ARN format: `arn:archon:<type>:<workspace>/<package>/<path>#<symbol>`.

4.3 THE `arn` field SHALL reference an existing node in the Code Graph (validated at sync time).

4.4 WHEN a chunk is retrieved THEN the `related_arns` field SHALL contain valid ARNs referenced in the chunk content.

4.5 THE `related_arns` field SHALL only contain ARNs that exist in the Code Graph (validated at sync time).

4.6 IF an ARN in `related_arns` cannot be resolved THEN it SHALL be omitted from the list during sync.

**Validates Root Spec:** Requirements 11.4, 11.5

### Requirement 5: Package Filtering

**User Story:** As a developer, I want to filter search results by package, so that I can focus on relevant code within a specific repository.

#### Acceptance Criteria

5.1 THE Vector_Store SHALL support filtering by `package` in similarity search.

5.2 THE search operation SHALL accept an optional `package` parameter (string).

5.3 WHEN `package` is specified THEN only chunks with matching `package` field SHALL be returned.

5.4 WHEN `package` is not specified THEN chunks from all packages SHALL be searched.

5.5 THE package filter SHALL be applied as a Qdrant filter condition before similarity ranking.

**Validates Root Spec:** Requirement 11.6

### Requirement 6: Symbol Kind Filtering

**User Story:** As a developer, I want to filter search results by symbol kind, so that I can find specific types of code elements (functions, classes, etc.).

#### Acceptance Criteria

6.1 THE Vector_Store SHALL support filtering by `symbol_kind` in similarity search.

6.2 THE search operation SHALL accept an optional `symbol_kind` parameter (string).

6.3 WHEN `symbol_kind` is specified THEN only chunks with matching `symbol_kind` field SHALL be returned.

6.4 WHEN `symbol_kind` is not specified THEN chunks of all symbol kinds SHALL be searched.

6.5 THE symbol_kind filter SHALL be applied as a Qdrant filter condition before similarity ranking.

6.6 THE `symbol_kind` parameter SHALL accept valid values: 'function', 'class', 'method', 'variable', 'type', 'module'.

**Validates Root Spec:** Requirement 11.7

### Requirement 7: Combined Filtering

**User Story:** As a developer, I want to combine multiple filters in a single search, so that I can precisely target the code I'm looking for.

#### Acceptance Criteria

7.1 THE search operation SHALL support combining `package` and `symbol_kind` filters.

7.2 WHEN both filters are specified THEN only chunks matching BOTH conditions SHALL be returned.

7.3 THE combined filter SHALL be applied as a Qdrant filter with AND logic.

7.4 THE search interface SHALL be:
```python
def search(
    query: str,
    limit: int = 10,
    package: str | None = None,
    symbol_kind: str | None = None
) -> list[SearchResult]:
    ...
```

**Validates Root Spec:** Requirements 11.6, 11.7

## Correctness Properties

*Properties define characteristics that should hold true across all valid executions. These bridge human-readable specifications and machine-verifiable correctness guarantees.*

### Property 25: Vector Chunk ARN Metadata

*For any* chunk stored in the Vector Store, the chunk SHALL have a non-empty `arn` field conforming to the ARN format, and the `related_arns` field SHALL be a list (possibly empty) of valid ARN strings.

**Validates:** Requirements 1.1, 1.2, 4.1, 4.2

### Property 26: ARN-Enriched Search Results

*For any* search query returning results, each result SHALL include the `arn`, `related_arns`, `symbol_name`, `symbol_kind`, and `package` metadata fields alongside `content`, `source`, and `score`.

**Validates:** Requirements 3.1, 3.2

## Error Handling

### Embedding Errors

| Error Condition | Response | Recovery |
|-----------------|----------|----------|
| Embedding service unavailable | Return service unavailable error | Retry with backoff |
| Invalid chunk content (empty) | Return validation error | Provide non-empty content |
| Embedding dimension mismatch | Return configuration error | Verify embedding model |

### Search Errors

| Error Condition | Response | Recovery |
|-----------------|----------|----------|
| Invalid symbol_kind value | Return validation error | Use valid symbol kind |
| Collection not found | Return configuration error | Initialize collection |
| Query embedding fails | Return service unavailable | Retry with backoff |

### ARN Validation Errors

| Error Condition | Response | Recovery |
|-----------------|----------|----------|
| Invalid ARN format | Log warning, skip chunk | Fix ARN generation |
| ARN not in Code Graph | Log warning, omit from related_arns | Sync Code Graph first |
| Empty ARN field | Return validation error | Provide valid ARN |

## Implementation Notes

- Use Qdrant's payload filtering for efficient package and symbol_kind filtering
- Validate ARN format at chunk creation time, not at search time
- Consider caching embedding service connections for performance
- Use batch upsert operations when syncing multiple chunks
- Implement ARN validation against Code Graph during sync, not during search
- Store `related_arns` as a list in Qdrant payload for efficient retrieval
