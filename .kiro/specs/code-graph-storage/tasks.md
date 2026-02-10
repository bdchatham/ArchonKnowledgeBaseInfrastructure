# Implementation Plan: GraphQL Code Graph Storage

## Overview

This implementation plan covers the GraphQL Code Graph Storage component for the Archon Knowledge Base Infrastructure. The Code Graph provides a queryable representation of code structure and relationships, enabling developers and agents to traverse code relationships through GraphQL queries.

**This is a child spec** implementing Requirement 10 (GraphQL Code Graph Storage) from the root Archon Agent Pipeline specification.

### Parent Specification Reference

- **Root Spec Location:** `.kiro/specs/archon-agent-pipeline/`
- **Root Spec Repository:** Workspace root (personal-work)
- **Related Requirements:** Requirement 10 (GraphQL Code Graph Storage)
- **Related Correctness Properties:** Properties 21-24

## Tasks

- [x] 1. Database Setup
  - [x] 1.1 Create PostgreSQL migration for code_graph_nodes table
    - Create `migrations/001_code_graph_tables.sql`
    - Define `code_graph_nodes` table with all fields from design
    - Add CHECK constraints for `type` and `kind` fields
    - Add `created_at` and `updated_at` timestamp fields
    - _Requirements: 1.1, 1.2, 1.3, 1.4_
  
  - [x] 1.2 Create PostgreSQL migration for code_graph_edges table
    - Add `code_graph_edges` table to migration file
    - Define foreign key constraints with CASCADE DELETE
    - Add CHECK constraint for `type` field
    - Add UNIQUE constraint on (from_arn, to_arn, type)
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5_
  
  - [x] 1.3 Create database indexes
    - Add index on `code_graph_edges(from_arn)` for outgoing edge traversal
    - Add index on `code_graph_edges(to_arn)` for incoming edge traversal
    - Add index on `code_graph_edges(type)` for edge type filtering
    - Add index on `code_graph_nodes(package)` for package-scoped queries
    - Add index on `code_graph_nodes(kind)` for symbol kind filtering
    - Add index on `code_graph_nodes(path)` for file-based lookups
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6_
  
  - [x] 1.4 Create SQLAlchemy models
    - Create `src/graph/models.py`
    - Define `GraphNodeModel` SQLAlchemy model
    - Define `GraphEdgeModel` SQLAlchemy model
    - Define `NodeType`, `SymbolKind`, `EdgeType` enums
    - _Requirements: 1.1, 1.2, 2.1, 2.2_

- [x] 2. Repository Layer
  - [x] 2.1 Create GraphRepository class
    - Create `src/graph/repository.py`
    - Implement `GraphRepository` class with database session management
    - Define `GraphNode` and `GraphEdge` dataclasses
    - _Requirements: 1.1, 2.1_
  
  - [x] 2.2 Implement node CRUD operations
    - Implement `get_node(arn: str)` method
    - Implement `search_nodes(name, kind, package, limit)` method
    - Implement `get_symbols_in_file(path, package)` method
    - Implement `get_symbols_in_package(package, kind)` method
    - _Requirements: 5.1, 5.2, 5.4, 5.5_
  
  - [x] 2.3 Implement edge CRUD operations
    - Implement `get_edges_from(arn, edge_type)` method
    - Implement `get_edges_to(arn, edge_type)` method
    - _Requirements: 4.2, 5.3_
  
  - [x] 2.4 Implement traversal operations
    - Implement `traverse(start_arn, edge_types, depth)` method
    - Handle depth limiting and cycle detection
    - _Requirements: 5.6_
  
  - [x] 2.5 Implement bulk upsert operations
    - Implement `upsert_nodes(nodes)` method using ON CONFLICT
    - Implement `upsert_edges(edges)` method using ON CONFLICT
    - Implement `prune_package(package, keep_arns)` method
    - Return counts for created/updated/removed items
    - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5, 6.6_

- [x] 3. GraphQL Schema
  - [x] 3.1 Create GraphQL type definitions
    - Create `src/graph/schema.py`
    - Define `Node` GraphQL type with all properties
    - Define `NodeType` enum (CODE, DOC, K8S, INFRA)
    - Define `SymbolKind` enum (FUNCTION, CLASS, METHOD, etc.)
    - Define `EdgeType` enum (CONTAINS, REFERENCES, etc.)
    - _Requirements: 4.1, 4.3, 4.4, 4.5_
  
  - [x] 3.2 Create GraphQL input type definitions
    - Define `SymbolInput` input type
    - Define `RelationshipInput` input type
    - Define `SyncResult` type
    - _Requirements: 7.1, 7.2, 7.3_
  
  - [x] 3.3 Create GraphQL query resolvers
    - Create `src/graph/resolvers.py`
    - Implement `node(arn)` resolver
    - Implement `searchNodes(name, kind, package, limit)` resolver
    - Implement `findReferences(arn)` resolver
    - Implement `symbolsInFile(path, package)` resolver
    - Implement `symbolsInPackage(package, kind)` resolver
    - Implement `traverse(startArn, edgeTypes, depth)` resolver
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 5.6_
  
  - [x] 3.4 Create GraphQL mutation resolvers
    - Implement `syncFromScip(packagePath, symbols, relationships, indexHash)` resolver
    - Implement `prunePackage(package, keepArns)` resolver
    - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5, 6.6_
  
  - [x] 3.5 Implement Node relationship fields
    - Implement `contains` field resolver
    - Implement `containedBy` field resolver
    - Implement `references` field resolver
    - Implement `referencedBy` field resolver
    - Implement `implements` field resolver
    - Implement `implementedBy` field resolver
    - Implement `extends` field resolver
    - Implement `extendedBy` field resolver
    - Implement `imports` field resolver
    - Implement `importedBy` field resolver
    - Implement `documentationNode` field resolver
    - _Requirements: 4.2_

- [x] 4. Service Layer
  - [x] 4.1 Create GraphQLService class
    - Create `src/graph/service.py`
    - Implement `GraphQLService` class with configuration
    - Define `GraphQLServiceConfig` dataclass
    - _Requirements: 4.1, 5.1_
  
  - [x] 4.2 Implement query execution
    - Implement `execute(query, variables)` method
    - Handle GraphQL execution errors
    - Return result with 'data' and optional 'errors' keys
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 5.6_
  
  - [x] 4.3 Implement schema introspection
    - Implement `get_schema()` method
    - Return GraphQL schema for introspection queries
    - _Requirements: 4.1_

- [x] 5. Property-Based Tests
  - [x] 5.1 PBT for Property 21: Graph Node ARN Uniqueness
    - Create `tests/test_graph_properties.py`
    - Generate random sets of nodes with potentially duplicate ARNs
    - Verify inserting duplicate ARNs either fails or updates existing node
    - Verify querying by ARN returns exactly one node
    - Use hypothesis library with minimum 100 iterations
    - **Validates: Requirements 1.2**
  
  - [x] 5.2 PBT for Property 22: Graph Edge Referential Integrity
    - Attempt to create edges with non-existent node ARNs (should fail)
    - Create nodes and edges, then delete a node
    - Verify all edges referencing the deleted node are also deleted
    - Use hypothesis library with minimum 100 iterations
    - **Validates: Requirements 2.3, 2.5**
  
  - [x] 5.3 PBT for Property 23: GraphQL Query Completeness
    - Insert nodes with all properties populated
    - Query via GraphQL `node(arn: ...)` query
    - Verify all properties are returned correctly
    - Verify relationship fields return connected nodes
    - Use hypothesis library with minimum 100 iterations
    - **Validates: Requirements 4.1, 4.2, 5.1**
  
  - [x] 5.4 PBT for Property 24: Graph Traversal Correctness
    - Create graphs with known structure (chain, tree, cycle)
    - Execute traversal queries with various edge types and depths
    - Verify results match expected reachable nodes
    - Test edge cases: depth=0, unreachable nodes, cycles
    - Use hypothesis library with minimum 100 iterations
    - **Validates: Requirement 5.6**

- [x] 6. Integration Tests
  - [x] 6.1 End-to-end sync flow tests
    - Create `tests/test_graph_integration.py`
    - Test `syncFromScip` mutation with sample SCIP data
    - Verify nodes and edges are created correctly
    - Test `prunePackage` mutation removes stale nodes
    - Verify cascade delete behavior
    - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5, 6.6_
  
  - [x] 6.2 GraphQL query execution tests
    - Test all query operations with sample data
    - Test relationship traversal fields
    - Test error handling for invalid inputs
    - Test depth limiting for traverse queries
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 5.6_

- [x] 7. Sync Adapter
  - [x] 7.1 Create graph sync adapter
    - Create `src/sync/graph_adapter.py`
    - Implement adapter that calls GraphQL mutations
    - Handle SCIP parse result transformation to GraphQL inputs
    - _Requirements: 6.1, 6.2, 6.3, 6.4_

## Notes

- Use `graphql-core` or `strawberry-graphql` for Python GraphQL implementation
- Use `hypothesis` for property-based testing
- Use PostgreSQL's `ON CONFLICT` clause for efficient upserts
- Implement DataLoader pattern to avoid N+1 queries on relationship fields
- Maximum traversal depth should be configurable (default: 5)
- Search results should be limited (default: 20, max: 100)

## Dependencies

- PostgreSQL database (existing infrastructure)
- SQLAlchemy for ORM
- graphql-core or strawberry-graphql for GraphQL
- hypothesis for property-based testing
- asyncpg for async PostgreSQL access (optional)
