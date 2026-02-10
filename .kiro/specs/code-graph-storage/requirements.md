# Requirements Document: GraphQL Code Graph Storage

## Introduction

This specification defines the GraphQL Code Graph Storage component for the Archon Knowledge Base Infrastructure. The Code Graph provides a queryable representation of code structure and relationships, enabling developers and agents to traverse code relationships and find relevant code through GraphQL queries.

**This is a child spec** created by the root Archon Agent Pipeline specification. It implements Requirement 10 (GraphQL Code Graph Storage) from the root spec, focusing on:
- PostgreSQL schema for storing code graph nodes and edges
- GraphQL schema and resolvers for querying the graph
- Mutations for syncing from SCIP parse results and pruning stale data

### Parent Specification Reference

- **Root Spec Location:** `.kiro/specs/archon-agent-pipeline/`
- **Root Spec Repository:** Workspace root (personal-work)
- **Related Requirements:** Requirement 10 (GraphQL Code Graph Storage)
- **Related Correctness Properties:** Properties 21-24

### Integration Context

The Code Graph integrates with:
- **ArchonDocumentationMCPTools**: Provides SCIP parse results via the sync service
- **ArchonMCPServer**: Exposes `archon.graph` tool that proxies GraphQL queries
- **Vector Store**: Chunks reference ARNs that resolve to Code Graph nodes

## Glossary

- **Code_Graph**: GraphQL-queryable representation of code structure and relationships stored in PostgreSQL
- **Node**: A vertex in the Code Graph representing a code symbol, file, package, or documentation
- **Edge**: A directed relationship between two nodes (e.g., contains, references, implements)
- **ARN**: Archon Resource Name - deterministic identifier for graph nodes (format: `arn:archon:<type>:<workspace>/<package>/<path>#<symbol>`)
- **SCIP**: Source Code Index Protocol - standard format for code intelligence data
- **SCIP_Parse_Result**: Output from parsing a SCIP index, containing symbols and relationships
- **GraphQL_Resolver**: Function that fetches data for a GraphQL field
- **Cascade_Delete**: Database behavior where deleting a node automatically deletes its edges
- **Index_Hash**: SHA-256 hash of SCIP index content for change detection
- **Symbol_Kind**: Classification of a code symbol (function, class, method, variable, type, module, file, package)
- **Edge_Type**: Classification of a relationship (contains, references, implements, extends, imports, documents)

## Requirements

### Requirement 1: PostgreSQL Node Storage

**User Story:** As a knowledge base operator, I want code symbols stored in PostgreSQL with comprehensive metadata, so that the graph can be queried efficiently.

#### Acceptance Criteria

1.1 THE Code_Graph SHALL use PostgreSQL with a `code_graph_nodes` table as the primary storage for graph nodes.

1.2 THE `code_graph_nodes` table SHALL store the following fields:
  - `arn` (TEXT, PRIMARY KEY): Archon Resource Name uniquely identifying the node
  - `type` (TEXT, NOT NULL): Resource type (code, doc, k8s, infra)
  - `workspace` (TEXT, NOT NULL): Workspace identifier
  - `package` (TEXT, NOT NULL): Package/repository name
  - `path` (TEXT, NOT NULL): File path relative to package
  - `symbol` (TEXT, NULLABLE): Symbol name within the file
  - `kind` (TEXT, NULLABLE): Symbol kind (function, class, method, variable, type, module, file, package)
  - `name` (TEXT, NOT NULL): Human-readable name
  - `signature` (TEXT, NULLABLE): Function/method signature
  - `documentation` (TEXT, NULLABLE): Documentation string
  - `file_path` (TEXT, NULLABLE): Absolute file path for resolution
  - `line_number` (INTEGER, NULLABLE): Line number in source file
  - `index_hash` (TEXT, NULLABLE): SCIP index hash for change detection

1.3 THE `type` field SHALL be constrained to valid values: 'code', 'doc', 'k8s', 'infra'.

1.4 THE `kind` field SHALL be constrained to valid values: 'function', 'class', 'method', 'variable', 'type', 'module', 'file', 'package'.

**Validates Root Spec:** Requirement 10.1

### Requirement 2: PostgreSQL Edge Storage

**User Story:** As a knowledge base operator, I want code relationships stored with referential integrity, so that graph traversals are reliable.

#### Acceptance Criteria

2.1 THE Code_Graph SHALL use a `code_graph_edges` table storing relationships between nodes.

2.2 THE `code_graph_edges` table SHALL store the following fields:
  - `id` (SERIAL, PRIMARY KEY): Auto-incrementing edge identifier
  - `from_arn` (TEXT, NOT NULL, FOREIGN KEY): Source node ARN
  - `to_arn` (TEXT, NOT NULL, FOREIGN KEY): Target node ARN
  - `type` (TEXT, NOT NULL): Relationship type

2.3 THE `from_arn` and `to_arn` fields SHALL reference `code_graph_nodes(arn)` with CASCADE DELETE behavior.

2.4 THE `type` field SHALL be constrained to valid values: 'contains', 'references', 'implements', 'extends', 'imports', 'documents'.

2.5 THE table SHALL enforce uniqueness on the combination of (from_arn, to_arn, type).

**Validates Root Spec:** Requirements 10.2, 10.3

### Requirement 3: Database Indexes

**User Story:** As a knowledge base operator, I want efficient queries on common access patterns, so that graph operations perform well at scale.

#### Acceptance Criteria

3.1 THE Code_Graph SHALL maintain an index on `code_graph_edges(from_arn)` for outgoing edge traversal.

3.2 THE Code_Graph SHALL maintain an index on `code_graph_edges(to_arn)` for incoming edge traversal.

3.3 THE Code_Graph SHALL maintain an index on `code_graph_edges(type)` for edge type filtering.

3.4 THE Code_Graph SHALL maintain an index on `code_graph_nodes(package)` for package-scoped queries.

3.5 THE Code_Graph SHALL maintain an index on `code_graph_nodes(kind)` for symbol kind filtering.

3.6 THE Code_Graph SHALL maintain an index on `code_graph_nodes(path)` for file-based lookups.

**Validates Root Spec:** Requirement 10.13

### Requirement 4: GraphQL Node Type

**User Story:** As a developer, I want to query node properties and traverse relationships via GraphQL, so that I can explore code structure programmatically.

#### Acceptance Criteria

4.1 THE GraphQL schema SHALL expose a `Node` type with all node properties: arn, type, workspace, package, path, symbol, kind, name, signature, documentation, filePath, lineNumber.

4.2 THE `Node` type SHALL include relationship traversal fields:
  - `contains`: Nodes contained by this node
  - `containedBy`: Node that contains this node
  - `references`: Nodes this node references
  - `referencedBy`: Nodes that reference this node
  - `implements`: Interfaces/types this node implements
  - `implementedBy`: Nodes that implement this node
  - `extends`: Types this node extends
  - `extendedBy`: Nodes that extend this node
  - `imports`: Modules this node imports
  - `importedBy`: Nodes that import this node
  - `documentation`: Link to associated .archon.md doc node

4.3 THE GraphQL schema SHALL define `NodeType` enum with values: CODE, DOC, K8S, INFRA.

4.4 THE GraphQL schema SHALL define `SymbolKind` enum with values: FUNCTION, CLASS, METHOD, VARIABLE, TYPE, MODULE, FILE, PACKAGE.

4.5 THE GraphQL schema SHALL define `EdgeType` enum with values: CONTAINS, REFERENCES, IMPLEMENTS, EXTENDS, IMPORTS, DOCUMENTS.

**Validates Root Spec:** Requirement 10.4

### Requirement 5: GraphQL Query Operations

**User Story:** As a developer, I want multiple query patterns for finding code, so that I can locate symbols by ARN, name, file, or package.

#### Acceptance Criteria

5.1 THE GraphQL schema SHALL support `node(arn: ID!)` query for direct ARN lookup, returning the node or null if not found.

5.2 THE GraphQL schema SHALL support `searchNodes(name: String!, kind: SymbolKind, package: String, limit: Int)` query for name-based search with optional filtering.

5.3 THE GraphQL schema SHALL support `findReferences(arn: ID!)` query to find all nodes that reference the specified symbol.

5.4 THE GraphQL schema SHALL support `symbolsInFile(path: String!, package: String!)` query to list all symbols defined in a file.

5.5 THE GraphQL schema SHALL support `symbolsInPackage(package: String!, kind: SymbolKind)` query to list all symbols in a package with optional kind filtering.

5.6 THE GraphQL schema SHALL support `traverse(startArn: ID!, edgeTypes: [EdgeType!]!, depth: Int)` query for relationship traversal up to the specified depth (default: 1).

**Validates Root Spec:** Requirements 10.5, 10.6, 10.7, 10.8, 10.9, 10.10

### Requirement 6: GraphQL Mutation Operations

**User Story:** As a sync service, I want to bulk upsert nodes and edges from SCIP parse results, so that the graph stays synchronized with code changes.

#### Acceptance Criteria

6.1 THE GraphQL schema SHALL support `syncFromScip` mutation accepting:
  - `packagePath` (String!): Path to the package being synced
  - `symbols` ([SymbolInput!]!): Array of symbol definitions
  - `relationships` ([RelationshipInput!]!): Array of relationships
  - `indexHash` (String!): SCIP index hash for change detection

6.2 THE `syncFromScip` mutation SHALL perform bulk upsert of nodes (insert or update on ARN conflict).

6.3 THE `syncFromScip` mutation SHALL perform bulk upsert of edges (insert or update on unique constraint conflict).

6.4 THE `syncFromScip` mutation SHALL return `SyncResult` with: nodesCreated, nodesUpdated, edgesCreated, edgesRemoved.

6.5 THE GraphQL schema SHALL support `prunePackage(package: String!, keepArns: [ID!]!)` mutation to remove stale nodes not in the keepArns list.

6.6 THE `prunePackage` mutation SHALL return the count of nodes removed.

**Validates Root Spec:** Requirements 10.11, 10.12

### Requirement 7: Input Types

**User Story:** As a sync service, I want well-defined input types for mutations, so that data integrity is enforced at the API level.

#### Acceptance Criteria

7.1 THE GraphQL schema SHALL define `SymbolInput` input type with fields:
  - `arn` (ID!): Archon Resource Name
  - `type` (NodeType!): Resource type
  - `workspace` (String!): Workspace identifier
  - `package` (String!): Package name
  - `path` (String!): File path
  - `symbol` (String): Symbol name
  - `kind` (SymbolKind!): Symbol kind
  - `name` (String!): Human-readable name
  - `signature` (String): Function signature
  - `documentation` (String): Documentation string
  - `filePath` (String): Absolute file path
  - `lineNumber` (Int): Line number

7.2 THE GraphQL schema SHALL define `RelationshipInput` input type with fields:
  - `fromArn` (ID!): Source node ARN
  - `toArn` (ID!): Target node ARN
  - `type` (EdgeType!): Relationship type

7.3 THE GraphQL schema SHALL define `SyncResult` type with fields:
  - `nodesCreated` (Int!): Count of new nodes inserted
  - `nodesUpdated` (Int!): Count of existing nodes updated
  - `edgesCreated` (Int!): Count of new edges inserted
  - `edgesRemoved` (Int!): Count of stale edges removed

**Validates Root Spec:** Requirements 10.11, 10.12

## Correctness Properties

*Properties define characteristics that should hold true across all valid executions. These bridge human-readable specifications and machine-verifiable correctness guarantees.*

### Property 21: Graph Node ARN Uniqueness

*For any* Code Graph, each node SHALL have a unique ARN as its primary key, and no two nodes SHALL share the same ARN.

**Validates:** Requirement 1.2 (arn as PRIMARY KEY)

### Property 22: Graph Edge Referential Integrity

*For any* edge in the Code Graph, both the `from_arn` and `to_arn` SHALL reference existing nodes, and deleting a node SHALL cascade delete its edges.

**Validates:** Requirements 2.3, 2.5

### Property 23: GraphQL Query Completeness

*For any* valid ARN in the Code Graph, querying by ARN SHALL return the node with all its properties and relationships.

**Validates:** Requirements 4.1, 4.2, 5.1

### Property 24: Graph Traversal Correctness

*For any* traversal query starting from a valid ARN, the result SHALL include all nodes reachable via the specified edge types up to the specified depth.

**Validates:** Requirement 5.6

## Error Handling

### Database Errors

| Error Condition | Response | Recovery |
|-----------------|----------|----------|
| Duplicate ARN on insert | Return conflict error | Use upsert instead |
| Foreign key violation on edge | Return referential integrity error | Ensure nodes exist first |
| Connection failure | Return service unavailable | Retry with backoff |

### GraphQL Errors

| Error Condition | Response | Recovery |
|-----------------|----------|----------|
| Invalid ARN format | Return validation error | Correct ARN format |
| Node not found | Return null (queries) or error (mutations) | Verify ARN exists |
| Invalid enum value | Return validation error | Use valid enum value |
| Depth exceeds limit | Return validation error | Reduce depth parameter |

## Implementation Notes

- Use PostgreSQL's `ON CONFLICT` clause for efficient upserts
- Consider using database transactions for `syncFromScip` to ensure atomicity
- Implement GraphQL resolvers using DataLoader pattern to avoid N+1 queries
- Use prepared statements for frequently executed queries
- Consider connection pooling for high-throughput scenarios
