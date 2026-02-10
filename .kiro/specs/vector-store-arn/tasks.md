# Implementation Plan: Vector Store with ARN Metadata

## Overview

This implementation plan covers the Vector Store with ARN Metadata component for the Archon Knowledge Base Infrastructure. The Vector Store provides semantic search over code and documentation, with search results enriched with ARN metadata that enables graph traversal and cross-referencing.

**This is a child spec** implementing Requirement 11 (Vector Store with ARN Metadata) from the root Archon Agent Pipeline specification.

### Parent Specification Reference

- **Root Spec Location:** `.kiro/specs/archon-agent-pipeline/`
- **Root Spec Repository:** Workspace root (personal-work)
- **Related Requirements:** Requirement 11 (Vector Store with ARN Metadata)
- **Related Correctness Properties:** Properties 25-26

## Tasks

- [x] 1. Data Model Implementation
  - [x] 1.1 Create ArchonChunk dataclass with ARN metadata
    - Create `src/vector/models.py`
    - Define `ArchonChunk` dataclass with all fields from design
    - Include `content`, `source`, `chunk_index` base fields
    - Include `arn`, `related_arns` ARN metadata fields
    - Include `symbol_name`, `symbol_kind`, `package` context fields
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6_
  
  - [x] 1.2 Create SearchResult dataclass with enriched metadata
    - Add `SearchResult` dataclass to `src/vector/models.py`
    - Include all ArchonChunk fields plus `score` field
    - Ensure `score` is a float between 0.0 and 1.0
    - _Requirements: 3.1, 3.2_
  
  - [x] 1.3 Create ARN validation utilities
    - Create `src/vector/arn.py`
    - Implement `validate_arn(arn: str) -> bool` function
    - Implement ARN format regex pattern matching
    - Validate ARN components: type, workspace, package, path, symbol
    - _Requirements: 4.1, 4.2_
  
  - [x] 1.4 Create SymbolKind enum
    - Add `SymbolKind` enum to `src/vector/models.py`
    - Define valid values: function, class, method, variable, type, module
    - _Requirements: 6.6_

- [x] 2. Embedding Service Integration
  - [x] 2.1 Create EmbeddingServiceClient class
    - Create `src/common/embedding.py`
    - Implement `EmbeddingServiceClient` class with service URL configuration
    - Implement async HTTP client for embedding service
    - Handle connection errors and timeouts
    - _Requirements: 2.3_
  
  - [x] 2.2 Implement batch embedding operation
    - Implement `embed(texts: list[str]) -> list[list[float]]` method
    - Implement batch processing with configurable batch size (default: 32)
    - Handle empty input lists
    - _Requirements: 2.3_
  
  - [x] 2.3 Implement single text embedding operation
    - Implement `embed_single(text: str) -> list[float]` method
    - Delegate to batch embed with single-item list
    - _Requirements: 2.3_

- [x] 3. Vector Store Service Implementation
  - [x] 3.1 Create VectorStoreService class
    - Create `src/vector/store.py`
    - Implement `VectorStoreService` class with Qdrant client
    - Configure collection name and embedding service URL
    - Initialize Qdrant client connection
    - _Requirements: 2.1_
  
  - [x] 3.2 Implement upsert operation with ARN metadata
    - Implement `upsert(chunks: list[ArchonChunk]) -> int` method
    - Generate embeddings for chunk content via embedding service
    - Create Qdrant PointStruct with full metadata payload
    - Store embedding vector alongside ARN metadata
    - Return count of chunks upserted
    - _Requirements: 2.1, 2.2, 2.3, 2.4_
  
  - [x] 3.3 Implement search operation with filters
    - Implement `search(query, limit, package, symbol_kind) -> list[SearchResult]` method
    - Generate query embedding via embedding service
    - Execute similarity search with optional filters
    - Return results ordered by score (highest first)
    - _Requirements: 3.1, 3.2, 3.3, 3.4_
  
  - [x] 3.4 Implement delete by package operation
    - Implement `delete_by_package(package: str) -> int` method
    - Delete all chunks matching package filter
    - Return count of chunks deleted
    - _Requirements: 5.1_
  
  - [x] 3.5 Implement delete by ARN operation
    - Implement `delete_by_arn(arn: str) -> int` method
    - Delete all chunks matching ARN filter
    - Return count of chunks deleted
    - _Requirements: 4.1_

- [x] 4. Filter Implementation
  - [x] 4.1 Create Qdrant filter builders
    - Create `src/vector/filters.py`
    - Implement `build_package_filter(package: str) -> Filter` function
    - Use Qdrant FieldCondition with MatchValue
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5_
  
  - [x] 4.2 Implement symbol_kind filter builder
    - Implement `build_symbol_kind_filter(symbol_kind: str) -> Filter` function
    - Validate symbol_kind against allowed values
    - Use Qdrant FieldCondition with MatchValue
    - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5, 6.6_
  
  - [x] 4.3 Implement combined filter builder
    - Implement `build_combined_filter(package, symbol_kind) -> Filter` function
    - Combine filters with AND logic using Qdrant Filter.must
    - Handle cases where one or both filters are None
    - _Requirements: 7.1, 7.2, 7.3, 7.4_

- [x] 5. Sync Adapter Implementation
  - [x] 5.1 Create VectorSyncAdapter class
    - Create `src/sync/vector_adapter.py`
    - Implement `VectorSyncAdapter` class with VectorStoreService dependency
    - Define `VectorSyncResult` dataclass for operation results
    - _Requirements: 2.1_
  
  - [x] 5.2 Implement sync_package operation
    - Implement `sync_package(package, archon_docs) -> VectorSyncResult` method
    - Upsert new/updated chunks
    - Track and report errors
    - Return counts for upserted chunks
    - _Requirements: 2.1, 2.2, 2.3, 2.4_
  
  - [x] 5.3 Implement delete_package operation
    - Implement `delete_package(package: str) -> int` method
    - Delegate to VectorStoreService.delete_by_package
    - Return count of deleted chunks
    - _Requirements: 5.1_

- [x] 6. Property-Based Tests
  - [x] 6.1 PBT for Property 25: Vector Chunk ARN Metadata
    - Create `tests/test_vector_properties.py`
    - Generate random ArchonChunk instances with various ARN formats
    - Upsert chunks to the vector store
    - Retrieve chunks and verify:
      - `arn` field is non-empty and matches ARN format regex
      - `related_arns` is a list (may be empty)
      - Each item in `related_arns` matches ARN format regex
    - Test edge cases: empty `related_arns`, many `related_arns`, special characters
    - Use hypothesis library with minimum 100 iterations
    - **Validates: Requirements 1.1, 1.2, 4.1, 4.2**
  
  - [x] 6.2 PBT for Property 26: ARN-Enriched Search Results
    - Insert chunks with various metadata combinations
    - Execute search queries with different filters
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
    - Use hypothesis library with minimum 100 iterations
    - **Validates: Requirements 3.1, 3.2**

- [x] 7. Integration Tests
  - [x] 7.1 End-to-end upsert and search tests
    - Create `tests/test_vector_integration.py`
    - Test upsert with sample ArchonChunk data
    - Verify chunks are stored with correct metadata
    - Test search returns results with ARN metadata
    - Verify search results are ordered by score
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 3.1, 3.2, 3.3, 3.4_
  
  - [x] 7.2 Filter functionality tests
    - Test search with package filter only
    - Test search with symbol_kind filter only
    - Test search with combined filters
    - Verify filter conditions are applied correctly
    - Test edge cases: no matching results, all results match
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 6.1, 6.2, 6.3, 6.4, 6.5, 6.6, 7.1, 7.2, 7.3, 7.4_
  
  - [x] 7.3 Delete operation tests
    - Test delete_by_package removes all chunks for a package
    - Test delete_by_arn removes specific chunks
    - Verify other chunks are not affected
    - _Requirements: 5.1_
  
  - [x] 7.4 Embedding service integration tests
    - Test embedding generation for single text
    - Test batch embedding generation
    - Test error handling for embedding service failures
    - _Requirements: 2.3_

- [x] 8. Unit Tests
  - [x] 8.1 Data model unit tests
    - Create `tests/test_vector_models.py`
    - Test ArchonChunk dataclass instantiation
    - Test SearchResult dataclass instantiation
    - Test SymbolKind enum values
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 3.1, 3.2_
  
  - [x] 8.2 ARN validation unit tests
    - Create `tests/test_arn_validation.py`
    - Test valid ARN formats are accepted
    - Test invalid ARN formats are rejected
    - Test edge cases: empty string, missing components, special characters
    - _Requirements: 4.1, 4.2_
  
  - [x] 8.3 Filter builder unit tests
    - Create `tests/test_filters.py`
    - Test package filter construction
    - Test symbol_kind filter construction
    - Test combined filter construction
    - Test filter with None values
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 6.1, 6.2, 6.3, 6.4, 6.5, 6.6, 7.1, 7.2, 7.3, 7.4_

## Notes

- Use `qdrant-client` Python library for Qdrant operations
- Use `hypothesis` for property-based testing
- Use Qdrant's in-memory mode for fast unit tests
- Embedding dimension is 768 (BGE-base model)
- Batch upsert operations for efficiency (up to 100 points per batch)
- Validate ARN format at chunk creation time, not at search time
- Use async operations for embedding and Qdrant calls

## Dependencies

- Qdrant vector database (existing infrastructure)
- qdrant-client Python library
- Embedding service (external dependency)
- hypothesis for property-based testing
- httpx or aiohttp for async HTTP client

