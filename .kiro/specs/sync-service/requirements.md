# Requirements Document: Knowledge Base Sync Service

## Introduction

This specification defines the Knowledge Base Sync Service component for the Archon Knowledge Base Infrastructure. The Sync Service orchestrates the synchronization of code intelligence data (SCIP parse results and Archon documentation) to the knowledge base, ensuring that the Code Graph and Vector Store reflect the current state of the codebase.

**This is a child spec** created by the root Archon Agent Pipeline specification. It implements Requirements 12-13 (Knowledge Base Sync Service, Documentation Pipeline Triggers) from the root spec, focusing on:
- Transforming SCIP symbols and relationships to Code Graph nodes and edges
- Chunking and embedding Archon documentation for the Vector Store
- Idempotent synchronization with change detection
- Automated pipeline triggers for documentation updates

### Parent Specification Reference

- **Root Spec Location:** `.kiro/specs/archon-agent-pipeline/`
- **Root Spec Repository:** Workspace root (personal-work)
- **Related Requirements:** Requirements 12 (Knowledge Base Sync Service), 13 (Documentation Pipeline Triggers)
- **Related Correctness Properties:** Properties 27-28

### Integration Context

The Sync Service integrates with:
- **ArchonDocumentationMCPTools**: Provides SCIP parse results and Archon documentation as input
- **Code Graph (PostgreSQL)**: Target for symbol and relationship synchronization
- **Vector Store (Qdrant)**: Target for documentation chunk embeddings with ARN metadata
- **Embedding Service**: Generates embeddings for documentation chunks
- **Kiro Hooks**: Triggers sync after documentation generation

## Glossary

- **Sync_Service**: Service that orchestrates synchronization of code intelligence to the knowledge base
- **SCIP_Parse_Result**: Output from parsing a SCIP index, containing symbols and relationships
- **Archon_Doc**: Documentation file co-located with source code (`<name>.archon.md`)
- **Generated_Doc**: Output from the documentation generator, containing content and ARN references
- **Code_Graph**: GraphQL-queryable representation of code structure stored in PostgreSQL
- **Vector_Store**: Embedding-based storage for semantic search (Qdrant)
- **ARN**: Archon Resource Name - deterministic identifier for graph nodes (format: `arn:archon:<type>:<workspace>/<package>/<path>#<symbol>`)
- **Index_Hash**: SHA-256 hash of SCIP index content for change detection
- **Graph_Sync_Adapter**: Component that transforms SCIP data to Code Graph operations
- **Vector_Sync_Adapter**: Component that chunks, embeds, and upserts documentation to Vector Store
- **Idempotent**: Property where multiple executions with same input produce identical state
- **Stale_Node**: A node in the Code Graph that no longer corresponds to existing code
- **Stale_Chunk**: A chunk in the Vector Store that no longer corresponds to existing documentation
- **Pipeline_Trigger**: Automated mechanism that invokes the sync service
- **Force_Sync**: Option to bypass change detection and perform full synchronization

## Requirements

### Requirement 1: Sync Service Input Handling

**User Story:** As a platform operator, I want the sync service to accept SCIP parse results and Archon docs, so that code intelligence can be synchronized to the knowledge base.

#### Acceptance Criteria

1.1 THE Sync_Service SHALL accept SCIP parse results as input, containing:
  - `symbols`: Array of symbol definitions with ARN, type, kind, name, signature, documentation, location
  - `relationships`: Array of relationships with from_arn, to_arn, and type
  - `hash`: SHA-256 hash of the SCIP index content

1.2 THE Sync_Service SHALL accept Archon documentation as input, containing:
  - `sourcePath`: Path to the source file being documented
  - `docPath`: Path to the generated `.archon.md` file
  - `content`: Documentation content
  - `arn`: ARN of the documented symbol/file
  - `referencedArns`: List of ARNs referenced in the documentation

1.3 THE Sync_Service SHALL accept a `package_path` parameter identifying the package being synchronized.

1.4 THE Sync_Service SHALL validate input data and return descriptive errors for invalid inputs.

**Validates Root Spec:** Requirement 12.1

### Requirement 2: Code Graph Symbol Synchronization

**User Story:** As a platform operator, I want SCIP symbols transformed to Code Graph nodes, so that code structure is queryable via GraphQL.

#### Acceptance Criteria

2.1 THE Sync_Service SHALL transform SCIP symbols to Code_Graph nodes using the ARN as primary key.

2.2 THE transformation SHALL map SCIP symbol fields to node fields:
  - `arn` → `arn` (primary key)
  - `type` → `type` (code, doc, k8s, infra)
  - `workspace` → `workspace`
  - `package` → `package`
  - `path` → `path`
  - `symbol` → `symbol`
  - `kind` → `kind` (function, class, method, variable, type, module)
  - `name` → `name`
  - `signature` → `signature`
  - `documentation` → `documentation`
  - `location.file` → `file_path`
  - `location.line` → `line_number`
  - `hash` → `index_hash`

2.3 THE Sync_Service SHALL perform upsert operations (insert or update on ARN conflict).

2.4 THE Sync_Service SHALL track the count of nodes created and nodes updated.

**Validates Root Spec:** Requirement 12.2

### Requirement 3: Code Graph Relationship Synchronization

**User Story:** As a platform operator, I want SCIP relationships transformed to Code Graph edges, so that code relationships are traversable.

#### Acceptance Criteria

3.1 THE Sync_Service SHALL transform SCIP relationships to Code_Graph edges.

3.2 THE transformation SHALL map relationship fields to edge fields:
  - `from_arn` → `from_arn` (foreign key to nodes)
  - `to_arn` → `to_arn` (foreign key to nodes)
  - `type` → `type` (contains, references, implements, extends, imports, documents)

3.3 THE Sync_Service SHALL perform upsert operations for edges (insert or update on unique constraint conflict).

3.4 THE Sync_Service SHALL track the count of edges created.

**Validates Root Spec:** Requirement 12.3

### Requirement 4: Documentation Chunking and Embedding

**User Story:** As a platform operator, I want Archon docs chunked and embedded, so that semantic search returns relevant documentation.

#### Acceptance Criteria

4.1 THE Sync_Service SHALL chunk Archon documentation into segments suitable for embedding (400-800 tokens per chunk).

4.2 THE Sync_Service SHALL generate embeddings for each chunk via the embedding service.

4.3 THE chunking process SHALL preserve ARN metadata:
  - `arn`: ARN of the documented symbol/file
  - `related_arns`: ARNs referenced in the chunk content
  - `symbol_name`: Name of the symbol being documented
  - `symbol_kind`: Kind of symbol (function, class, etc.)
  - `package`: Package name

4.4 THE Sync_Service SHALL handle embedding service failures gracefully with retry logic.

**Validates Root Spec:** Requirement 12.4

### Requirement 5: Vector Store Upsert

**User Story:** As a platform operator, I want documentation chunks upserted to the Vector Store with ARN metadata, so that search results enable graph traversal.

#### Acceptance Criteria

5.1 THE Sync_Service SHALL upsert chunks to the Vector_Store with the full metadata payload:
  - `content`: Chunk text content
  - `source`: File path to the `.archon.md` file
  - `chunk_index`: Index of chunk within the source file
  - `arn`: ARN of the documented symbol/file
  - `related_arns`: ARNs referenced in the chunk
  - `symbol_name`: Name of the symbol
  - `symbol_kind`: Kind of symbol
  - `package`: Package name

5.2 THE upsert operation SHALL use a deterministic point ID derived from the ARN and chunk index.

5.3 THE Sync_Service SHALL track the count of chunks upserted.

**Validates Root Spec:** Requirement 12.5

### Requirement 6: Stale Data Pruning

**User Story:** As a platform operator, I want stale nodes and chunks removed when symbols are deleted, so that the knowledge base stays consistent with the codebase.

#### Acceptance Criteria

6.1 THE Sync_Service SHALL prune stale nodes from the Code Graph when symbols are removed from the SCIP index.

6.2 THE pruning operation SHALL identify stale nodes as those with ARNs not present in the current SCIP parse result for the package.

6.3 THE Sync_Service SHALL prune stale chunks from the Vector Store when documentation is removed.

6.4 THE pruning operation SHALL identify stale chunks as those with ARNs not present in the current Archon docs for the package.

6.5 THE Sync_Service SHALL track the count of nodes and chunks pruned.

6.6 THE pruning operation SHALL respect cascade delete behavior (deleting a node deletes its edges).

**Validates Root Spec:** Requirement 12.6

### Requirement 7: Change Detection

**User Story:** As a platform operator, I want synchronization skipped when nothing has changed, so that resources are used efficiently.

#### Acceptance Criteria

7.1 THE Sync_Service SHALL skip synchronization when the SCIP index hash has not changed since the last sync.

7.2 THE Sync_Service SHALL store the last synchronized index hash per package.

7.3 THE Sync_Service SHALL support a `force` parameter to bypass change detection.

7.4 WHEN `force=true` THEN the Sync_Service SHALL perform full synchronization regardless of hash comparison.

7.5 WHEN synchronization is skipped THEN the Sync_Service SHALL return a result indicating the skip reason.

**Validates Root Spec:** Requirement 12.7

### Requirement 8: Sync Idempotency

**User Story:** As a platform operator, I want multiple syncs with the same input to produce identical state, so that the system is reliable and predictable.

#### Acceptance Criteria

8.1 THE Sync_Service SHALL be idempotent: multiple syncs with the same SCIP result and Archon docs SHALL produce identical Code Graph and Vector Store state.

8.2 THE idempotency guarantee SHALL apply to:
  - Node creation and updates (same ARN → same node state)
  - Edge creation (same from_arn, to_arn, type → same edge)
  - Chunk upserts (same ARN + chunk_index → same chunk state)

8.3 THE Sync_Service SHALL use deterministic identifiers for all operations:
  - Nodes: ARN as primary key
  - Edges: (from_arn, to_arn, type) as unique constraint
  - Chunks: Hash of (ARN + chunk_index) as point ID

8.4 THE Sync_Service SHALL not create duplicate entries on repeated execution.

**Validates Root Spec:** Requirement 12.8

### Requirement 9: Sync Service Interface

**User Story:** As a developer, I want a well-defined sync service interface, so that I can integrate synchronization into workflows.

#### Acceptance Criteria

9.1 THE Sync_Service SHALL expose a `sync_package` method with the following signature:
```python
async def sync_package(
    self,
    package_path: str,
    scip_result: ScipParseResult,
    archon_docs: list[GeneratedDoc],
) -> SyncResult
```

9.2 THE `SyncResult` type SHALL include:
  - `success`: Boolean indicating overall success
  - `nodes_created`: Count of new nodes inserted
  - `nodes_updated`: Count of existing nodes updated
  - `edges_created`: Count of new edges inserted
  - `chunks_upserted`: Count of chunks upserted to Vector Store
  - `nodes_pruned`: Count of stale nodes removed
  - `chunks_pruned`: Count of stale chunks removed
  - `skipped`: Boolean indicating if sync was skipped due to unchanged hash
  - `errors`: List of error messages if any

9.3 THE Sync_Service SHALL expose a `sync_workspace` method with the following signature:
```python
async def sync_workspace(
    self,
    workspace_path: str,
    force: bool = False,
) -> WorkspaceSyncResult
```

9.4 THE `WorkspaceSyncResult` type SHALL include:
  - `success`: Boolean indicating overall success
  - `packages_synced`: List of package sync results
  - `packages_skipped`: List of packages skipped due to unchanged hash
  - `errors`: List of errors by package

**Validates Root Spec:** Requirement 12.9

### Requirement 10: Kiro Hook for Knowledge Base Sync

**User Story:** As a platform operator, I want a Kiro hook that triggers knowledge base sync after documentation generation, so that the system stays current automatically.

#### Acceptance Criteria

10.1 A Kiro hook SHALL trigger knowledge base sync after documentation generation completes.

10.2 THE hook SHALL be configured to run on `agentStop` event when documentation was generated.

10.3 THE hook SHALL invoke the `sync_to_knowledge_base` MCP tool.

10.4 THE hook configuration SHALL be stored in the workspace `.kiro/hooks/` directory.

10.5 THE hook SHALL include context about which packages were documented.

**Validates Root Spec:** Requirements 13.1, 13.2, 13.3

### Requirement 11: Manual Sync Trigger

**User Story:** As a platform operator, I want a manual trigger for on-demand sync, so that I can synchronize specific packages when needed.

#### Acceptance Criteria

11.1 A manual trigger SHALL be available via MCP tool for on-demand sync.

11.2 THE manual trigger SHALL support package filtering via a `packages` parameter.

11.3 THE manual trigger SHALL support force sync via a `force` parameter.

11.4 THE MCP tool interface SHALL be:
```typescript
interface SyncToKnowledgeBaseInput {
  workspacePath: string;
  packages?: string[];      // Optional: specific packages to sync
  force?: boolean;          // Force full sync
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

11.5 THE MCP tool SHALL be implemented in the ArchonDocumentationMCPTools package.

**Validates Root Spec:** Requirements 13.4, 13.5, 13.6

## Correctness Properties

*Properties define characteristics that should hold true across all valid executions. These bridge human-readable specifications and machine-verifiable correctness guarantees.*

### Property 27: Sync Idempotency

*For any* package, running the sync service multiple times with the same SCIP result and Archon docs SHALL produce identical Code Graph and Vector Store state. Specifically:
- The set of nodes after N syncs SHALL equal the set after 1 sync
- The set of edges after N syncs SHALL equal the set after 1 sync
- The set of chunks after N syncs SHALL equal the set after 1 sync
- No duplicate entries SHALL be created

**Validates:** Requirement 8 (Sync Idempotency)

### Property 28: Sync Change Detection

*For any* package where the SCIP index hash has not changed since the last sync, the sync service SHALL skip synchronization unless `force=true`. Specifically:
- WHEN hash matches stored hash AND force=false THEN sync SHALL be skipped
- WHEN hash matches stored hash AND force=true THEN sync SHALL proceed
- WHEN hash differs from stored hash THEN sync SHALL proceed regardless of force parameter

**Validates:** Requirement 7 (Change Detection)

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

### Vector Store Errors

| Error Condition | Response | Recovery |
|-----------------|----------|----------|
| Embedding service unavailable | Return service unavailable | Retry with backoff |
| Qdrant connection failure | Return service unavailable | Retry with backoff |
| Invalid chunk content | Log warning, skip chunk | Fix chunking logic |

### Sync Orchestration Errors

| Error Condition | Response | Recovery |
|-----------------|----------|----------|
| Partial sync failure | Return partial success with errors | Retry failed packages |
| Hash storage failure | Log warning, continue sync | Manual hash reset |
| Pruning failure | Log error, continue | Manual cleanup |

## Implementation Notes

- Use database transactions for atomic sync operations (all-or-nothing per package)
- Implement retry logic with exponential backoff for transient failures
- Use batch operations for efficiency (bulk upsert nodes, bulk upsert edges, batch embed chunks)
- Store index hashes in a persistent location (e.g., `.archon/sync-state.json` or database table)
- Consider parallel processing for workspace sync (sync packages concurrently)
- Implement proper logging for debugging sync issues
- Use deterministic point IDs for Vector Store to enable idempotent upserts

