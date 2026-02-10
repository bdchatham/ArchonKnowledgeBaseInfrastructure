-- Migration: 001_code_graph_tables.sql
-- Description: Create code_graph_nodes table for GraphQL Code Graph Storage
-- Requirements: 1.1, 1.2, 1.3, 1.4 from code-graph-storage spec
--
-- This migration creates the nodes table for the Code Graph.
-- The edges table and indexes will be added in subsequent tasks (1.2, 1.3).

-- =============================================================================
-- code_graph_nodes table
-- =============================================================================
-- Nodes represent code symbols, files, packages, and documentation.
-- Each node is uniquely identified by its ARN (Archon Resource Name).
--
-- ARN Format: arn:archon:<type>:<workspace>/<package>/<path>#<symbol>
-- Example: arn:archon:code:personal-work/ArchonKnowledgeBaseInfrastructure/src/graph/schema.py#GraphQLService

CREATE TABLE IF NOT EXISTS code_graph_nodes (
    -- Primary key: Archon Resource Name uniquely identifying the node
    arn TEXT PRIMARY KEY,
    
    -- Resource type classification
    -- Constrained to: code, doc, k8s, infra
    type TEXT NOT NULL CHECK (type IN ('code', 'doc', 'k8s', 'infra')),
    
    -- Location identifiers
    workspace TEXT NOT NULL,      -- Workspace identifier (e.g., 'personal-work')
    package TEXT NOT NULL,        -- Package/repository name
    path TEXT NOT NULL,           -- File path relative to package
    
    -- Symbol information (nullable for file/package-level nodes)
    symbol TEXT,                  -- Symbol name within the file
    
    -- Symbol kind classification (nullable for non-code nodes)
    -- Constrained to: function, class, method, variable, type, module, file, package
    kind TEXT CHECK (kind IN ('function', 'class', 'method', 'variable', 'type', 'module', 'file', 'package')),
    
    -- Human-readable name (required)
    name TEXT NOT NULL,
    
    -- Optional metadata
    signature TEXT,               -- Function/method signature
    documentation TEXT,           -- Documentation string
    file_path TEXT,               -- Absolute file path for resolution
    line_number INTEGER,          -- Line number in source file
    
    -- Timestamps for tracking
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW(),
    
    -- SCIP index hash for change detection during sync
    index_hash TEXT
);

-- Add comment to table for documentation
COMMENT ON TABLE code_graph_nodes IS 'Stores code graph nodes representing code symbols, files, packages, and documentation';
COMMENT ON COLUMN code_graph_nodes.arn IS 'Archon Resource Name - unique identifier in format arn:archon:<type>:<workspace>/<package>/<path>#<symbol>';
COMMENT ON COLUMN code_graph_nodes.type IS 'Resource type: code, doc, k8s, or infra';
COMMENT ON COLUMN code_graph_nodes.kind IS 'Symbol kind: function, class, method, variable, type, module, file, or package';
COMMENT ON COLUMN code_graph_nodes.index_hash IS 'SHA-256 hash of SCIP index content for change detection';

-- =============================================================================
-- code_graph_edges table
-- =============================================================================
-- Edges represent directed relationships between nodes in the Code Graph.
-- Each edge connects a source node (from_arn) to a target node (to_arn).
--
-- Relationship types:
--   - contains: Parent contains child (e.g., class contains method)
--   - references: Source references target (e.g., function calls another)
--   - implements: Source implements target interface/type
--   - extends: Source extends target class/type
--   - imports: Source imports target module
--   - documents: Documentation node documents code node
--
-- Requirements: 2.1, 2.2, 2.3, 2.4, 2.5 from code-graph-storage spec

CREATE TABLE IF NOT EXISTS code_graph_edges (
    -- Auto-incrementing edge identifier
    id SERIAL PRIMARY KEY,
    
    -- Source node ARN (foreign key with cascade delete)
    -- When the source node is deleted, this edge is automatically removed
    from_arn TEXT NOT NULL REFERENCES code_graph_nodes(arn) ON DELETE CASCADE,
    
    -- Target node ARN (foreign key with cascade delete)
    -- When the target node is deleted, this edge is automatically removed
    to_arn TEXT NOT NULL REFERENCES code_graph_nodes(arn) ON DELETE CASCADE,
    
    -- Relationship type classification
    -- Constrained to: contains, references, implements, extends, imports, documents
    type TEXT NOT NULL CHECK (type IN ('contains', 'references', 'implements', 'extends', 'imports', 'documents')),
    
    -- Timestamp for tracking when edge was created
    created_at TIMESTAMP DEFAULT NOW(),
    
    -- Ensure no duplicate edges with same source, target, and type
    -- This allows multiple relationship types between the same pair of nodes
    UNIQUE (from_arn, to_arn, type)
);

-- Add comments to table for documentation
COMMENT ON TABLE code_graph_edges IS 'Stores directed relationships between code graph nodes';
COMMENT ON COLUMN code_graph_edges.from_arn IS 'Source node ARN - references code_graph_nodes(arn)';
COMMENT ON COLUMN code_graph_edges.to_arn IS 'Target node ARN - references code_graph_nodes(arn)';
COMMENT ON COLUMN code_graph_edges.type IS 'Relationship type: contains, references, implements, extends, imports, or documents';


-- =============================================================================
-- Database Indexes
-- =============================================================================
-- Indexes for efficient traversal and common query patterns.
-- These indexes support the GraphQL query operations defined in the spec.
--
-- Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6 from code-graph-storage spec

-- -----------------------------------------------------------------------------
-- Edge Indexes
-- -----------------------------------------------------------------------------
-- Index for outgoing edge traversal (e.g., finding what a node contains/references)
-- Supports: contains, references, implements, extends, imports relationship queries
CREATE INDEX IF NOT EXISTS idx_edges_from ON code_graph_edges(from_arn);

-- Index for incoming edge traversal (e.g., finding what references/contains a node)
-- Supports: containedBy, referencedBy, implementedBy, extendedBy, importedBy queries
CREATE INDEX IF NOT EXISTS idx_edges_to ON code_graph_edges(to_arn);

-- Index for edge type filtering (e.g., finding all 'references' edges)
-- Supports: filtering traversals by relationship type
CREATE INDEX IF NOT EXISTS idx_edges_type ON code_graph_edges(type);

-- -----------------------------------------------------------------------------
-- Node Indexes
-- -----------------------------------------------------------------------------
-- Index for package-scoped queries (e.g., symbolsInPackage query)
-- Supports: finding all symbols within a specific package/repository
CREATE INDEX IF NOT EXISTS idx_nodes_package ON code_graph_nodes(package);

-- Index for symbol kind filtering (e.g., finding all functions or classes)
-- Supports: searchNodes and symbolsInPackage queries with kind filter
CREATE INDEX IF NOT EXISTS idx_nodes_kind ON code_graph_nodes(kind);

-- Index for file-based lookups (e.g., symbolsInFile query)
-- Supports: finding all symbols defined in a specific file path
CREATE INDEX IF NOT EXISTS idx_nodes_path ON code_graph_nodes(path);
