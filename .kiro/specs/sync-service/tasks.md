# Implementation Plan: Knowledge Base Sync Service

## Overview

This implementation plan covers the Knowledge Base Sync Service component for the Archon Knowledge Base Infrastructure. The Sync Service orchestrates the synchronization of code intelligence data (SCIP parse results and Archon documentation) to the knowledge base, ensuring that the Code Graph and Vector Store reflect the current state of the codebase.

**This is a child spec** implementing Requirements 12-13 (Knowledge Base Sync Service, Documentation Pipeline Triggers) from the root Archon Agent Pipeline specification.

### Parent Specification Reference

- **Root Spec Location:** `.kiro/specs/archon-agent-pipeline/`
- **Root Spec Repository:** Workspace root (personal-work)
- **Related Requirements:** Requirements 12 (Knowledge Base Sync Service), 13 (Documentation Pipeline Triggers)
- **Related Correctness Properties:** Properties 27-28

## Tasks

- [x] 1. Data Models
  - [x] 1.1 Create input data models
    - Create `src/sync/models.py`
    - Define `SymbolLocation` dataclass with file, line, column fields
    - Define `ScipSymbol` dataclass with arn, type, workspace, package, path, symbol, kind, name, signature, documentation, location fields
    - Define `ScipRelationship` dataclass with from_arn, to_arn, type fields
    - Define `ScipParseResult` dataclass with symbols, relationships, hash fields
    - Define `GeneratedDoc` dataclass with source_path, doc_path, content, arn, referenced_arns fields
    - _Requirements: 1.1, 1.2, 1.3_
  
  - [x] 1.2 Create result type models
    - Define `SyncResult` dataclass with success, nodes_created, nodes_updated, edges_created, chunks_upserted, nodes_pruned, chunks_pruned, skipped, skip_reason, errors fields
    - Define `PackageSyncResult` dataclass with package, success, and all SyncResult fields
    - Define `WorkspaceSyncResult` dataclass with success, packages_synced, packages_skipped, errors fields
    - Define `GraphSyncResult` dataclass with nodes_created, nodes_updated, edges_created, nodes_pruned fields
    - Define `VectorSyncResult` dataclass with chunks_upserted, chunks_pruned, errors fields
    - _Requirements: 9.1, 9.2, 9.3, 9.4_
  
  - [x] 1.3 Create hash state model
    - Define `HashState` dataclass with package, index_hash, last_synced fields
    - _Requirements: 7.1, 7.2_

- [x] 2. Graph Sync Adapter
  - [x] 2.1 Create GraphSyncAdapter class
    - Create `src/sync/graph_adapter.py`
    - Implement `GraphSyncAdapter` class with `GraphRepository` dependency injection
    - Add constructor accepting `graph_repository` parameter
    - _Requirements: 2.1, 3.1_
  
  - [x] 2.2 Implement symbol to node transformation
    - Implement `transform_symbol_to_node(symbol, index_hash)` method
    - Map SCIP symbol fields to GraphNode fields per design specification
    - Handle optional fields (symbol, signature, documentation)
    - _Requirements: 2.1, 2.2_
  
  - [x] 2.3 Implement relationship to edge transformation
    - Implement `transform_relationship_to_edge(relationship)` method
    - Map SCIP relationship fields to GraphEdge fields per design specification
    - Validate edge type is one of: contains, references, implements, extends, imports, documents
    - _Requirements: 3.1, 3.2_
  
  - [x] 2.4 Implement sync_symbols operation
    - Implement `async sync_symbols(package, symbols, index_hash)` method
    - Transform all symbols to nodes using `transform_symbol_to_node`
    - Perform batch upsert operations (up to 100 per batch)
    - Track and return counts of nodes created and updated
    - _Requirements: 2.1, 2.2, 2.3, 2.4_
  
  - [x] 2.5 Implement sync_relationships operation
    - Implement `async sync_relationships(relationships)` method
    - Transform all relationships to edges using `transform_relationship_to_edge`
    - Perform batch upsert operations
    - Track and return count of edges created
    - _Requirements: 3.1, 3.2, 3.3, 3.4_
  
  - [x] 2.6 Implement prune_stale_nodes operation
    - Implement `async prune_stale_nodes(package, current_arns)` method
    - Query for nodes in package not in current_arns list
    - Delete stale nodes (cascade deletes edges)
    - Track and return count of nodes pruned
    - _Requirements: 6.1, 6.2, 6.6_

- [x] 3. Vector Sync Adapter
  - [x] 3.1 Create VectorSyncAdapter class
    - Create `src/sync/vector_adapter.py`
    - Implement `VectorSyncAdapter` class with `VectorStoreService` and `EmbeddingServiceClient` dependencies
    - Add constructor with chunk_size (default 600) and chunk_overlap (default 100) parameters
    - _Requirements: 4.1, 5.1_
  
  - [x] 3.2 Implement document chunking
    - Implement `chunk_document(doc)` method
    - Split document content into chunks of 400-800 tokens
    - Preserve ARN metadata in each chunk (arn, related_arns, symbol_name, symbol_kind, package)
    - Handle chunk overlap for context preservation
    - Return list of `ArchonChunk` instances
    - _Requirements: 4.1, 4.3_
  
  - [x] 3.3 Implement chunk ID generation
    - Implement `generate_chunk_id(arn, chunk_index)` method
    - Generate deterministic point ID using hash of (ARN + chunk_index)
    - Ensure idempotent upserts with same inputs
    - _Requirements: 5.2, 8.3_
  
  - [x] 3.4 Implement sync_docs operation
    - Implement `async sync_docs(package, archon_docs)` method
    - Chunk each document using `chunk_document`
    - Generate embeddings for chunks via embedding service (batch of 32)
    - Upsert chunks to Vector Store with full metadata payload
    - Prune stale chunks
    - Return `VectorSyncResult` with counts and errors
    - _Requirements: 4.1, 4.2, 4.4, 5.1, 5.3_
  
  - [x] 3.5 Implement prune_stale_chunks operation
    - Implement `async prune_stale_chunks(package, current_arns)` method
    - Query for chunks in package not in current_arns list
    - Delete stale chunks from Vector Store
    - Track and return count of chunks pruned
    - _Requirements: 6.3, 6.4, 6.5_

- [x] 4. Change Detector
  - [x] 4.1 Create ChangeDetector class
    - Create `src/sync/change_detector.py`
    - Implement `ChangeDetector` class with state_file parameter (default `.archon/sync-state.json`)
    - _Requirements: 7.1, 7.2_
  
  - [x] 4.2 Implement hash comparison
    - Implement `has_changed(package, current_hash)` method
    - Compare current hash with stored hash for package
    - Return True if hash differs or no stored hash exists
    - _Requirements: 7.1_
  
  - [x] 4.3 Implement state file loading/saving
    - Implement `load_state()` method to read state from JSON file
    - Implement `save_state(state)` method to write state to JSON file
    - Handle missing state file gracefully (return empty state)
    - Create parent directories if needed
    - _Requirements: 7.2_
  
  - [x] 4.4 Implement hash update
    - Implement `get_stored_hash(package)` method
    - Implement `update_hash(package, new_hash)` method
    - Update stored hash and last_synced timestamp
    - Persist state to file after update
    - _Requirements: 7.2_

- [x] 5. Sync Service
  - [x] 5.1 Create KnowledgeBaseSyncService class
    - Create `src/sync/service.py`
    - Implement `KnowledgeBaseSyncService` class with graph_adapter, vector_adapter, change_detector dependencies
    - Add constructor accepting all three dependencies
    - _Requirements: 9.1_
  
  - [x] 5.2 Implement sync_package operation
    - Implement `async sync_package(package_path, scip_result, archon_docs)` method
    - Check if SCIP index hash has changed (skip if unchanged)
    - Sync symbols to Code Graph as nodes
    - Sync relationships to Code Graph as edges
    - Sync Archon docs to Vector Store
    - Prune stale nodes and chunks
    - Update stored hash after successful sync
    - Return `SyncResult` with all counts and errors
    - _Requirements: 9.1, 9.2_
  
  - [x] 5.3 Implement sync_workspace operation
    - Implement `async sync_workspace(workspace_path, force)` method
    - Discover packages in workspace
    - Load SCIP indexes and Archon docs for each package
    - Sync packages in parallel (max 4 concurrent with semaphore)
    - Aggregate results into `WorkspaceSyncResult`
    - Handle partial failures gracefully
    - _Requirements: 9.3, 9.4_
  
  - [x] 5.4 Implement change detection integration
    - Integrate change detector into sync_package flow
    - Skip sync when hash unchanged and force=false
    - Proceed with sync when force=true regardless of hash
    - Set skipped=true and skip_reason in result when skipping
    - _Requirements: 7.1, 7.3, 7.4, 7.5_
  
  - [x] 5.5 Implement input validation
    - Validate SCIP parse result structure
    - Validate Archon doc format
    - Validate package_path is non-empty
    - Log warnings for invalid ARN formats, skip invalid entries
    - Return descriptive errors for invalid inputs
    - _Requirements: 1.4_
  
  - [x] 5.6 Implement transaction management
    - Wrap graph operations in database transaction
    - Ensure atomic sync per package (all-or-nothing)
    - Handle transaction rollback on errors
    - _Requirements: 8.1, 8.2, 8.3, 8.4_

- [x] 6. Kiro Hook Configuration
  - [x] 6.1 Create hook configuration file
    - Create `.kiro/hooks/archon-knowledge-base-sync.json`
    - Configure hook to trigger on `agentStop` event
    - Set hook action to `askAgent` with prompt to sync if documentation was generated
    - Include hook name, version, and description
    - _Requirements: 10.1, 10.2, 10.3, 10.4_
  
  - [x] 6.2 Document hook usage
    - Add hook documentation to `.kiro/docs/operations.md`
    - Document trigger conditions and expected behavior
    - Include troubleshooting guidance for hook failures
    - _Requirements: 10.5_

- [x] 7. Property-Based Tests
  - [x] 7.1 PBT for Property 27: Sync Idempotency
    - Create `tests/test_sync_properties.py`
    - Generate random SCIP parse results with symbols and relationships
    - Generate random Archon docs with content and ARN references
    - Run sync_package multiple times (N=1, 2, 5) with same inputs
    - Capture state after each sync (nodes, edges, chunks)
    - Verify state after N syncs equals state after 1 sync
    - Verify no duplicate ARNs in nodes
    - Verify no duplicate (from_arn, to_arn, type) in edges
    - Verify no duplicate (arn, chunk_index) in chunks
    - Use hypothesis library with minimum 100 iterations
    - **Validates: Requirement 8 (Sync Idempotency)**
  
  - [x] 7.2 PBT for Property 28: Sync Change Detection
    - Generate SCIP parse result with known hash
    - Run sync_package to establish baseline state
    - Run sync_package again with same hash and force=false
    - Verify sync was skipped (skipped=true in result)
    - Verify no database operations occurred
    - Run sync_package again with same hash and force=true
    - Verify sync proceeded (skipped=false in result)
    - Modify SCIP parse result (different hash)
    - Run sync_package with force=false
    - Verify sync proceeded (skipped=false in result)
    - Use hypothesis library with minimum 100 iterations
    - **Validates: Requirement 7 (Change Detection)**

- [x] 8. Unit Tests
  - [x] 8.1 Graph sync adapter unit tests
    - Create `tests/test_graph_adapter.py`
    - Test symbol to node transformation with all field mappings
    - Test relationship to edge transformation
    - Test batch upsert operations
    - Test stale node pruning
    - Test error handling for invalid inputs
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 3.1, 3.2, 3.3, 3.4, 6.1, 6.2_
  
  - [x] 8.2 Vector sync adapter unit tests
    - Create `tests/test_vector_adapter.py`
    - Test document chunking with various content sizes
    - Test chunk ID generation determinism
    - Test metadata preservation in chunks
    - Test batch embedding and upsert
    - Test stale chunk pruning
    - Test error handling for embedding service failures
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 5.1, 5.2, 5.3, 6.3, 6.4, 6.5_
  
  - [x] 8.3 Change detector unit tests
    - Create `tests/test_change_detector.py`
    - Test hash comparison with matching and differing hashes
    - Test state file loading with existing and missing files
    - Test state file saving and persistence
    - Test hash update with timestamp
    - _Requirements: 7.1, 7.2_
  
  - [x] 8.4 Sync service unit tests
    - Create `tests/test_sync_service.py`
    - Test sync_package with valid inputs
    - Test sync_package with invalid inputs
    - Test sync_package skip behavior when hash unchanged
    - Test sync_package force behavior
    - Test sync_workspace with multiple packages
    - Test partial failure handling
    - _Requirements: 9.1, 9.2, 9.3, 9.4, 7.3, 7.4, 7.5_

- [x] 9. Integration Tests
  - [x] 9.1 End-to-end sync flow tests
    - Create `tests/test_sync_integration.py`
    - Test full sync flow with real PostgreSQL and Qdrant
    - Verify nodes and edges created correctly in Code Graph
    - Verify chunks upserted correctly in Vector Store
    - Verify stale data pruned correctly
    - Verify hash state persisted correctly
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 3.1, 3.2, 3.3, 3.4, 4.1, 4.2, 4.3, 4.4, 5.1, 5.2, 5.3, 6.1, 6.2, 6.3, 6.4, 6.5, 6.6_
  
  - [x] 9.2 Concurrent sync tests
    - Test workspace sync with multiple packages in parallel
    - Verify no race conditions or deadlocks
    - Verify correct aggregation of results
    - Test semaphore limiting (max 4 concurrent)
    - _Requirements: 9.3, 9.4_

- [x] 10. MCP Tool Integration
  - [x] 10.1 Create sync_to_knowledge_base MCP tool
    - Add tool to ArchonDocumentationMCPTools package
    - Implement `SyncToKnowledgeBaseInput` interface
    - Implement `SyncToKnowledgeBaseOutput` interface
    - Call KnowledgeBaseSyncService from tool handler
    - Support optional packages filter and force parameter
    - _Requirements: 11.1, 11.2, 11.3, 11.4, 11.5_

## Notes

- Use `hypothesis` for property-based testing with minimum 100 iterations per property
- Use database transactions for atomic sync operations (all-or-nothing per package)
- Implement retry logic with exponential backoff for transient failures
- Use batch operations for efficiency (bulk upsert nodes, bulk upsert edges, batch embed chunks)
- Store index hashes in `.archon/sync-state.json`
- Consider parallel processing for workspace sync (max 4 concurrent syncs)
- Implement structured logging with `structlog` for debugging sync issues
- Use deterministic point IDs for Vector Store to enable idempotent upserts

## Dependencies

- PostgreSQL database (existing infrastructure from code-graph-storage spec)
- Qdrant vector store (existing infrastructure from vector-store-arn spec)
- GraphRepository from code-graph-storage spec
- VectorStoreService from vector-store-arn spec
- EmbeddingServiceClient from existing infrastructure
- hypothesis for property-based testing
- structlog for structured logging
- asyncio for parallel processing
