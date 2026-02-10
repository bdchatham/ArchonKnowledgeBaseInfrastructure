# Design Document: GraphQL Code Graph Storage

## Overview

The GraphQL Code Graph Storage component provides a queryable representation of code structure and relationships for the Archon Knowledge Base. It stores code symbols (functions, classes, methods, etc.) and their relationships (contains, references, implements, etc.) in PostgreSQL, exposing them through a GraphQL API.

**This is a child spec** implementing Requirement 10 (GraphQL Code Graph Storage) from the root Archon Agent Pipeline specification.

### Parent Specification Reference

- **Root Spec Location:** `.kiro/specs/archon-agent-pipeline/`
- **Root Spec Repository:** Workspace root (personal-work)
- **Related Requirements:** Requirement 10 (GraphQL Code Graph Storage)
- **Related Correctness Properties:** Properties 21-24

### Design Principles

1. **ARN-Centric**: All nodes use ARN (Archon Resource Name) as primary key for deterministic identification
2. **Referential Integrity**: Edges enforce foreign key constraints with cascade delete
3. **Efficient Traversal**: Indexes optimized for common graph traversal patterns
4. **GraphQL-First**: All access through GraphQL schema for type safety and introspection
5. **Sync-Friendly**: Bulk upsert operations for efficient synchronization from SCIP indexes


## Architecture

### System Context

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                     ArchonKnowledgeBaseInfrastructure                        │
│                                                                              │
│  ┌──────────────────────────────────────────────────────────────────────┐  │
│  │                        Code Graph Module                              │  │
│  │                                                                       │  │
│  │  ┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐  │  │
│  │  │  GraphQL API    │───▶│   Resolvers     │───▶│   PostgreSQL    │  │  │
│  │  │  (Schema)       │    │   (Python)      │    │   (Storage)     │  │  │
│  │  └─────────────────┘    └─────────────────┘    └─────────────────┘  │  │
│  │                                                                       │  │
│  └──────────────────────────────────────────────────────────────────────┘  │
│                                    ▲                                        │
│                                    │                                        │
│  ┌─────────────────────────────────┼────────────────────────────────────┐  │
│  │                    Sync Service │                                     │  │
│  │  ┌─────────────────┐           │                                     │  │
│  │  │ Graph Sync      │───────────┘                                     │  │
│  │  │ Adapter         │                                                 │  │
│  │  │ - syncFromScip  │                                                 │  │
│  │  │ - prunePackage  │                                                 │  │
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
│  │ (archon.graph)  │    │ (sync trigger)  │    │ (queries)       │         │
│  └─────────────────┘    └─────────────────┘    └─────────────────┘         │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Package Structure

```
ArchonKnowledgeBaseInfrastructure/
├── src/
│   ├── graph/                      # New module for Code Graph
│   │   ├── __init__.py
│   │   ├── schema.py               # GraphQL schema definition
│   │   ├── resolvers.py            # GraphQL resolvers
│   │   ├── models.py               # SQLAlchemy models
│   │   └── service.py              # Graph service layer
│   ├── sync/
│   │   └── graph_adapter.py        # Sync adapter for graph operations
│   └── common/
│       └── database.py             # Database connection utilities
├── migrations/                     # New directory for DB migrations
│   └── 001_code_graph_tables.sql   # Initial schema migration
└── .kiro/
    └── specs/
        └── code-graph-storage/     # This spec
```


## Component Interfaces

### GraphQL Service

The main entry point for graph operations, exposing the GraphQL API.

```python
from dataclasses import dataclass
from typing import Optional
from graphql import GraphQLSchema

@dataclass
class GraphQLServiceConfig:
    """Configuration for the GraphQL service."""
    database_url: str
    max_traversal_depth: int = 5
    default_search_limit: int = 20
    max_search_limit: int = 100

class GraphQLService:
    """Service for executing GraphQL queries against the Code Graph."""
    
    def __init__(self, config: GraphQLServiceConfig) -> None:
        """Initialize the GraphQL service with configuration."""
        ...
    
    async def execute(
        self,
        query: str,
        variables: Optional[dict] = None,
    ) -> dict:
        """Execute a GraphQL query and return the result.
        
        Args:
            query: GraphQL query string
            variables: Optional query variables
            
        Returns:
            GraphQL execution result with 'data' and optional 'errors' keys
        """
        ...
    
    def get_schema(self) -> GraphQLSchema:
        """Return the GraphQL schema for introspection."""
        ...
```

### Graph Repository

Data access layer for graph nodes and edges.

```python
from dataclasses import dataclass
from typing import Optional
from enum import Enum

class NodeType(Enum):
    CODE = "code"
    DOC = "doc"
    K8S = "k8s"
    INFRA = "infra"

class SymbolKind(Enum):
    FUNCTION = "function"
    CLASS = "class"
    METHOD = "method"
    VARIABLE = "variable"
    TYPE = "type"
    MODULE = "module"
    FILE = "file"
    PACKAGE = "package"

class EdgeType(Enum):
    CONTAINS = "contains"
    REFERENCES = "references"
    IMPLEMENTS = "implements"
    EXTENDS = "extends"
    IMPORTS = "imports"
    DOCUMENTS = "documents"

@dataclass
class GraphNode:
    """Represents a node in the Code Graph."""
    arn: str
    type: NodeType
    workspace: str
    package: str
    path: str
    name: str
    symbol: Optional[str] = None
    kind: Optional[SymbolKind] = None
    signature: Optional[str] = None
    documentation: Optional[str] = None
    file_path: Optional[str] = None
    line_number: Optional[int] = None
    index_hash: Optional[str] = None

@dataclass
class GraphEdge:
    """Represents an edge in the Code Graph."""
    from_arn: str
    to_arn: str
    type: EdgeType

class GraphRepository:
    """Repository for Code Graph data access."""
    
    async def get_node(self, arn: str) -> Optional[GraphNode]:
        """Get a node by ARN."""
        ...
    
    async def search_nodes(
        self,
        name: str,
        kind: Optional[SymbolKind] = None,
        package: Optional[str] = None,
        limit: int = 20,
    ) -> list[GraphNode]:
        """Search nodes by name with optional filters."""
        ...
    
    async def get_symbols_in_file(
        self,
        path: str,
        package: str,
    ) -> list[GraphNode]:
        """Get all symbols defined in a file."""
        ...
    
    async def get_symbols_in_package(
        self,
        package: str,
        kind: Optional[SymbolKind] = None,
    ) -> list[GraphNode]:
        """Get all symbols in a package."""
        ...
    
    async def get_edges_from(
        self,
        arn: str,
        edge_type: Optional[EdgeType] = None,
    ) -> list[GraphEdge]:
        """Get outgoing edges from a node."""
        ...
    
    async def get_edges_to(
        self,
        arn: str,
        edge_type: Optional[EdgeType] = None,
    ) -> list[GraphEdge]:
        """Get incoming edges to a node."""
        ...
    
    async def traverse(
        self,
        start_arn: str,
        edge_types: list[EdgeType],
        depth: int = 1,
    ) -> list[GraphNode]:
        """Traverse the graph from a starting node."""
        ...
    
    async def upsert_nodes(self, nodes: list[GraphNode]) -> tuple[int, int]:
        """Bulk upsert nodes. Returns (created_count, updated_count)."""
        ...
    
    async def upsert_edges(self, edges: list[GraphEdge]) -> int:
        """Bulk upsert edges. Returns created_count."""
        ...
    
    async def prune_package(
        self,
        package: str,
        keep_arns: list[str],
    ) -> int:
        """Remove nodes not in keep_arns list. Returns removed_count."""
        ...
```


### Sync Result Types

```python
@dataclass
class SyncResult:
    """Result of a sync operation."""
    nodes_created: int
    nodes_updated: int
    edges_created: int
    edges_removed: int
```

## Data Models

### PostgreSQL Schema

The Code Graph uses two tables: `code_graph_nodes` for vertices and `code_graph_edges` for relationships.

```sql
-- Nodes represent code symbols, files, packages, and documentation
CREATE TABLE code_graph_nodes (
    arn TEXT PRIMARY KEY,
    type TEXT NOT NULL CHECK (type IN ('code', 'doc', 'k8s', 'infra')),
    workspace TEXT NOT NULL,
    package TEXT NOT NULL,
    path TEXT NOT NULL,
    symbol TEXT,
    kind TEXT CHECK (kind IN ('function', 'class', 'method', 'variable', 'type', 'module', 'file', 'package')),
    name TEXT NOT NULL,
    signature TEXT,
    documentation TEXT,
    file_path TEXT,
    line_number INTEGER,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW(),
    index_hash TEXT  -- SCIP index hash for change detection
);

-- Edges represent relationships between nodes
CREATE TABLE code_graph_edges (
    id SERIAL PRIMARY KEY,
    from_arn TEXT NOT NULL REFERENCES code_graph_nodes(arn) ON DELETE CASCADE,
    to_arn TEXT NOT NULL REFERENCES code_graph_nodes(arn) ON DELETE CASCADE,
    type TEXT NOT NULL CHECK (type IN ('contains', 'references', 'implements', 'extends', 'imports', 'documents')),
    created_at TIMESTAMP DEFAULT NOW(),
    UNIQUE (from_arn, to_arn, type)
);

-- Indexes for efficient traversal
CREATE INDEX idx_edges_from ON code_graph_edges(from_arn);
CREATE INDEX idx_edges_to ON code_graph_edges(to_arn);
CREATE INDEX idx_edges_type ON code_graph_edges(type);
CREATE INDEX idx_nodes_package ON code_graph_nodes(package);
CREATE INDEX idx_nodes_kind ON code_graph_nodes(kind);
CREATE INDEX idx_nodes_path ON code_graph_nodes(path);
```

**Schema Notes:**
- `arn` is the primary key, ensuring uniqueness (Property 21)
- Foreign keys with `ON DELETE CASCADE` ensure referential integrity (Property 22)
- Unique constraint on `(from_arn, to_arn, type)` prevents duplicate edges
- Indexes support common query patterns: package-scoped queries, kind filtering, file lookups, and edge traversal


### GraphQL Schema

```graphql
type Node {
  arn: ID!
  type: NodeType!
  workspace: String!
  package: String!
  path: String!
  symbol: String
  kind: SymbolKind
  name: String!
  signature: String
  documentation: String
  filePath: String
  lineNumber: Int
  
  # Relationship traversal
  contains: [Node!]!
  containedBy: Node
  references: [Node!]!
  referencedBy: [Node!]!
  implements: [Node!]!
  implementedBy: [Node!]!
  extends: [Node!]!
  extendedBy: [Node!]!
  imports: [Node!]!
  importedBy: [Node!]!
  documentationNode: Node  # Link to .archon.md doc node
}

enum NodeType {
  CODE
  DOC
  K8S
  INFRA
}

enum SymbolKind {
  FUNCTION
  CLASS
  METHOD
  VARIABLE
  TYPE
  MODULE
  FILE
  PACKAGE
}

enum EdgeType {
  CONTAINS
  REFERENCES
  IMPLEMENTS
  EXTENDS
  IMPORTS
  DOCUMENTS
}

type Query {
  # Lookup by ARN
  node(arn: ID!): Node
  
  # Search by name pattern
  searchNodes(
    name: String!
    kind: SymbolKind
    package: String
    limit: Int = 20
  ): [Node!]!
  
  # Find all references to a symbol
  findReferences(arn: ID!): [Node!]!
  
  # Find all symbols in a file
  symbolsInFile(path: String!, package: String!): [Node!]!
  
  # Find all symbols in a package
  symbolsInPackage(package: String!, kind: SymbolKind): [Node!]!
  
  # Traverse relationships
  traverse(
    startArn: ID!
    edgeTypes: [EdgeType!]!
    depth: Int = 1
  ): [Node!]!
}

type Mutation {
  # Sync from SCIP parse result (called by sync service)
  syncFromScip(
    packagePath: String!
    symbols: [SymbolInput!]!
    relationships: [RelationshipInput!]!
    indexHash: String!
  ): SyncResult!
  
  # Remove stale nodes for a package
  prunePackage(package: String!, keepArns: [ID!]!): Int!
}

input SymbolInput {
  arn: ID!
  type: NodeType!
  workspace: String!
  package: String!
  path: String!
  symbol: String
  kind: SymbolKind!
  name: String!
  signature: String
  documentation: String
  filePath: String
  lineNumber: Int
}

input RelationshipInput {
  fromArn: ID!
  toArn: ID!
  type: EdgeType!
}

type SyncResult {
  nodesCreated: Int!
  nodesUpdated: Int!
  edgesCreated: Int!
  edgesRemoved: Int!
}
```


### ARN Format Reference

ARNs (Archon Resource Names) follow this format:

```
arn:archon:<type>:<workspace>/<package>/<path>#<symbol>
```

| Component | Description | Example |
|-----------|-------------|---------|
| `type` | Resource type | `code`, `doc`, `k8s`, `infra` |
| `workspace` | Workspace identifier | `personal-work` |
| `package` | Package/repository name | `ArchonKnowledgeBaseInfrastructure` |
| `path` | File path relative to package | `src/graph/schema.py` |
| `symbol` | Symbol name (optional) | `GraphQLService.execute` |

**Examples:**
```
arn:archon:code:personal-work/ArchonKnowledgeBaseInfrastructure/src/graph/schema.py#GraphQLService
arn:archon:doc:personal-work/ArchonAgent/src/orchestrator/main.py
arn:archon:code:personal-work/AphexCLI/cmd/root.go#Execute
```

## Correctness Properties

*Properties define characteristics that should hold true across all valid executions. These bridge human-readable specifications and machine-verifiable correctness guarantees.*

### Property 21: Graph Node ARN Uniqueness

*For any* Code Graph, each node SHALL have a unique ARN as its primary key, and no two nodes SHALL share the same ARN.

**Validates:** Requirement 1.2 (arn as PRIMARY KEY)

**Test Strategy:**
- Generate random sets of nodes with potentially duplicate ARNs
- Verify that inserting duplicate ARNs either fails or updates existing node
- Verify that querying by ARN returns exactly one node

### Property 22: Graph Edge Referential Integrity

*For any* edge in the Code Graph, both the `from_arn` and `to_arn` SHALL reference existing nodes, and deleting a node SHALL cascade delete its edges.

**Validates:** Requirements 2.3, 2.5

**Test Strategy:**
- Attempt to create edges with non-existent node ARNs (should fail)
- Create nodes and edges, then delete a node
- Verify all edges referencing the deleted node are also deleted

### Property 23: GraphQL Query Completeness

*For any* valid ARN in the Code Graph, querying by ARN SHALL return the node with all its properties and relationships.

**Validates:** Requirements 4.1, 4.2, 5.1

**Test Strategy:**
- Insert nodes with all properties populated
- Query via GraphQL `node(arn: ...)` query
- Verify all properties are returned correctly
- Verify relationship fields return connected nodes

### Property 24: Graph Traversal Correctness

*For any* traversal query starting from a valid ARN, the result SHALL include all nodes reachable via the specified edge types up to the specified depth.

**Validates:** Requirement 5.6

**Test Strategy:**
- Create a graph with known structure (chain, tree, cycle)
- Execute traversal queries with various edge types and depths
- Verify results match expected reachable nodes
- Test edge cases: depth=0, unreachable nodes, cycles


## Error Handling

### Database Errors

| Error Condition | Response | Recovery |
|-----------------|----------|----------|
| Duplicate ARN on insert | Return conflict error with existing ARN | Use upsert operation instead |
| Foreign key violation on edge | Return referential integrity error | Ensure both nodes exist before creating edge |
| Connection failure | Return service unavailable (503) | Retry with exponential backoff |
| Query timeout | Return timeout error (504) | Reduce query complexity or depth |

### GraphQL Errors

| Error Condition | Response | Recovery |
|-----------------|----------|----------|
| Invalid ARN format | Return validation error with format hint | Correct ARN format |
| Node not found | Return `null` for queries, error for mutations | Verify ARN exists |
| Invalid enum value | Return validation error listing valid values | Use valid enum value |
| Depth exceeds limit | Return validation error with max depth | Reduce depth parameter |
| Invalid query syntax | Return GraphQL parse error | Fix query syntax |

### Sync Errors

| Error Condition | Response | Recovery |
|-----------------|----------|----------|
| Partial sync failure | Return partial result with error details | Retry failed items |
| Invalid symbol data | Skip invalid symbol, log warning | Fix source data |
| Transaction rollback | Return error, no changes persisted | Retry entire sync |

## Testing Strategy

### Property-Based Testing

Use `hypothesis` (Python) for property-based tests:

- **Library**: hypothesis
- **Minimum iterations**: 100 per property test
- **Tag format**: `Feature: code-graph-storage, Property N: <property_text>`

### Test Categories

#### Database Layer Tests

**Property Tests:**
- Property 21: ARN uniqueness enforcement
- Property 22: Referential integrity and cascade delete

**Unit Tests:**
- CRUD operations for nodes and edges
- Index usage verification
- Constraint violation handling

#### GraphQL Layer Tests

**Property Tests:**
- Property 23: Query completeness
- Property 24: Traversal correctness

**Unit Tests:**
- Schema validation
- Resolver error handling
- Input validation

#### Integration Tests

- End-to-end sync flow
- GraphQL query execution
- Concurrent access handling

### Test Data Generation

For property-based tests, generate:
- Random valid ARN components (type, workspace, package, path, symbol)
- Random graph structures (chains, trees, DAGs, cycles)
- Random node properties (all fields populated, minimal fields)
- Random edge configurations (single type, mixed types)

## Implementation Notes

### Database Considerations

- Use PostgreSQL's `ON CONFLICT` clause for efficient upserts:
  ```sql
  INSERT INTO code_graph_nodes (arn, type, ...) 
  VALUES ($1, $2, ...)
  ON CONFLICT (arn) DO UPDATE SET type = $2, ...
  ```
- Use database transactions for `syncFromScip` to ensure atomicity
- Consider connection pooling (e.g., `asyncpg` pool) for high-throughput scenarios

### GraphQL Implementation

- Use `graphql-core` or `strawberry-graphql` for Python GraphQL implementation
- Implement DataLoader pattern to avoid N+1 queries on relationship fields
- Use prepared statements for frequently executed queries

### Performance Considerations

- Limit maximum traversal depth (default: 5) to prevent expensive queries
- Limit search results (default: 20, max: 100) to bound response size
- Use database indexes for all common query patterns
- Consider caching for frequently accessed nodes

